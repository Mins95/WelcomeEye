"""Explicit single-shot Door Connect opening on the already shared QV session.

Only the Preview live-transparent path is implemented; no CGI fallback or retry.
APK references and the distinction between confirmation and physical observation
are documented in docs/connect3-control-evidence.md.
"""
import asyncio
from dataclasses import dataclass
import struct
import time

from .cgi import encode_auth_code
from . import protocol as qv
from ..snapshot import _finish_task

UNLOCK_ORDER = 4
UNLOCK_TIMEOUT = 15.0  # PreviewPresenter.startUnlock, classes4 0x19f828.


class OutputFailure(RuntimeError):
    """A fixed local reason and uncertainty flag, never a network/secret string."""

    def __init__(self, reason, uncertain=False):
        super().__init__(reason)
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
        self._family_label = ('r002' if getattr(getattr(hub, 'capabilities', None), 'r002_qv_read', False)
                              else 'connect3')
        self._observation = dict(command_count=0, request_send_attempt_count=0,
            request_sent_count=0, response_count=0, last_output=None,
            last_result=None, last_error_type=None, last_error_stage=None,
            physical_request_uncertain=False, physical_activation_verified=False,
            native_control_path=f'{self._family_label}_live_transparent_order_4')

    def diagnostics(self):
        return {**self._observation, 'closed': self.closed, 'busy': self._busy}

    async def unlock(self, output):
        if type(output) is not int or output not in (0, 1):
            raise OutputFailure('invalid_output')
        if self._busy:
            raise OutputFailure('output_busy')
        if self.closed or self.hub.stopped:
            raise OutputFailure('output_closed')
        data = self.hub.entry.data
        if not (data.get('experimental_outputs', False)
                and data.get('experimental_video', False)
                and data.get('opening_code')):
            raise OutputFailure('output_not_enabled')
        self._busy = True
        self._task = asyncio.current_task()
        obs = self._observation
        obs['command_count'] += 1
        obs.update(last_output=output, last_result=None, last_error_type=None,
                   last_error_stage=None, physical_request_uncertain=False)
        owner = object()
        acquired = False
        session = None
        before_attempts = before_sent = before_responses = 0
        stage = f'acquiring_{self._family_label}_media'
        started = time.monotonic()
        try:
            await self.hub.acquire(owner)
            acquired = True
            if self.closed or self.hub.stopped:
                raise OutputFailure('output_closed')
            session = self.hub.live.session
            if session is None:
                raise OutputFailure('output_session_unavailable')
            counters = session.output_diagnostics()
            before_attempts = counters['request_send_attempt_count']
            before_sent = counters['request_sent_count']
            before_responses = counters['response_count']
            stage = f'{self._family_label}_live_output'
            response = await session.execute_output(output + 1, data['opening_code'], channel=1)
            obs['last_result'] = response.result
            if not response.accepted:
                raise OutputFailure('device_rejected')
        except BaseException as exc:
            obs['last_error_type'] = type(exc).__name__
            obs['last_error_stage'] = stage
            obs['physical_request_uncertain'] = bool(getattr(exc, 'physical_request_uncertain', False))
            raise
        finally:
            if session is not None:
                counters = session.output_diagnostics()
                obs['request_send_attempt_count'] += counters['request_send_attempt_count'] - before_attempts
                obs['request_sent_count'] += counters['request_sent_count'] - before_sent
                obs['response_count'] += counters['response_count'] - before_responses
                obs['physical_request_uncertain'] = counters['physical_request_uncertain']
            try:
                if acquired:
                    await _finish_task(asyncio.create_task(self.hub.release(owner,
                        reason='output_completed')), cancel_on_cancel=False)
            finally:
                obs['elapsed_ms'] = round((time.monotonic() - started) * 1000)
                self._task = None
                self._busy = False

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
