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


@dataclass(frozen=True)
class RingMessage:
    """Only fields needed to distinguish repeated alarm deliveries."""

    channel: int
    timestamp: str
    timestamp_svr: int


def decode_rings(uid, kind, body):
    """Recognize the alarm reproduced by two isolated physical button presses."""
    if kind != 510:
        return []
    try:
        _, inner = decode_private_reply(uid, body)
        if len(inner) < 8 or struct.unpack_from('>I', inner)[0] != len(inner) - 4:
            return []
        messages = []
        for inner_kind, payload in parse_tlvs(inner[8:]):
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
            if param['alarm_type'] != 14:
                continue
            channel, stamp, server = (param.get('channel'), param.get('timestamp'),
                                      param.get('timestamp_svr'))
            if type(channel) is not int or not 0 <= channel <= 255:
                continue
            if not isinstance(stamp, str) or len(stamp) != 14 or not stamp.isascii() or not stamp.isdigit():
                continue
            if type(server) is not int or not 0 < server < 2**63:
                continue
            messages.append(RingMessage(channel, stamp, server))
        return messages
    except (ProtocolError, ValueError, TypeError, UnicodeError, struct.error):
        return []


class RingListener:
    """Own one control connection and deliver callbacks on the HA event loop."""

    def __init__(self, loop, entry, on_ring, on_state):
        self.loop, self.entry = loop, entry
        self.on_ring, self.on_state = on_ring, on_state
        self.closed = threading.Event()
        self.session = self.thread = None
        self.seen = deque(maxlen=256)

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

    def _worker(self):
        delay = 2
        while not self.closed.is_set():
            data = self.entry.data
            session = Session(data['host'], data['username'], data['password'],
                              channel=0, stream=3, mode=0)
            self.session = session
            started = time.monotonic()
            try:
                if self.closed.is_set():
                    return
                parts = session.connect()
                if session.info.uid != self.entry.unique_id:
                    self._emit(self.on_state, False, 'IdentityMismatch')
                    return
                self._emit(self.on_state, True, None)
                last_received = time.monotonic()
                while not self.closed.is_set():
                    for kind, body in parts:
                        if kind in _MEDIA_TYPES:
                            self._emit(self.on_state, False, 'UnexpectedMedia')
                            return
                        for message in decode_rings(session.info.uid, kind, body):
                            self._accept(message)
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
            except AuthenticationError:
                self._emit(self.on_state, False, 'AuthenticationError')
                return
            except Exception as exc:
                if not self.closed.is_set():
                    error = type(exc).__name__
                    self._emit(self.on_state, False, error)
                    _LOGGER.debug('Doorbell connection interrupted (%s)', error)
            finally:
                session.close()
                self.session = None
            if time.monotonic() - started >= 30:
                delay = 2
            if self.closed.wait(delay):
                return
            delay = min(delay * 2, 60)
