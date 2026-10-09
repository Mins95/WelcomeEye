"""Second LT media source using the primary hub's sole media worker."""
import asyncio
from copy import deepcopy
from hashlib import sha256
from ipaddress import ip_address
import json

from .capabilities import DeviceCapabilities, DeviceVariant
from .snapshot import _finish_task


class ChannelBusyError(ConnectionError):
    reason = 'other_channel_busy'


def profile_binding(data):
    """Private observation belongs to this unicast LT endpoint/profile only."""
    try:
        host = str(ip_address(data['host']))
    except (KeyError, TypeError, ValueError):
        return None
    profile = {'host': host, 'protocol': 'legacy_owsp',
        'main_channel': data.get('channel', 16), 'secondary_channel': 17,
        'stream': 1, 'mode': 2}
    return sha256(json.dumps(profile, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def observed_channels(data):
    record = data.get('observed_legacy_media_channels')
    binding = profile_binding(data)
    if (binding is None or type(record) is not dict or set(record) != {'binding', 'channels'}
            or record.get('binding') != binding or type(record.get('channels')) is not list
            or not 1 <= len(record['channels']) <= 2
            or any(type(channel) is not int or channel not in (1, 2) for channel in record['channels'])):
        return frozenset()
    return frozenset(record['channels'])


class _Lease:
    # Channel 2 has one proven profile. Never cycle profiles or retry a failed
    # acquisition merely because it belongs to an explicit viewer.
    single_session_attempt = True


def secondary_microphone_enabled(parent):
    return (parent.variant in (DeviceVariant.V1, DeviceVariant.R001)
        and parent.entry.data.get('second_channel_enabled') is True
        and parent.entry.data.get('experimental_channel2_microphone') is True)


def secondary_microphone_context(parent, session):
    """The decoded LT channel and its exclusive worker must still be current."""
    secondary = parent.channel2
    return bool(secondary is not None and secondary_microphone_enabled(parent)
        and not parent.stopped and not secondary._closing
        and parent._active_media_channel == 2 and parent._media_connected
        and parent.session is session and not session.closed.is_set()
        and session.channel == 17 and secondary._leases
        and all(lease in parent.consumers for lease in tuple(secondary._leases.values()))
        and not parent.stop_event.is_set()
        and parent.current_profile == {'name': 'lt_second_panel', 'channel': 17,
                                       'stream': 1, 'mode': 2}
        and secondary.observation['decoded_video_frames'] > 0
        and secondary.observation['selected_channel'] == 2
        and parent._media_profile_binding == profile_binding(parent.entry.data))


class _SecondaryTalkback:
    """Route through the parent's sole LT talk controller and media reader."""

    def __init__(self, parent):
        self.parent = parent

    def __getattr__(self, name):
        return getattr(self.parent.talkback, name)

    @property
    def active(self):
        return self.parent.talkback.channel == 2 and self.parent.talkback.active

    @property
    def owner(self):
        return self.parent.talkback.owner if self.parent.talkback.channel == 2 else None

    @property
    def diagnostics(self):
        if self.parent.talkback.diagnostics['requested_channel'] == 2:
            return self.parent.talkback.diagnostics
        return {'state': 'off', 'requested_channel': 2, 'wire_video_channel': 17,
            'wire_audio_channel': 18, 'route_experimental': True,
            'physically_verified': False, 'frames_sent': 0, 'bytes_sent': 0}

    async def start(self, owner):
        await self.parent.talkback.start(owner, channel=2)


class LegacyChannel2:
    channel = 2
    ring_image_capture_entity_id = None
    url = None

    def __init__(self, parent):
        self.parent = parent
        self.hass, self.entry = parent.hass, parent.entry
        self.frame_listeners, self.close_listeners, self.listeners = set(), set(), set()
        self.webrtc_diagnostics = {}
        self._leases = {}
        self._closing = False
        self.image = self.format = None
        self.talkback = _SecondaryTalkback(parent)
        from .legacy_snapshot import LegacySnapshotCapture
        self.manual_snapshot = LegacySnapshotCapture(self)
        self._close_task = None
        self.observation = {'requested_channel': 2, 'wire_channel': 17,
            'selected_channel': None, 'physical_channel_verified': False,
            'decoded_video_frames': 0, 'decoded_audio_frames': 0,
            'last_error_type': None, 'close_reason': None}

    @property
    def capabilities(self):
        return DeviceCapabilities(camera=True, live_media=True, downstream_audio=True,
            talkback=secondary_microphone_enabled(self.parent),
            manual_snapshot=True, last_snapshot=True)

    @property
    def stopped(self):
        return self.parent.stopped or self._closing

    @property
    def connected(self):
        return self.parent._active_media_channel == 2 and self.parent._media_connected

    @property
    def consumers(self):
        return set(self._leases)

    @property
    def variant(self):
        return self.parent.variant

    @property
    def protocol_family(self):
        return self.parent.protocol_family

    @property
    def device_model(self):
        return self.parent.device_model

    async def acquire(self, owner):
        if self.stopped:
            raise ConnectionError('Second channel unavailable')
        lease = self._leases.setdefault(owner, _Lease())
        try:
            return await self.parent.acquire(lease, _channel=2)
        except BaseException:
            if self._leases.get(owner) is lease:
                self._leases.pop(owner, None)
            raise

    async def release(self, owner, *, reason='viewer_closed'):
        lease = self._leases.pop(owner, None)
        if lease is not None:
            await self.parent.release(lease, reason=reason)
        if not self._leases:
            self.observation['close_reason'] = reason

    async def stop(self, *, reason='integration_unload'):
        if self._close_task is None or self._close_task.done():
            self._closing = True
            self._close_task = asyncio.create_task(self._stop(reason))
        await _finish_task(self._close_task, cancel_on_cancel=False)

    async def _stop(self, reason):
        try:
            for owner in tuple(self._leases):
                await self.release(owner, reason=reason)
            if self.close_listeners:
                results = await asyncio.gather(*(close() for close in tuple(self.close_listeners)),
                    return_exceptions=True)
                errors = [type(result).__name__ for result in results if isinstance(result, Exception)]
                if errors:
                    self.webrtc_diagnostics['cleanup_error_type'] = errors[0]
        finally:
            self.observation['close_reason'] = reason
            self._closing = False
            self._notify()

    def subscribe(self, callback):
        self.listeners.add(callback)
        return lambda: self.listeners.discard(callback)

    def _notify(self):
        for callback in tuple(self.listeners):
            callback()

    def diagnostics(self):
        targets = {target: self.parent.control.target_status(target)
                   for target in ('strike_2', 'gate_2')}
        controls = any(status['enabled'] for status in targets.values())
        return {**deepcopy(self.observation), 'active': bool(self._leases),
            'active_consumers': len(self._leases), 'downstream_audio': True,
            'microphone_supported': self.capabilities.talkback, 'controls_supported': controls,
            'microphone_unavailable_reason': None if self.capabilities.talkback else 'channel2_microphone_trial_disabled',
            'microphone': deepcopy(self.talkback.diagnostics),
            'manual_snapshot': deepcopy(self.manual_snapshot.diagnostics),
            'controls_unavailable_reason': None if controls else 'channel2_output_trial_disabled',
            'output_targets': targets,
            'webrtc': {key: deepcopy(self.webrtc_diagnostics[key]) for key in (
                'stage', 'failed_at_stage', 'last_exception_type', 'active_viewers',
                'requested_tracks', 'created_tracks', 'downstream_frames_queued',
                'connection_state', 'ice_connection_state', 'negotiation_ok',
                'cleanup_stage', 'cleanup_failed_stage', 'cleanup_error_type')
                if key in self.webrtc_diagnostics}}
