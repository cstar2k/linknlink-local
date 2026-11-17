"""IR learning receiver with a bounded HAIR-compatible capture window."""

import asyncio
import logging
from datetime import timedelta

from linknlink.exceptions import LinknLinkException, ReadError, StorageError

from homeassistant.components.infrared import (
    InfraredReceivedSignal,
    InfraredReceiverEntity,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import LinknLinkEntity

_LOGGER = logging.getLogger(__name__)
CAPTURE_SECONDS = 30


def decode_capture(data: bytes) -> list[int]:
    """Read hardware timings, including packets whose length includes the trailer."""
    if len(data) < 6 or data[0] != 0x26:
        raise ValueError("Expected a LinknLink IR packet")
    end = 4 + int.from_bytes(data[2:4], "little")
    if end > len(data):
        raise ValueError("Truncated IR packet")
    payload = data[4:end]
    if payload.endswith(b"\x00") and data[end:end + 2] == b"\x0d\x05":
        # Hardware can count the escape byte but exclude the final gap value.
        payload += b"\x0d\x05"
    elif not payload.endswith(b"\x00\x0d\x05") and data[end:end + 2] != b"\x0d\x05":
        raise ValueError("IR packet has no terminator")
    timings = []
    index = 0
    while index < len(payload):
        ticks = payload[index]
        index += 1
        if ticks == 0:
            if index + 2 > len(payload):
                raise ValueError("Truncated IR duration")
            ticks = int.from_bytes(payload[index:index + 2], "big")
            index += 2
        if ticks == 0:
            raise ValueError("Zero IR duration")
        duration = round(ticks * 269 / 8192 * 1000)
        timings.append(duration if len(timings) % 2 == 0 else -duration)
    if len(timings) < 4:
        raise ValueError("IR signal is too short")
    return timings


class LinknLinkInfraredReceiver(LinknLinkEntity, InfraredReceiverEntity):
    """Expose hardware learning through the native infrared receiver API."""

    _attr_name = "IR Receiver"
    _attr_should_poll = False

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.api.mac.hex()}-infrared-receiver"
        self._task = None
        self._window_ends_at = None

    @property
    def extra_state_attributes(self):
        registry = er.async_get(self.hass)
        switch = registry.async_get_entity_id(
            "switch", DOMAIN, f"{self.coordinator.api.mac.hex()}-ir-capture"
        )
        return {
            "capturing": self._task is not None,
            "capture_switch": switch,
            "capture_mode": "single",
            "window_ends_at": self._window_ends_at,
        }

    @callback
    def _write_capture_state(self):
        self.async_write_ha_state()
        self.coordinator.async_update_listeners()

    async def async_start_capture(self):
        if self._task is not None:
            return
        lock = self.coordinator.learning_lock
        if lock.locked():
            raise HomeAssistantError("This eRemote already has a learning session")
        await lock.acquire()
        try:
            await self.coordinator.async_request(self.api.enter_learning)
        except BaseException:
            lock.release()
            raise
        self._window_ends_at = (
            dt_util.utcnow() + timedelta(seconds=CAPTURE_SECONDS)
        ).isoformat()
        self._task = self.hass.async_create_background_task(
            self._async_capture(), f"LinknLink IR capture {self.entity_id}"
        )
        self._write_capture_state()

    async def _async_capture(self):
        try:
            async with asyncio.timeout(CAPTURE_SECONDS):
                while True:
                    await asyncio.sleep(1)
                    try:
                        packet = await self.coordinator.async_request(self.api.check_data)
                    except (ReadError, StorageError):
                        continue
                    timings = decode_capture(packet)
                    self._handle_received_signal(InfraredReceivedSignal(timings))
                    return
        except TimeoutError:
            _LOGGER.debug("IR capture timed out for %s", self.entity_id)
        except (LinknLinkException, OSError, ValueError):
            _LOGGER.exception("IR capture failed for %s", self.entity_id)
        finally:
            self._task = None
            self._window_ends_at = None
            self.coordinator.learning_lock.release()
            self._write_capture_state()

    async def async_stop_capture(self):
        if self._task is not None:
            task = self._task
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def async_will_remove_from_hass(self):
        await self.async_stop_capture()
        await super().async_will_remove_from_hass()
