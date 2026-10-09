from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.helpers.typing import UNDEFINED

from .entity import WelcomeEyeEntity
from .rtc import WebRTCManager
from .snapshot import capture_fresh_image


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.runtime_data.capabilities.camera:
        caps = entry.runtime_data.capabilities
        camera = WelcomeEyeConnect3Camera if (caps.connect3_read or caps.r002_qv_read) else WelcomeEyeCamera
        entities = [camera(entry.runtime_data)]
        if (getattr(entry.runtime_data, 'supports_multichannel_player', False)
                and (channel2 := getattr(entry.runtime_data, 'channel2', None)) is not None):
            entities.append(WelcomeEyeConnect3Channel2Camera(channel2))
        async_add_entities(entities)


class WelcomeEyeCamera(WelcomeEyeEntity, Camera):
    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_brand = 'Philips'
    _attr_use_stream_for_stills = False
    _rtc_class = WebRTCManager
    _welcomeeye_channel = 1

    def __init__(self, hub):
        Camera.__init__(self)
        # Beta 9 deliberately lets Home Assistant consume stream_source() through
        # its Stream/HLS path. Real enterprise-Wi-Fi testing showed that the same
        # media succeeds over HTTP while native WebRTC remains stuck in ICE
        # checking when UDP traversal is blocked and no TURN relay is available.
        # Keep the WebRTC implementation intact below so it can be re-enabled once
        # an automatic transport-selection path is proven without regressing HLS.
        self._supports_native_async_webrtc = False
        WelcomeEyeEntity.__init__(self, hub, 'camera')
        self.rtc = self._rtc_class(hub)

    @property
    def available(self):
        return not self.hub.stopped

    @property
    def extra_state_attributes(self):
        attributes = {
            'welcomeeye_player': True,
            'welcomeeye_capabilities': self.hub.capabilities.as_dict(),
            'ring_image_capture_entity_id': self.hub.ring_image_capture_entity_id,
        }
        parent = getattr(self.hub, 'parent', self.hub)
        if getattr(parent, 'supports_multichannel_player', False):
            attributes.update(welcomeeye_channel=self._welcomeeye_channel,
                welcomeeye_multichannel_available=getattr(parent, 'channel2', None) is not None)
        return attributes

    @property
    def frame_interval(self):
        return 0.5

    async def async_camera_image(self, width=None, height=None):
        if not self.available:
            return None
        return await capture_fresh_image(self.hub)

    async def async_capture_snapshot(self, save_to_media=True):
        """Explicit user capture, separate from the last visitor image."""
        if not self.hub.capabilities.manual_snapshot:
            from homeassistant.exceptions import HomeAssistantError
            raise HomeAssistantError('Snapshot unavailable for this device')
        return await self.hub.manual_snapshot.capture(save_to_media=save_to_media)

    async def stream_source(self):
        return self.hub.url if self.available else None

    async def async_handle_async_webrtc_offer(self, offer_sdp, session_id, send_message):
        await self.rtc.offer(offer_sdp, session_id, send_message)

    async def async_on_webrtc_candidate(self, session_id, candidate):
        await self.rtc.candidate(session_id, candidate)

    def close_webrtc_session(self, session_id):
        self.rtc.schedule_close(session_id)

    async def async_will_remove_from_hass(self):
        try:
            await self.rtc.close_all()
        finally:
            self.hub.frame_listeners.discard(self.rtc._frame)
            self.hub.close_listeners.discard(self.rtc.close_all)
            await super().async_will_remove_from_hass()


class WelcomeEyeConnect3Camera(WelcomeEyeCamera):
    """A QV video source. Still-image polling never opens a session."""

    _welcomeeye_channel = 1

    def __init__(self, hub):
        super().__init__(hub)
        self._supports_native_async_webrtc = True

    @property
    def extra_state_attributes(self):
        return {**super().extra_state_attributes,
            'welcomeeye_snapshot_requires_live': self.hub.capabilities.manual_snapshot}

    async def async_camera_image(self, width=None, height=None):
        return self.hub.image if self.available else None

    async def stream_source(self):
        # Connect 3 is decoded into the existing WebRTC tracks; no legacy URL.
        return None


class WelcomeEyeConnect3Channel2Camera(WelcomeEyeConnect3Camera):
    """The second outdoor source on the same configured QV device."""

    _attr_name = UNDEFINED
    # Keep the translation key and registry unique ID from beta.8.
    _attr_translation_key = 'channel_2_trial'
    _attr_icon = 'mdi:camera-switch'
    _welcomeeye_channel = 2

    def __init__(self, hub):
        super().__init__(hub)
        self._attr_unique_id = f'{hub.entry.unique_id}_camera_channel_2'
