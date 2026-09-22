"""Authenticated HA image entity for the most recent successful ring photo."""
from homeassistant.components.image import ImageEntity

from .entity import WelcomeEyeEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([WelcomeEyeRingImage(hass, entry.runtime_data)])


class WelcomeEyeRingImage(WelcomeEyeEntity, ImageEntity):
    _attr_name = 'Last ring'
    _attr_content_type = 'image/jpeg'
    _attr_icon = 'mdi:doorbell-video'

    def __init__(self, hass, hub):
        ImageEntity.__init__(self, hass)
        WelcomeEyeEntity.__init__(self, hub, 'last_ring')

    @property
    def available(self):
        return not self.hub.stopped

    @property
    def image_last_updated(self):
        return self.hub.ring_image.updated

    async def async_image(self):
        return self.hub.ring_image.jpeg

    @property
    def extra_state_attributes(self):
        capture = self.hub.ring_image
        return {
            'source': capture.source,
            'captured_at': capture.updated.isoformat() if capture.updated else None,
            # This sequence belongs to the cached image, not a failed new ring.
            'ring_sequence': capture.image_sequence,
            'capture_status': capture.status,
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.hub.ring_image.entity_id = self.entity_id

    async def async_will_remove_from_hass(self):
        await self.hub.ring_image.close()
        await super().async_will_remove_from_hass()
