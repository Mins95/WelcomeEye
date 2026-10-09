"""Explicit channel-2 video/audio on the configured primary endpoint.

Channel 2 is the existing QV PLAY selector, not a claim about which outdoor
panel a firmware maps to it. Viewing never triggers an output or microphone.
"""
import asyncio
from copy import deepcopy

from ..capabilities import DeviceCapabilities
from ..snapshot import _finish_task
from .live import LiveMedia
from .talk import Talkback
from .talk_route import secondary_talk_enabled

WEBRTC_FIELDS = ('stage', 'failed_at_stage', 'last_exception_type', 'active_viewers',
    'requested_tracks', 'created_tracks', 'downstream_frames_queued',
    'connection_state', 'ice_connection_state', 'negotiation_ok',
    'cleanup_stage', 'cleanup_failed_stage', 'cleanup_error_type')


class Channel2Media:
    """Camera/RTC facade sharing private configuration but never media listeners."""

    channel = 2
    ring_image_capture_entity_id = None

    @property
    def capabilities(self):
        return DeviceCapabilities(camera=True, live_media=True, downstream_audio=True,
            talkback=secondary_talk_enabled(self), manual_snapshot=True, last_snapshot=True)

    def __init__(self, parent):
        self.parent = parent
        self.hass, self.entry = parent.hass, parent.entry
        self.variant, self.protocol_family = parent.variant, parent.protocol_family
        self.device_model = parent.device_model
        self.listeners, self.frame_listeners, self.close_listeners = set(), set(), set()
        self.webrtc_diagnostics = {}
        self.live = LiveMedia(self, channel=2, controls_enabled=False)
        from .snapshot import LiveSnapshotCapture
        self.manual_snapshot = LiveSnapshotCapture(self)
        self.talkback = Talkback(self)
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
    def doorbell(self):
        # One observer, explicitly bound to its selected existing session.
        return self.parent.doorbell

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
        self._last_error_reason = self._last_close_reason = None

    def _release_media(self, live):
        self.parent._release_media(live)

    def record_channel_observed(self, channel, observation, binding):
        self.parent.record_channel_observed(channel, observation, binding)

    def _begin_close(self, reason):
        if self._close_task is None or self._close_task.done():
            self._closing = True
            self._close_task = asyncio.create_task(self._close(reason),
                name='welcomeeye-channel2-close')
            self._close_task.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None)
        return self._close_task

    async def acquire(self, owner):
        if self.stopped:
            raise RuntimeError('Connect 3 channel unavailable')
        try:
            self.check_tls_trust()
            await self.live.acquire(owner)
        except Exception:
            # Fixed diagnostic only; no network exception/string is exported.
            self._last_error_reason = ('other_channel_busy'
                if self.parent._media_claim not in (None, self.live)
                else 'channel_acquisition_failed')
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
        outputs = {target: self.parent.control.target_status(target)
                   for target in ('strike_2', 'gate_2')}
        return {'enabled': True, 'requested_channel': 2, 'requested_stream': 1,
            'selected_channel': self.live.observation.get('selected_channel'),
            'physical_channel_verified': False,
            'downstream_audio': True, 'microphone_supported': self.capabilities.talkback,
            'controls_supported': any(item['enabled'] for item in outputs.values()),
            'microphone_unavailable_reason': None if self.capabilities.talkback else 'channel_route_unverified',
            'microphone_route_experimental': True,
            'microphone': self.talkback.diagnostics,
            'snapshot': deepcopy(self.manual_snapshot.diagnostics),
            'output_targets': outputs,
            'active': bool(self.consumers),
            'closing': self._closing, 'close_reason': self._last_close_reason,
            'last_error_reason': self._last_error_reason,
            'media': {**deepcopy(self.live.observation),
                'active_consumers': len(self.consumers),
                'session_attempts': self.live.session_count,
                'worker_active': self.live.task is not None and not self.live.task.done()},
            'webrtc': {key: deepcopy(self.webrtc_diagnostics[key]) for key in WEBRTC_FIELDS
                if key in self.webrtc_diagnostics}}
