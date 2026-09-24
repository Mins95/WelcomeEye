"""Authenticated images backed only by the most recent successful capture."""
from homeassistant.components.image import ImageEntity

from .entity import WelcomeEyeEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([WelcomeEyeRingImage(hass, entry.runtime_data),
                        WelcomeEyeSnapshotImage(hass, entry.runtime_data)])


class WelcomeEyeCaptureImage(WelcomeEyeEntity, ImageEntity):
    _attr_content_type = 'image/jpeg'

    def __init__(self, hass, hub, key, capture):
        ImageEntity.__init__(self, hass)
        WelcomeEyeEntity.__init__(self, hub, key)
        self.capture = capture

    @property
    def available(self):
        return not self.hub.stopped

    @property
    def image_last_updated(self):
        return self.capture.updated

    async def async_image(self):
        return self.capture.jpeg

    @property
    def extra_state_attributes(self):
        capture = self.capture
        return {
            'source': capture.source,
            'captured_at': capture.updated.isoformat() if capture.updated else None,
            'capture_status': capture.status,
            'media_content_id': capture.media_content_id,
            'filename': capture.filename,
            'save_error': capture.diagnostics['last_save_error_type'],
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.capture.entity_id = self.entity_id
        self.hub._notify()

    async def async_will_remove_from_hass(self):
        self.capture.entity_id = None
        # Hub owns the backend, which also serves services and the switch.
        self.hub._notify()
        await super().async_will_remove_from_hass()


class WelcomeEyeRingImage(WelcomeEyeCaptureImage):
    _attr_translation_key = 'last_ring'
    _attr_icon = 'mdi:doorbell-video'

    def __init__(self, hass, hub):
        super().__init__(hass, hub, 'last_ring', hub.ring_image)

    @property
    def extra_state_attributes(self):
        return {**super().extra_state_attributes, 'ring_sequence': self.capture.image_sequence}


class WelcomeEyeSnapshotImage(WelcomeEyeCaptureImage):
    _attr_translation_key = 'last_snapshot'
    _attr_icon = 'mdi:camera'

    def __init__(self, hass, hub):
        super().__init__(hass, hub, 'last_snapshot', hub.manual_snapshot)
