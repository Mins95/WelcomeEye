"""Bounded, local-only WelcomeEye TCP session."""
import socket
import struct
import time
import threading

from .protected import (ProtocolError, build_private_query, build_protected_login,
                        build_start_av_request, build_stop_av_request, decode_discovery,
                        decode_login_reply, owsp, parse_tlvs, tlv)
from .protocol import encode_password


class AuthenticationError(Exception):
    """The device refused the supplied credentials."""


_DISCOVERY_CACHE = {}
_DISCOVERY_LOCK = threading.Lock()
_DISCOVERY_NETWORK_REQUESTS = 0
_DISCOVERY_CACHE_HITS = 0
_DISCOVERY_CACHE_INVALIDATIONS = 0
_DISCOVERY_CONNECTION_RETRIES = 0


def discovery_diagnostics():
    return {
        'cached_hosts': len(_DISCOVERY_CACHE),
        'network_requests': _DISCOVERY_NETWORK_REQUESTS,
        'cache_hits': _DISCOVERY_CACHE_HITS,
        'cache_invalidations': _DISCOVERY_CACHE_INVALIDATIONS,
        'connection_refused_rediscoveries': _DISCOVERY_CONNECTION_RETRIES,
    }


def _invalidate_discovery(host, expected):
    """Forget only this observation, never a newer concurrent discovery."""
    global _DISCOVERY_CACHE_INVALIDATIONS
    with _DISCOVERY_LOCK:
        if expected is not None and _DISCOVERY_CACHE.get(host) is expected:
            _DISCOVERY_CACHE.pop(host, None)
            _DISCOVERY_CACHE_INVALIDATIONS += 1
            return True
    return False


def _recover_discovery_after_refused(host, expected):
    """Preserve the existing single pre-login retry after TCP refusal."""
    global _DISCOVERY_CONNECTION_RETRIES
    invalidated = _invalidate_discovery(host, expected)
    with _DISCOVERY_LOCK:
        _DISCOVERY_CONNECTION_RETRIES += 1
    return invalidated


def discover(host, *, diagnostics=None):
    """Reuse discovery; failed pre-auth transports invalidate their observation."""
    global _DISCOVERY_NETWORK_REQUESTS, _DISCOVERY_CACHE_HITS
    socket.inet_pton(socket.AF_INET, host)
    # Serialize discovery so ring/media/control workers cannot overlap UDP probes.
    started = time.monotonic()
    with _DISCOVERY_LOCK:
        if diagnostics is not None:
            diagnostics['discovery_lock_wait_ms'] = round((time.monotonic() - started) * 1000)
        cached = _DISCOVERY_CACHE.get(host)
        if cached is not None:
            _DISCOVERY_CACHE_HITS += 1
            if diagnostics is not None:
                diagnostics['endpoint_source'] = 'cache'
            return cached
        _DISCOVERY_NETWORK_REQUESTS += 1
        if diagnostics is not None:
            diagnostics['endpoint_source'] = 'fresh_discovery'
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.settimeout(3)
            for _ in range(2):
                udp.sendto(bytes.fromhex('07a02000') + bytes(32), (host, 1500))
                if diagnostics is not None:
                    diagnostics['discovery_probe_count'] += 1
                until = time.monotonic() + 3
                while time.monotonic() < until:
                    udp.settimeout(max(0.01, until - time.monotonic()))
                    try:
                        packet, peer = udp.recvfrom(4096)
                    except TimeoutError:
                        break
                    if peer[0] == host:
                        info = decode_discovery(packet)
                        if info.address != host:
                            raise ProtocolError('Discovery address mismatch')
                        if not info.protected:
                            raise ProtocolError('This integration requires a protected WelcomeEye protocol')
                        _DISCOVERY_CACHE[host] = info
                        if diagnostics is not None:
                            diagnostics['discovery_response_received'] = True
                        return info
        raise TimeoutError('No device discovery response')


class Session:
    def __init__(self, host, username, password, channel=16, *, stream=1, mode=2):
        self.host, self.username, self.password = host, username, password
        self.channel = channel
        self.stream, self.mode = stream, mode
        self.closed = threading.Event()
        self.sock = None
        self.write_lock = threading.RLock()
        self.talk_enabled = self.talk_requested = False
        self._login_deadline = None
        self.info = None
        self.last_keepalive = 0
        self.device_time = 0
        self.clock_received = 0
        self.encryption_profile = None
        self.read_count = 0
        self.keepalive_count = 0
        self.last_keepalive_sent_at = 0.0
        self.invalid_frame_count = 0
        self.zero_frame_count = 0
        self.last_invalid_frame_be_length = None
        self.last_invalid_frame_le_length = None
        self.last_invalid_frame_after_keepalive = False
        self.last_frame_size = None
        self.connection_stage = 'idle'
        self.connection_error_type = None
        self.connection_attempts = []
        self.connection_elapsed_ms = None
        self.login_frames_seen = 0
        self.login_timeout_count = 0
        self.login_tlv_counts = {}
        # Enabled only by the V1 media worker; control/ring sessions leave it off.
        self.media_observer = None
        self.v1_video_receive = False
        self.v1_allow_idle_timeouts = False
        self._v1_read_started = 0.0
        self._v1_read_failed = False

    def enable_v1_video_receive(self):
        """Opt in only on the authenticated V1 media session."""
        self.v1_video_receive = True

    def connect(self):
        self.connection_error_type = None
        self.connection_stage = 'discovering'
        self.connection_attempts = []
        self.connection_elapsed_ms = None
        started = time.monotonic()
        login_started = None
        attempt = None
        try:
            for tcp_attempt in range(2):
                attempt = {
                    'attempt': tcp_attempt + 1, 'endpoint_source': 'not_selected',
                    'discovery_lock_wait_ms': None, 'discovery_elapsed_ms': None,
                    'discovery_probe_count': 0, 'discovery_response_received': False,
                    'tcp_connect_elapsed_ms': None, 'tcp_connected': False,
                    'login_sent': False, 'login_elapsed_ms': None, 'authenticated': False,
                    'cache_invalidated': False, 'cache_invalidation_reason': None,
                    'error_stage': None, 'error_type': None,
                }
                self.connection_attempts.append(attempt)
                phase_started = time.monotonic()
                try:
                    self.info = discover(self.host, diagnostics=attempt)
                finally:
                    attempt['discovery_elapsed_ms'] = round((time.monotonic() - phase_started) * 1000)
                self.connection_stage = 'discovered'
                if self.closed.is_set():
                    raise ConnectionAbortedError('Session cancelled')

                self.connection_stage = 'tcp_connecting'
                phase_started = time.monotonic()
                try:
                    try:
                        self.sock = socket.create_connection(
                            (self.host, self.info.tcp_port), timeout=5
                        )
                    finally:
                        attempt['tcp_connect_elapsed_ms'] = round((time.monotonic() - phase_started) * 1000)
                except ConnectionRefusedError:
                    if tcp_attempt:
                        raise
                    # A V1 can advertise a TCP endpoint that becomes stale after
                    # the previous media session closes. Refresh discovery once,
                    # before login and before any output packet can exist.
                    attempt.update(
                        error_stage='tcp_connecting', error_type='ConnectionRefusedError',
                        cache_invalidated=_recover_discovery_after_refused(self.host, self.info),
                        cache_invalidation_reason='connection_refused',
                    )
                    self.connection_stage = 'rediscovering_after_refused'
                    continue
                break

            self.connection_stage = 'tcp_connected'
            attempt['tcp_connected'] = True
            if self.closed.is_set():
                raise ConnectionAbortedError('Session cancelled')

            self.sock.settimeout(2)
            self.connection_stage = 'sending_login'
            login_started = time.monotonic()
            self.send_packet(build_protected_login(
                self.info.uid,
                self.username,
                encode_password(self.password),
                channel=self.channel,
                stream=self.stream,
                mode=self.mode,
            ))
            self.connection_stage = 'login_sent'
            attempt['login_sent'] = True
            self.last_keepalive = time.monotonic()

            # The native LT parser keeps scanning OWSP frames until the login
            # response arrives. A V1 may emit empty/padding frames or unrelated
            # TLVs before TLV 502, so do not assume the first read contains it.
            deadline = time.monotonic() + 20
            self._login_deadline = deadline
            collected = []
            while time.monotonic() < deadline:
                self.connection_stage = 'waiting_login_response'
                try:
                    parts = self.read()
                except TimeoutError:
                    self.login_timeout_count += 1
                    continue

                self.login_frames_seen += 1
                collected.extend(parts)
                for kind, _body in parts:
                    self.login_tlv_counts[kind] = self.login_tlv_counts.get(kind, 0) + 1

                replies = [body for kind, body in parts if kind == 502]
                if not replies:
                    continue

                self.connection_stage = 'decoding_login_response'
                status, _, metadata, device_time = decode_login_reply(
                    self.info.uid, replies[0], include_time=True
                )
                if status == 2:
                    raise AuthenticationError('Device refused authentication')
                if status != 1:
                    raise ProtocolError(f'Device login status {status}')

                self.device_time = device_time
                self.clock_received = time.monotonic()
                self.encryption_profile = metadata.get('AppId')
                self.last_keepalive = time.monotonic()
                self.connection_stage = 'authenticated'
                attempt['authenticated'] = True
                attempt['login_elapsed_ms'] = round((time.monotonic() - login_started) * 1000)
                return collected

            self.connection_stage = 'login_response_timeout'
            raise TimeoutError('WelcomeEye login response timed out')
        except BaseException as exc:
            self.connection_error_type = type(exc).__name__
            if attempt is not None:
                attempt.update(error_stage=self.connection_stage, error_type=type(exc).__name__)
                if login_started is not None:
                    attempt['login_elapsed_ms'] = round((time.monotonic() - login_started) * 1000)
            # No new attempt here. The NEXT normal acquisition must rediscover
            # after a pre-auth transport failure, including a second refusal.
            # Authentication refusal/protocol errors and explicit cancellation
            # do not establish an unusable endpoint. Never evict after auth/media.
            invalidate = (isinstance(exc, OSError) and not self.closed.is_set()
                          and self.info is not None and self.connection_stage != 'authenticated')
            if not self.connection_stage.startswith('failed_'):
                self.connection_stage = f'failed_{self.connection_stage}'
            # Release TCP before waiting for a concurrent UDP discovery's lock.
            self.close()
            if invalidate:
                invalidated = _invalidate_discovery(self.host, self.info)
                if attempt is not None and invalidated:
                    attempt['cache_invalidated'] = True
                    attempt['cache_invalidation_reason'] = 'preauth_transport_failure'
            raise
        finally:
            self._login_deadline = None
            self.connection_elapsed_ms = round((time.monotonic() - started) * 1000)

    def connection_diagnostics(self):
        """Stages, durations and flags only; no endpoints, identity or payloads."""
        return {
            'connection_stage': self.connection_stage,
            'connection_error_type': self.connection_error_type,
            'connection_elapsed_ms': self.connection_elapsed_ms,
            'connection_attempts': [dict(item) for item in self.connection_attempts],
            'read_count': self.read_count,
            'login_frames_seen': self.login_frames_seen,
            'login_timeout_count': self.login_timeout_count,
        }

    def device_now(self):
        if self.device_time <= 0:
            raise ProtocolError('Device clock is unavailable')
        return self.device_time + int(time.monotonic() - self.clock_received)

    def send_packet(self, packet):
        """Serialize microphone and media/control writes on the same TCP stream."""
        with self.write_lock:
            if self.closed.is_set() or self.sock is None:
                raise ConnectionAbortedError('Session closed')
            self.sock.sendall(packet)

    def start_talk(self):
        from .talkback import talk_request
        with self.write_lock:
            if self.connection_stage != 'authenticated' or self.talk_requested:
                raise ProtocolError('Talk requires an authenticated media session')
            self.talk_requested = True
            self.send_packet(talk_request(self, True))

    def send_talk_audio(self, data):
        from .talkback import audio_request
        with self.write_lock:
            if not self.talk_enabled or self.closed.is_set():
                return False
            self.send_packet(audio_request(self.channel, data))
            return True

    def stop_talk(self):
        from .talkback import talk_request
        self.talk_enabled = False
        with self.write_lock:
            if not self.talk_requested:
                return
            self.talk_requested = False
            if not self.closed.is_set() and self.sock is not None:
                self.send_packet(talk_request(self, False))

    def _exact(self, size):
        if self.v1_video_receive:
            return self._v1_exact(size)
        data = bytearray()
        try:
            while len(data) < size:
                if self.closed.is_set():
                    raise ConnectionAbortedError('Session cancelled')
                if self._login_deadline is not None:
                    remaining = self._login_deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('Login receive deadline exceeded')
                    self.sock.settimeout(min(2.0, remaining))
                try:
                    part = self.sock.recv(size - len(data))
                except TimeoutError:
                    if self._login_deadline is None or time.monotonic() >= self._login_deadline:
                        raise
                    # Retain partial login frame bytes across socket timeouts.
                    self.login_timeout_count += 1
                    continue
                if not part:
                    self.remote_eof = True
                    raise ConnectionError('Device closed the connection')
                data.extend(part)
        except OSError as exc:
            if self.media_observer is not None:
                self.media_observer.read_failed(size, len(data), type(exc).__name__)
            raise
        return bytes(data)

    def _v1_exact(self, size):
        """Retain partial bytes across socket timeouts, never across failure.

        Native IParser retains unconsumed data (0x68a22). Here the same read
        owns its buffer until complete: 6 s without progress, 20 s total for
        header + padding + payload, and at most 1 MiB from read()'s size check.
        A failed partial read poisons the reader; Stop AV can still be sent.
        """
        data = bytearray()
        sock = self.sock
        previous_timeout = sock.gettimeout()
        progress = time.monotonic()
        try:
            while len(data) < size:
                now = time.monotonic()
                remaining = min(6 - (now - progress), 20 - (now - self._v1_read_started))
                if remaining <= 0:
                    raise ProtocolError('V1 OWSP receive deadline exceeded')
                if self.closed.is_set():
                    raise ConnectionAbortedError('Session cancelled')
                sock.settimeout(min(2.0, remaining))
                if now - self.last_keepalive >= 10:
                    self.send_keepalive()
                try:
                    part = sock.recv(size - len(data))
                except TimeoutError:
                    if self.media_observer is not None:
                        self.media_observer.receive_timeout(size, len(data))
                    # Only a completely untouched length word can be considered
                    # clean idle time. Partial frame bytes are retained until the
                    # bounded receive deadline and are never silently discarded.
                    if size == 4 and not data and self._v1_reading_header:
                        raise
                    continue
                if not part:
                    self.remote_eof = True
                    raise ConnectionError('Device closed the connection')
                now = time.monotonic()
                if now - progress >= 6 or now - self._v1_read_started >= 20:
                    raise ProtocolError('V1 OWSP receive deadline exceeded')
                data.extend(part)
                progress = now
            return bytes(data)
        except (OSError, ProtocolError) as exc:
            idle_header = (isinstance(exc, TimeoutError) and not data
                           and self._v1_reading_header)
            if not idle_header:
                self._v1_read_failed = True
            if self.media_observer is not None:
                self.media_observer.read_failed(size, len(data), type(exc).__name__)
            raise
        finally:
            # close()/interrupt_read() may race the worker during shutdown.
            try:
                sock.settimeout(previous_timeout)
            except OSError:
                pass

    def send_keepalive(self):
        # Native DataChannel::sendAliveReq equivalent used by the validated path.
        self.send_packet(owsp(tlv(49, bytes((self.channel, 0, 0, 0)))))
        now = time.monotonic()
        self.last_keepalive = now
        self.last_keepalive_sent_at = now
        self.keepalive_count += 1

    def send_start_av(self):
        if type(self.encryption_profile) is not int:
            raise ProtocolError('Start AV encryption profile unavailable')
        self.send_packet(build_start_av_request(
            self.info.uid, self.encryption_profile, self.device_now(),
            self.channel, self.stream, self.mode,
        ))

    def send_stop_av(self):
        if type(self.encryption_profile) is not int:
            raise ProtocolError('Stop AV encryption profile unavailable')
        self.send_packet(build_stop_av_request(
            self.info.uid, self.encryption_profile, self.device_now(),
            self.channel, self.stream, self.mode,
        ))

    def send_session_stop(self):
        """Native DataChannel::stop: zero-payload TLV 5005, sequence zero.

        Explicit opt-in by the V1 media teardown; not used by Connect 2 control.
        See docs/stabilization-beta8.md for the packetOWSP/native call chain.
        """
        self.send_packet(owsp(tlv(5005, b'')))

    def interrupt_read(self):
        """Wake a worker blocked in recv while keeping the write side alive."""
        sock = self.sock
        if sock:
            try:
                sock.shutdown(socket.SHUT_RD)
            except OSError:
                pass

    def send_manufacturer(self, data):
        if type(self.encryption_profile) is not int:
            raise ProtocolError('Manufacturer encryption profile unavailable')
        self.send_packet(build_private_query(
            self.info.uid, self.encryption_profile, self.device_now(), data, kind=509
        ))

    def framing_diagnostics(self):
        return {
            **self.connection_diagnostics(),
            'zero_frame_count': self.zero_frame_count,
            'keepalive_count': self.keepalive_count,
            'invalid_frame_count': self.invalid_frame_count,
            'last_invalid_be_length': self.last_invalid_frame_be_length,
            'last_invalid_le_length': self.last_invalid_frame_le_length,
            'invalid_after_keepalive': self.last_invalid_frame_after_keepalive,
            'last_valid_frame_size': self.last_frame_size,
            'login_tlv_counts': {
                str(kind): self.login_tlv_counts[kind]
                for kind in sorted(self.login_tlv_counts)
            },
        }

    def read(self):
        if self.v1_video_receive:
            if self._v1_read_failed:
                raise ProtocolError('V1 OWSP reader requires a new session')
            self._v1_read_started = time.monotonic()
        if time.monotonic() - self.last_keepalive >= 10:
            self.send_keepalive()

        # libglnkio's OWSP parser treats a 0-length word as an empty/padding
        # marker and continues scanning. V1 devices use this on live/control
        # channels, so it must not tear down the session.
        while True:
            if self._login_deadline is not None and time.monotonic() >= self._login_deadline:
                raise TimeoutError('Login receive deadline exceeded')
            self._v1_reading_header = True
            if self.media_observer is not None:
                self.media_observer.begin_header()
            try:
                header = self._exact(4)
            except TimeoutError:
                # While a physical V1 output is awaiting TLV 506, the device may
                # briefly stop emitting media. Keep this exact authenticated
                # session alive only for that explicit command window. Normal V1
                # and all Connect 2 timeout behavior is otherwise unchanged.
                if (self.v1_video_receive and self.v1_allow_idle_timeouts
                        and not self._v1_read_failed):
                    return []
                raise
            size = struct.unpack('>I', header)[0]
            if size == 0:
                self.zero_frame_count += 1
                continue
            if not 4 <= size <= 1048576:
                self.invalid_frame_count += 1
                self.last_invalid_frame_be_length = size
                self.last_invalid_frame_le_length = struct.unpack('<I', header)[0]
                self.last_invalid_frame_after_keepalive = (
                    self.last_keepalive_sent_at > 0
                    and time.monotonic() - self.last_keepalive_sent_at <= 2.0
                )
                raise ProtocolError('Frame length outside bounds')
            self.read_count += 1
            self.last_frame_size = size
            if self.media_observer is not None:
                self.media_observer.begin_payload(size)
            self._v1_reading_header = False
            frame = self._exact(size)
            if self.media_observer is not None:
                self.media_observer.complete(frame)
            try:
                if self.v1_video_receive:
                    from .v1_video import parse_video_tlvs
                    parts = parse_video_tlvs(frame[4:])
                else:
                    parts = parse_tlvs(frame[4:])
            except ProtocolError:
                if self.media_observer is not None:
                    self.media_observer.parsed(False)
                raise
            if self.media_observer is not None:
                self.media_observer.parsed(True)
            return parts

    def close(self):
        self.talk_enabled = False
        self.closed.set()
        sock, self.sock = self.sock, None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


def validate_connection(host, username, password, channel=16):
    session = Session(host, username, password, channel)
    try:
        session.connect()
        return session.info.uid
    finally:
        session.close()
