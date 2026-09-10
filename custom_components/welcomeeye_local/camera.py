from homeassistant.components.camera import Camera, CameraEntityFeature

from .entity import WelcomeEyeEntity
from .rtc import WebRTCManager


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([WelcomeEyeCamera(entry.runtime_data)])


class WelcomeEyeCamera(WelcomeEyeEntity, Camera):
    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_brand = 'Philips'
    _attr_model = 'WelcomeEye Connect 2'
    _attr_use_stream_for_stills = False

    def __init__(self, hub):
        Camera.__init__(self)
        WelcomeEyeEntity.__init__(self, hub, 'camera')
        self.rtc = WebRTCManager(hub)

    @property
    def available(self):
        return not self.hub.stopped

    @property
    def frame_interval(self):
        return 0.5

    async def async_camera_image(self, width=None, height=None):
        return self.hub.image if self.available else None

    async def stream_source(self):
        return self.hub.url if self.available else None

    async def async_handle_async_webrtc_offer(self, offer_sdp, session_id, send_message):
        await self.rtc.offer(offer_sdp, session_id, send_message)

    async def async_on_webrtc_candidate(self, session_id, candidate):
        await self.rtc.candidate(session_id, candidate)

    def close_webrtc_session(self, session_id):
        self.rtc.schedule_close(session_id)

    async def async_will_remove_from_hass(self):
        await self.rtc.close_all()
        self.hub.frame_listeners.discard(self.rtc._frame)
        self.hub.close_listeners.discard(self.rtc.close_all)
        await super().async_will_remove_from_hass()
