"""Explicit, bounded channel-2 video trial on the configured primary endpoint.

Channel 2 is the existing QV PLAY selector, not a claim about which outdoor
panel a firmware maps to it. No discovery, output or microphone command occurs.
"""
import asyncio
from copy import deepcopy

from ..capabilities import DeviceCapabilities
from ..snapshot import _finish_task
from .live import LiveMedia

TRIAL_TIMEOUT_SECONDS = 60.0
WEBRTC_FIELDS = ('stage', 'failed_at_stage', 'last_exception_type', 'active_viewers',
    'requested_tracks', 'created_tracks', 'downstream_frames_queued',
    'connection_state', 'ice_connection_state', 'negotiation_ok',
    'cleanup_stage', 'cleanup_failed_stage', 'cleanup_error_type')


class Channel2Trial:
    """Camera/RTC facade sharing private configuration but never media listeners."""

    capabilities = DeviceCapabilities(camera=True, live_media=True)
    ring_image_capture_entity_id = None

    def __init__(self, parent):
        self.parent = parent
        self.hass, self.entry = parent.hass, parent.entry
        self.variant, self.protocol_family = parent.variant, parent.protocol_family
        self.device_model = parent.device_model
        self.listeners, self.frame_listeners, self.close_listeners = set(), set(), set()
        self.webrtc_diagnostics = {}
        self.live = LiveMedia(self, channel=2, video_only=True)
        self._deadline = None
        self._close_task = None
        self._closing = False
        self._last_close_reason = None
        self._last_error_reason = None

    @property
    def stopped(self):
        return self.parent.stopped or self._closing

    @property
    def _task(self):
        return self.parent._task

    @property
    def _authentication(self):
        return self.parent._authentication

    @_authentication.setter
    def _authentication(self, value):
        self.parent._authentication = value

    @property
    def connected(self):
        return self.live.connected

    @property
    def image(self):
        return self.live.image

    @property
    def consumers(self):
        return self.live.consumers

    def check_tls_trust(self):
        self.parent.check_tls_trust()

    def report_tls_error(self, exc, *, endpoint):
        return self.parent.report_tls_error(exc, endpoint=endpoint)

    async def prepare_media_endpoint(self, observation):
        return await self.parent.prepare_media_endpoint(observation)

    def _claim_media(self, live):
        self.parent._claim_media(live)
        if self._deadline is None:
            self._last_error_reason = self._last_close_reason = None
            self._deadline = asyncio.get_running_loop().call_later(
                TRIAL_TIMEOUT_SECONDS, self._expire)

    def _release_media(self, live):
        self.parent._release_media(live)
        if not live.consumers:
            self._cancel_deadline()

    def _cancel_deadline(self):
        if self._deadline is not None:
            self._deadline.cancel()
            self._deadline = None

    def _expire(self):
        self._deadline = None
        self._begin_close('trial_timeout')

    def _begin_close(self, reason):
        if self._close_task is None or self._close_task.done():
            self._closing = True
            self._cancel_deadline()
            self._close_task = asyncio.create_task(self._close(reason),
                name='welcomeeye-channel2-close')
            self._close_task.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None)
        return self._close_task

    async def acquire(self, owner):
        if self.stopped:
            raise RuntimeError('Connect 3 channel trial unavailable')
        self.check_tls_trust()
        try:
            await self.live.acquire(owner)
        except Exception:
            # Fixed diagnostic only; no network exception/string is exported.
            self._last_error_reason = ('other_channel_busy'
                if self.parent._media_claim not in (None, self.live)
                else 'trial_acquisition_failed')
            raise

    async def release(self, owner, *, reason='viewer_closed'):
        await self.live.release(owner, reason=reason)
        if not self.consumers:
            self._last_close_reason = reason

    async def stop(self, *, reason='integration_unload'):
        await _finish_task(self._begin_close(reason), cancel_on_cancel=False)

    async def _close(self, reason):
        try:
            # Stop the owned media/acquisition first; closing an RTC offer may
            # then safely release that same lease without a circular await.
            await self.live.stop()
            if self.close_listeners:
                results = await asyncio.gather(
                    *(callback() for callback in tuple(self.close_listeners)),
                    return_exceptions=True)
                errors = [type(result).__name__ for result in results if isinstance(result, Exception)]
                if errors:
                    self.webrtc_diagnostics['cleanup_error_type'] = errors[0]
        finally:
            self._cancel_deadline()
            self._last_close_reason = reason
            if self.live.observation.get('stage') == 'closed':
                self.live.observation['close_reason'] = reason
            self._closing = False
            self._notify()

    def subscribe(self, listener):
        self.listeners.add(listener)
        return lambda: self.listeners.discard(listener)

    def _notify(self):
        if not self.parent.stopped:
            for callback in tuple(self.listeners):
                callback()
            self.parent._notify()

    def diagnostics(self):
        return {'enabled': True, 'requested_channel': 2, 'requested_stream': 1,
            'video_only': True, 'time_limit_seconds': TRIAL_TIMEOUT_SECONDS,
            'active': bool(self.consumers), 'deadline_armed': self._deadline is not None,
            'closing': self._closing, 'close_reason': self._last_close_reason,
            'last_error_reason': self._last_error_reason,
            'media': {**deepcopy(self.live.observation),
                'active_consumers': len(self.consumers),
                'session_attempts': self.live.session_count,
                'worker_active': self.live.task is not None and not self.live.task.done()},
            'webrtc': {key: deepcopy(self.webrtc_diagnostics[key]) for key in WEBRTC_FIELDS
                if key in self.webrtc_diagnostics}}
