"""Explicit single-shot Door Connect opening on the already shared QV session.

Only the Preview live-transparent path is implemented; no CGI fallback or retry.
APK references and the distinction between confirmation and physical observation
are documented in docs/connect3-control-evidence.md.
"""
import asyncio
from dataclasses import dataclass
import struct
import time
from types import MappingProxyType

from .cgi import encode_auth_code
from . import protocol as qv
from ..snapshot import _finish_task

UNLOCK_ORDER = 4
UNLOCK_TIMEOUT = 15.0  # PreviewPresenter.startUnlock, classes4 0x19f828.
OUTPUT_TARGETS = MappingProxyType({
    'strike_1': (1, 1), 'gate_1': (1, 2),
    'strike_2': (2, 1), 'gate_2': (2, 2),
})
SECONDARY_OUTPUT_OPTIONS = MappingProxyType({
    'strike_2': 'channel2_strike_trial_enabled',
    'gate_2': 'channel2_gate_trial_enabled',
})
_OUTPUT_CONFIGURATION_KEYS = ('host', 'cgi_port', 'media_port', 'media_transport',
    'certificate_sha256', 'media_certificate_sha256', 'auth_code', 'opening_code',
    'credential_source', 'credential_device_uid')


class OutputFailure(RuntimeError):
    """A fixed local reason and uncertainty flag, never a network/secret string."""

    def __init__(self, reason, uncertain=False):
        super().__init__(reason)
        self.reason = reason
        self.physical_request_uncertain = bool(uncertain)


@dataclass(frozen=True)
class UnlockResponse:
    result: int

    @property
    def accepted(self):
        return self.result == 0


def build_unlock_request(material, *, channel, output, opening_code,
                         timestamp_seconds):
    """Encode native output 1/2 and channel; the owner supplies a separate code.

    SendUnlockData(classes4 0x21bbc8): output@0, channel@2, true@3,
    code@16. OnSendData(ARM64 0x4a9838): FE, order@13, payload@32.
    """
    if type(output) is not int or output not in (1, 2):
        raise OutputFailure('invalid_output')
    if type(channel) is not int or not 1 <= channel <= 255:
        raise OutputFailure('invalid_output_channel')
    if (not isinstance(opening_code, str) or not opening_code
            or len(opening_code) > 256):
        raise OutputFailure('opening_code_required')
    try:
        # The APK calls the same EncodeDevicePassword transform, but the
        # value is Device.unlockPassword/user input, never inferred authCode.
        encoded = encode_auth_code(opening_code).encode('utf-8')
    except (ValueError, UnicodeError):
        raise OutputFailure('invalid_opening_code') from None
    if len(encoded) > qv.MAX_PARAMETERS - 16 or any(c < 32 or c == 127 for c in encoded):
        raise OutputFailure('invalid_opening_code')
    qv._bounded_number(timestamp_seconds, 0xFFFFFFFFFFFFFFFF, 'invalid_timestamp')
    parameters = bytes((output, 0, channel, 1)) + bytes(12) + encoded
    header = bytearray(qv.HEADER_SIZE)
    header[0], header[13] = 0xFE, UNLOCK_ORDER
    struct.pack_into('<Q', header, 1, timestamp_seconds)
    struct.pack_into('<H', header, 11, len(parameters))
    return qv._encode_command(header, parameters, material)


def parse_unlock_response(packet):
    """Return only the native order-4 result from an already checked packet.

    QvCamera.callback 0x44c490..0x44c520: order@13 and parameters@32;
    ReceiveUnlockData(classes4 0x21bec4): minimum2, byte0==0 =>0;
    otherwise byte1==2 =>-10029, else -1. No correlation ID is available.
    """
    if (not isinstance(packet, qv.ControlPacket)
            or packet.header.command != 0xFE
            or packet.header.plaintext[13] != UNLOCK_ORDER):
        return None
    if len(packet.parameters) < 2:
        raise OutputFailure('invalid_output_response', True)
    result = 0 if packet.parameters[0] == 0 else (-10029 if packet.parameters[1] == 2 else -1)
    return UnlockResponse(result)


class Connect3OutputController:
    """One explicit HA action, one media consumer, at most one physical write."""

    def __init__(self, hub):
        self.hub = hub
        self.closed = False
        self._busy = False
        self._task = None
        self._close_task = None
        self._active_target = self._active_session = self._active_media_hub = None
        self._action_configuration = None
        self._family_label = ('r002' if getattr(getattr(hub, 'capabilities', None), 'r002_qv_read', False)
                              else 'connect3')
        self._observation = dict(command_count=0, request_send_attempt_count=0,
            target_channel=1,
            request_sent_count=0, response_count=0, last_output=None,
            last_result=None, last_error_type=None, last_error_stage=None,
            physical_request_uncertain=False, physical_activation_verified=False,
            native_control_path=f'{self._family_label}_live_transparent_order_4')
        self._targets = {target: dict(command_count=0, request_send_attempt_count=0,
            request_sent_count=0, response_count=0, last_result=None,
            last_error_type=None, last_error_reason=None, last_error_stage=None,
            physical_request_uncertain=False, physical_activation_verified=False,
            native_ack_received=False, native_ack_accepted=False)
            for target in OUTPUT_TARGETS}

    def diagnostics(self):
        return {**self._observation, 'closed': self.closed, 'busy': self._busy,
            'targets': {target: {**obs, **self.target_status(target)}
                        for target, obs in self._targets.items()}}

    def target_status(self, target):
        """Configuration policy only; no network, credentials or inferred mapping."""
        pair = OUTPUT_TARGETS.get(target) if isinstance(target, str) else None
        if pair is None:
            return {'enabled': False, 'reason': 'invalid_output_target',
                    'channel': None, 'output': None}
        channel, output = pair
        data = self.hub.entry.data
        reason = None
        if getattr(self.hub, 'channel', 1) != 1:
            reason = 'output_channel_not_supported'
        elif not (data.get('experimental_outputs') is True
                and data.get('experimental_video') is True
                and isinstance(data.get('opening_code'), str) and data['opening_code']):
            reason = 'output_not_enabled'
        elif (self._family_label == 'connect3' and data.get('media_transport', 'tls') == 'connect3_tcp'
                and (data.get('experimental_tcp_controls') is not True
                     or (channel == 2 and data.get('media_tcp_approved') is not True)
                     or getattr(getattr(self.hub, 'capabilities', None),
                                'strike' if output == 1 else 'gate', False) is not True)):
            reason = 'connect3_tcp_outputs_disabled'
        elif channel == 2:
            from ..capabilities import DeviceVariant
            if (getattr(self.hub, 'variant', None) not in (DeviceVariant.CONNECT3, DeviceVariant.R002)
                    or not getattr(self.hub, 'secondary_channel_enabled', False)
                    or getattr(self.hub, 'channel2', None) is None):
                reason = 'output_channel_not_supported'
            elif data.get(SECONDARY_OUTPUT_OPTIONS[target]) is not True:
                reason = 'channel2_output_trial_disabled'
        return {'enabled': reason is None, 'reason': reason, 'channel': channel, 'output': output}

    def target_enabled(self, target):
        return self.target_status(target)['enabled']

    def owns_media_acquisition(self, live):
        """Only the action's own task can reserve its selected media context."""
        return (self._busy and self._task is asyncio.current_task()
                and getattr(self._active_media_hub, 'live', None) is live)

    def authorize_output(self, session, channel, output):
        """Private channel-2 write grant, bound to this action and exact session."""
        return (channel == 2 and self._busy and not self.closed and not self.hub.stopped
                and getattr(self.hub, '_tls_blocked_reason', None) is None
                and self._active_media_hub is not None and not self._active_media_hub.stopped
                and self._active_media_hub.live.connected
                and self._task is asyncio.current_task()
                and self._active_session is session
                and getattr(getattr(self._active_media_hub, 'live', None), 'session', None) is session
                and self._action_configuration == tuple(
                    self.hub.entry.data.get(key) for key in _OUTPUT_CONFIGURATION_KEYS)
                and OUTPUT_TARGETS.get(self._active_target) == (channel, output)
                and self.target_enabled(self._active_target))

    async def unlock(self, output):
        """Preserve the existing 0/1 API, always targeting the first channel."""
        if getattr(self.hub, 'channel', 1) != 1:
            raise OutputFailure('output_channel_not_supported')
        if type(output) is not int or output not in (0, 1):
            raise OutputFailure('invalid_output')
        await self.unlock_target('strike_1' if output == 0 else 'gate_1')

    async def unlock_target(self, target):
        route = self.target_status(target)
        if not route['enabled']:
            raise OutputFailure(route['reason'])
        if self._busy:
            raise OutputFailure('output_busy')
        if self.closed or self.hub.stopped:
            raise OutputFailure('output_closed')
        data = self.hub.entry.data
        channel, output = OUTPUT_TARGETS[target]
        media_hub = self.hub if channel == 1 else self.hub.channel2
        self._busy = True
        self._task = asyncio.current_task()
        self._active_target, self._active_media_hub = target, media_hub
        self._action_configuration = tuple(data.get(key) for key in _OUTPUT_CONFIGURATION_KEYS)
        obs = self._observation
        target_obs = self._targets[target]
        for current in (obs, target_obs):
            current['command_count'] += 1
            current.update(last_output=output - 1, target_channel=channel,
                last_target=target, last_result=None, last_error_type=None,
                last_error_reason=None, last_error_stage=None, physical_request_uncertain=False,
                native_ack_received=False, native_ack_accepted=False)
        owner = object()
        acquired = False
        session = None
        before_attempts = before_sent = before_responses = 0
        stage = f'acquiring_{self._family_label}_media'
        started = time.monotonic()
        try:
            await media_hub.acquire(owner)
            acquired = True
            if self.closed or self.hub.stopped:
                raise OutputFailure('output_closed')
            if not self.target_enabled(target):
                raise OutputFailure('output_authorization_changed')
            session = media_hub.live.session
            if session is None:
                raise OutputFailure('output_session_unavailable')
            self._active_session = session
            counters = session.output_diagnostics()
            before_attempts = counters['request_send_attempt_count']
            before_sent = counters['request_sent_count']
            before_responses = counters['response_count']
            stage = f'{self._family_label}_live_output'
            response = await session.execute_output(output, data['opening_code'], channel=channel)
            for current in (obs, target_obs):
                current.update(last_result=response.result, native_ack_received=True,
                    native_ack_accepted=response.accepted)
            if not response.accepted:
                raise OutputFailure('device_rejected')
            return response
        except BaseException as exc:
            for current in (obs, target_obs):
                current['last_error_type'] = type(exc).__name__
                current['last_error_stage'] = stage
                current['last_error_reason'] = (exc.reason if isinstance(exc, OutputFailure)
                    else 'other_channel_busy' if getattr(exc, 'reason', None) == 'other_channel_busy' else None)
                current['physical_request_uncertain'] = bool(getattr(exc, 'physical_request_uncertain', False))
            raise
        finally:
            if session is not None:
                counters = session.output_diagnostics()
                for current in (obs, target_obs):
                    current['request_send_attempt_count'] += counters['request_send_attempt_count'] - before_attempts
                    current['request_sent_count'] += counters['request_sent_count'] - before_sent
                    current['response_count'] += counters['response_count'] - before_responses
                    current['physical_request_uncertain'] = counters['physical_request_uncertain']
            try:
                if acquired:
                    await _finish_task(asyncio.create_task(media_hub.release(owner,
                        reason='output_completed')), cancel_on_cancel=False)
            finally:
                for current in (obs, target_obs):
                    current['elapsed_ms'] = round((time.monotonic() - started) * 1000)
                self._task = None
                self._busy = False
                self._active_target = self._active_session = self._active_media_hub = None
                self._action_configuration = None

    async def close(self):
        self.closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(asyncio.current_task()))
        await _finish_task(self._close_task, cancel_on_cancel=False)

    async def _close(self, caller):
        task = self._task
        if task is not None and task is not caller and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
