"""Control the eRemote's bounded infrared learning window."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .entity import LinknLinkEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LinknLinkCaptureSwitch(coordinator)])


class LinknLinkCaptureSwitch(LinknLinkEntity, SwitchEntity):
    _attr_name = "IR Capture"
    _attr_icon = "mdi:remote"

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.api.mac.hex()}-ir-capture"

    @property
    def is_on(self):
        receiver = getattr(self.coordinator, "ir_receiver", None)
        return receiver is not None and receiver._task is not None

    async def async_turn_on(self, **kwargs):
        receiver = getattr(self.coordinator, "ir_receiver", None)
        if receiver is None:
            raise HomeAssistantError("IR receiver is not ready")
        await receiver.async_start_capture()

    async def async_turn_off(self, **kwargs):
        receiver = getattr(self.coordinator, "ir_receiver", None)
        if receiver is not None:
            await receiver.async_stop_capture()
