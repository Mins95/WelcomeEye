"""Push-only doorbell listener using a control session without video."""
from collections import deque
from dataclasses import dataclass
import json
import logging
import select
import struct
import threading
import time

from .client import AuthenticationError, Session
from .protected import ProtocolError, decode_private_reply, owsp, parse_tlvs, tlv

_LOGGER = logging.getLogger(__name__)
_MEDIA_TYPES = {97, 98, 99, 100, 101, 203}
# Vendor APK constants identify 7 as DOOR_BELL, 19 as CALL and 47 as CALL_FROM.
# Type 14 remains enabled because it was observed on the validated Connect 2.
_CONFIRMED_RING_TYPES = {7, 14, 19, 47}
_CANDIDATE_RING_TYPES = set(_CONFIRMED_RING_TYPES)


@dataclass(frozen=True)
class RingMessage:
    """Only fields needed to distinguish repeated alarm deliveries."""

    channel: int
    timestamp: str
    timestamp_svr: int


@dataclass(frozen=True)
class AlarmObservation:
    """Privacy-safe alarm metadata used for compatibility diagnostics."""

    alarm_type: int
    message: RingMessage | None


def _safe_error_message(exc):
    if isinstance(exc, (ProtocolError, ValueError, RuntimeError, TimeoutError)):
        return str(exc)
    return None


def decode_alarm_observations(uid, kind, body):
    """Decode reportAlarm messages while retaining no raw manufacturer data."""
    if kind != 510:
        return [], {}
    _, inner = decode_private_reply(uid, body)
    if len(inner) < 8 or struct.unpack_from('>I', inner)[0] != len(inner) - 4:
        raise ProtocolError('Invalid private alarm envelope')

    observations = []
    inner_counts = {}
    for inner_kind, payload in parse_tlvs(inner[8:]):
        inner_counts[inner_kind] = inner_counts.get(inner_kind, 0) + 1
        if inner_kind != 14854 or len(payload) < 5:
            continue
        length = struct.unpack_from('<I', payload)[0]
        if length not in (len(payload) - 4, len(payload) - 5) or length <= 0:
            continue
        if payload[4 + length:].strip(b'\0'):
            continue
        data = json.loads(payload[4:4 + length].rstrip(b'\0'))
        if not isinstance(data, dict):
            continue
        if data.get('name') != 'reportAlarm' or data.get('mode') != 'set':
            continue
        param = data.get('param')
        if not isinstance(param, dict) or type(param.get('alarm_type')) is not int:
            continue
        alarm_type = param['alarm_type']
        message = None
        channel, stamp, server = (
            param.get('channel'), param.get('timestamp'), param.get('timestamp_svr')
        )
        if (
            type(channel) is int
            and 0 <= channel <= 255
            and isinstance(stamp, str)
            and len(stamp) == 14
            and stamp.isascii()
            and stamp.isdigit()
            and type(server) is int
            and 0 < server < 2**63
        ):
            message = RingMessage(channel, stamp, server)
        observations.append(AlarmObservation(alarm_type, message))
    return observations, inner_counts


def decode_rings(uid, kind, body):
    """Compatibility wrapper returning only validated ring messages."""
    try:
        observations, _ = decode_alarm_observations(uid, kind, body)
    except (ProtocolError, ValueError, TypeError, UnicodeError, struct.error):
        return []
    return [
        item.message for item in observations
        if item.alarm_type in _CONFIRMED_RING_TYPES and item.message is not None
    ]


class RingListener:
    """Own one control connection and deliver callbacks on the HA event loop."""

    def __init__(self, loop, entry, on_ring, on_state):
        self.loop, self.entry = loop, entry
        self.on_ring, self.on_state = on_ring, on_state
        self.closed = threading.Event()
        self.session = self.thread = None
        self.seen = deque(maxlen=256)
        self.connection_attempts = 0
        self.last_error_type = None
        self.last_error_message = None
        self.last_error_stage = None
        self.last_top_level_tlv = None
        self.tlv_counts = {}
        self.inner_tlv_counts = {}
        self.alarm_type_counts = {}
        self.decode_failures = 0

    @property
    def candidate_ring_types(self):
        return sorted(_CANDIDATE_RING_TYPES)

    def start(self):
        self.thread = threading.Thread(target=self._worker, name='welcomeeye-ring', daemon=True)
        self.thread.start()

    def close(self):
        self.closed.set()
        if self.session:
            self.session.close()

    def _deliver(self, callback, *args):
        if not self.closed.is_set():
            callback(*args)

    def _emit(self, callback, *args):
        self.loop.call_soon_threadsafe(self._deliver, callback, *args)

    def _accept(self, message):
        if message in self.seen:
            return
        self.seen.append(message)
        self._emit(self.on_ring, message)

    def _record_error(self, exc, stage):
        self.last_error_type = type(exc).__name__
        self.last_error_message = _safe_error_message(exc)
        self.last_error_stage = stage

    def _clear_error(self):
        self.last_error_type = None
        self.last_error_message = None
        self.last_error_stage = None

    def _record_alarm_parts(self, uid, kind, body):
        self.last_top_level_tlv = kind
        self.tlv_counts[kind] = self.tlv_counts.get(kind, 0) + 1
        if kind != 510:
            return
        try:
            observations, inner_counts = decode_alarm_observations(uid, kind, body)
        except (ProtocolError, ValueError, TypeError, UnicodeError, struct.error):
            self.decode_failures += 1
            return
        for inner_kind, count in inner_counts.items():
            self.inner_tlv_counts[inner_kind] = self.inner_tlv_counts.get(inner_kind, 0) + count
        for observation in observations:
            alarm_type = observation.alarm_type
            self.alarm_type_counts[alarm_type] = self.alarm_type_counts.get(alarm_type, 0) + 1
            if alarm_type in _CONFIRMED_RING_TYPES and observation.message is not None:
                self._accept(observation.message)

    def _worker(self):
        delay = 2
        while not self.closed.is_set():
            data = self.entry.data
            session = Session(
                data['host'], data['username'], data['password'],
                channel=0, stream=3, mode=0,
            )
            self.session = session
            self.connection_attempts += 1
            started = time.monotonic()
            stage = 'connecting'
            try:
                if self.closed.is_set():
                    return
                parts = session.connect()
                stage = 'identity_check'
                if session.info.uid != self.entry.unique_id:
                    self.last_error_type = 'IdentityMismatch'
                    self.last_error_message = 'Doorbell session device identity mismatch'
                    self.last_error_stage = stage
                    self._emit(self.on_state, False, 'IdentityMismatch')
                    return
                self._clear_error()
                self._emit(self.on_state, True, None)
                stage = 'listening'
                last_received = time.monotonic()
                while not self.closed.is_set():
                    for kind, body in parts:
                        # Some legacy WelcomeEye units emit media/format TLVs even on
                        # the control listener. Record and ignore them instead of
                        # tearing down the doorbell session.
                        if kind in _MEDIA_TYPES:
                            self.last_top_level_tlv = kind
                            self.tlv_counts[kind] = self.tlv_counts.get(kind, 0) + 1
                            continue
                        self._record_alarm_parts(session.info.uid, kind, body)
                    if self.closed.is_set():
                        break
                    now = time.monotonic()
                    if now - last_received >= 35:
                        raise TimeoutError('Doorbell keepalive response missing')
                    if now - session.last_keepalive >= 10:
                        session.sock.sendall(owsp(tlv(49, bytes(4))))
                        session.last_keepalive = now
                    wait = max(0, 10 - (time.monotonic() - session.last_keepalive))
                    readable, _, _ = select.select([session.sock], [], [], wait)
                    parts = session.read() if readable and not self.closed.is_set() else []
                    if readable:
                        last_received = time.monotonic()
            except AuthenticationError as exc:
                self._record_error(exc, stage)
                self._emit(self.on_state, False, 'AuthenticationError')
                return
            except Exception as exc:
                if not self.closed.is_set():
                    self._record_error(exc, stage)
                    self._emit(self.on_state, False, type(exc).__name__)
                    message = self.last_error_message
                    if message:
                        _LOGGER.debug(
                            'Doorbell connection interrupted (%s at %s): %s',
                            type(exc).__name__, stage, message,
                        )
                    else:
                        _LOGGER.debug(
                            'Doorbell connection interrupted (%s at %s)',
                            type(exc).__name__, stage,
                        )
            finally:
                session.close()
                self.session = None
            if time.monotonic() - started >= 30:
                delay = 2
            if self.closed.wait(delay):
                return
            delay = min(delay * 2, 60)
