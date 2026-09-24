"""Persistent opt-in for visitor captures; independent from ring detection."""
from homeassistant.components.switch import SwitchEntity

from .entity import WelcomeEyeEntity
from .ring_image import OPTION_RING_IMAGE_CAPTURE


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([WelcomeEyeRingCaptureSwitch(entry.runtime_data)])


class WelcomeEyeRingCaptureSwitch(WelcomeEyeEntity, SwitchEntity):
    _attr_translation_key = 'ring_image_capture'
    _attr_icon = 'mdi:camera-timer'

    def __init__(self, hub):
        super().__init__(hub, 'ring_image_capture')

    @property
    def available(self):
        return not self.hub.stopped and self.hub.local_ring_supported

    @property
    def is_on(self):
        return self.hub.ring_image.enabled

    async def _set_enabled(self, enabled):
        self.hass.config_entries.async_update_entry(
            self.hub.entry, options={**self.hub.entry.options, OPTION_RING_IMAGE_CAPTURE: enabled},
        )
        self.hub.ring_image.set_enabled(enabled)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs):
        await self._set_enabled(False)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.hub.ring_image.set_enabled(self.hub.entry.options.get(OPTION_RING_IMAGE_CAPTURE) is True)
        self.hub.ring_image_capture_entity_id = self.entity_id
        self.hub._notify()

    async def async_will_remove_from_hass(self):
        self.hub.ring_image_capture_entity_id = None
        self.hub.ring_image.set_enabled(False)
        self.hub._notify()
        await super().async_will_remove_from_hass()
