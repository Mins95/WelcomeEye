"""Explicit, single-shot legacy SDK observations; never normal media fallback.

These are experiments, not capabilities. No payload, identity or image is retained
in the hub or logged. Only the explicit caller receives the bounded report.
"""
import asyncio
import json
import re
import socket
import struct
import threading
import time

from .capabilities import DeviceVariant, ProtocolFamily
from .client import AuthenticationError, DiscoveryTimeout, Session
from .protected import (ProtocolError, build_protected_login, decode_discovery,
                        decode_login_reply, decode_private_reply,
                        decode_start_av_reply, decode_stop_av_reply, owsp,
                        parse_tlvs, tlv)
from .protocol import encode_password
from .snapshot import _finish_task

DISCOVERY_BUDGET = 3.0
TCP_BUDGET = 3.0
LOGIN_BUDGET = 6.0
OBSERVATION_BUDGET = 4.0
CLEANUP_BUDGET = 2.0
TOTAL_BUDGET = 18.0
MAX_RECEIVED_BYTES = 2 * 1024 * 1024
MAX_READS = 64
MAX_DECODED_FRAMES = 3
_OPERATIONS = ('version_469', 'additional_camera', 'udt_handshake')
_VERSION_FIELDS = ('AppCom', 'SolCom', 'ReleaseTime', 'HDVersion')


def version_query():
    """APK LtDeviceManager / DataChannel::sendData2: inner OWSP, private 509."""
    return owsp(tlv(469, bytes(4)))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError('Duplicate version field')
        result[key] = value
    return result


def decode_version_response(clear):
    """Accept only the SDK response pair 469 -> 470, never alarm 14854."""
    if not 12 <= len(clear) <= 1024:
        raise ProtocolError('Version response size outside bounds')
    declared = struct.unpack_from('>I', clear)[0]
    if declared != len(clear) - 4 or declared < 8:
        raise ProtocolError('Invalid inner OWSP length')
    parts = parse_tlvs(clear[8:])
    replies = [body for kind, body in parts if kind == 470]
    if not replies:
        return None
    if len(parts) != 1 or len(replies) != 1:
        raise ProtocolError('Ambiguous version response')
    body = replies[0].rstrip(b'\0')
    if not body or len(body) > 512:
        raise ProtocolError('Invalid version metadata size')
    try:
        parsed = json.loads(body.decode('utf-8'), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError('Invalid version metadata') from exc
    if type(parsed) is not dict:
        raise ProtocolError('Version metadata is not an object')
    # No arbitrary manufacturer keys, URLs, serials, UID, host or raw text.
    # Restrict known SDK version/date fields to their non-identifying grammar.
    safe = {}
    for key in _VERSION_FIELDS:
        value = parsed.get(key)
        if (isinstance(value, str) and 0 < len(value) <= 80
                and re.fullmatch(r'[A-Za-z0-9_. :/-]+', value)
                and not re.search(r'UID|https?|secret|token|password', value, re.I)
                and not re.search(r'\b\d{1,3}(?:\.\d{1,3}){3}\b', value)):
            safe[key] = value
    return safe


class _DiagnosticSession(Session):
    """Existing wire builders, exactly one discovery/TCP/login, bounded reader.

    Normal Session.connect() intentionally has a refusal recovery; an experiment
    must not use that retry or modify its discovery cache.
    """

    def __init__(self, *args, operation, cancel_event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.operation = operation
        self.cancelled = cancel_event if cancel_event is not None else threading.Event()
        self.udp = None
        self.deadline = time.monotonic() + TOTAL_BUDGET
        self.network_deadline = self.deadline - CLEANUP_BUDGET
        self.read_deadline = self.network_deadline
        self.received_bytes = 0
        self.sent_counts = {}
        self.authenticated = False

    def cancel(self):
        self.cancelled.set()
        udp = self.udp
        if udp is not None:
            try:
                udp.close()
            except OSError:
                pass
        self.interrupt_read()

    def set_read_budget(self, seconds):
        self.read_deadline = min(self.network_deadline, time.monotonic() + seconds)

    def begin_cleanup(self):
        self.read_deadline = min(self.deadline, time.monotonic() + CLEANUP_BUDGET)

    def _exact(self, size):
        if size < 0 or size > 1048576:
            raise ProtocolError('Experimental frame size outside bounds')
        data = bytearray()
        while len(data) < size:
            if self.cancelled.is_set():
                raise ConnectionAbortedError('Experimental observation cancelled')
            remaining = self.read_deadline - time.monotonic()
            if remaining <= 0 or self.read_count > MAX_READS:
                raise TimeoutError('Experimental receive budget exhausted')
            self.sock.settimeout(min(0.5, remaining))
            try:
                part = self.sock.recv(size - len(data))
            except TimeoutError:
                continue
            if not part:
                raise ConnectionError('Experimental peer closed')
            self.received_bytes += len(part)
            if self.received_bytes > MAX_RECEIVED_BYTES:
                raise ProtocolError('Experimental receive size budget exhausted')
            data.extend(part)
        return bytes(data)

    def send_packet(self, packet):
        parts = parse_tlvs(packet[8:]) if len(packet) >= 12 else []
        kinds = tuple(kind for kind, _ in parts)
        allowed = {40, 501, 49, 5005}
        allowed.add(509 if self.operation == 'version_469' else 5007)
        if self.operation == 'additional_camera':
            allowed.add(5009)
        if not kinds or any(kind not in allowed for kind in kinds):
            raise ProtocolError('Packet outside experimental allowlist')
        if self.cancelled.is_set() and any(kind not in (5009, 5005) for kind in kinds):
            raise ConnectionAbortedError('Experimental observation cancelled')
        if struct.unpack_from('>I', packet)[0] != len(packet) - 4:
            raise ProtocolError('Invalid experimental OWSP envelope')
        for kind in kinds:
            maximum = 2 if kind == 49 else 1
            if self.sent_counts.get(kind, 0) >= maximum:
                raise ProtocolError('Experimental packet already attempted')
        # Count before send: a partially written request is never replayed.
        for kind in kinds:
            self.sent_counts[kind] = self.sent_counts.get(kind, 0) + 1
        remaining = (self.deadline if all(kind in (5009, 5005) for kind in kinds)
                     else self.network_deadline) - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Experimental send budget exhausted')
        if self.sock is not None:
            self.sock.settimeout(min(0.5, remaining))
        super().send_packet(packet)

    def connect(self):
        socket.inet_pton(socket.AF_INET, self.host)
        self.connection_stage = 'discovering'
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp = udp
        try:
            udp.settimeout(min(DISCOVERY_BUDGET, self.network_deadline - time.monotonic()))
            udp.sendto(bytes.fromhex('07a02000') + bytes(32), (self.host, 1500))
            until = min(self.network_deadline, time.monotonic() + DISCOVERY_BUDGET)
            while time.monotonic() < until:
                if self.cancelled.is_set():
                    raise ConnectionAbortedError('Experimental observation cancelled')
                udp.settimeout(max(0.001, until - time.monotonic()))
                try:
                    packet, peer = udp.recvfrom(4096)
                except (TimeoutError, ConnectionRefusedError, ConnectionResetError) as exc:
                    raise DiscoveryTimeout(1) from exc
                if peer[0] == self.host:
                    self.info = decode_discovery(packet)
                    if self.info.address != self.host or not self.info.protected:
                        raise ProtocolError('Unexpected experimental discovery')
                    expected_uid = getattr(self, 'expected_uid', None)
                    if expected_uid and self.info.uid != expected_uid:
                        raise ProtocolError('Experimental device identity mismatch')
                    break
            if self.info is None:
                raise DiscoveryTimeout(1)
        finally:
            udp.close()
            self.udp = None
        if self.cancelled.is_set():
            raise ConnectionAbortedError('Experimental observation cancelled')
        self.connection_stage = 'tcp_connecting'
        self.sock = socket.create_connection((self.host, self.info.tcp_port),
            min(TCP_BUDGET, max(0.001, self.network_deadline - time.monotonic())))
        self.connection_stage = 'sending_login'
        self.send_packet(build_protected_login(
            self.info.uid, self.username, encode_password(self.password),
            channel=self.channel, stream=self.stream, mode=self.mode))
        self.last_keepalive = time.monotonic()
        self.set_read_budget(LOGIN_BUDGET)
        self.connection_stage = 'waiting_login_response'
        while time.monotonic() < self.read_deadline:
            for kind, body in self.read():
                if kind != 502:
                    continue
                status, _, metadata, clock = decode_login_reply(self.info.uid, body, include_time=True)
                if status == 2:
                    raise AuthenticationError('Experimental login refused')
                if status != 1:
                    raise ProtocolError('Unexpected experimental login result')
                self.device_time, self.clock_received = clock, time.monotonic()
                self.encryption_profile = metadata.get('AppId')
                self.authenticated = True
                self.connection_stage = 'authenticated'
                return []  # Discard pre-query messages; no stale correlation.
        raise TimeoutError('Experimental login budget exhausted')


def _alive(worker):
    return worker is not None and worker.is_alive()


def _active_capture(capture):
    for name in ('_task', 'task', '_worker', '_capture_task'):
        task = getattr(capture, name, None)
        if task is not None and hasattr(task, 'done') and not task.done():
            return True
    return False


class ExperimentalDiagnostics:
    """Per-loaded-entry backend. A denied/busy request never consumes its trial."""

    def __init__(self, hub):
        self.hub = hub
        self._attempted = set()
        self._session = None
        self._task = None
        self._stopped = False
        self._cancel_requested = threading.Event()

    def _permission(self, user, entity_id):
        # HA's POLICY_CONTROL is the literal 'control'; use the real public
        # constant when HA is installed, keeping offline fixtures importable.
        try:
            from homeassistant.auth.permissions.const import POLICY_CONTROL
        except ModuleNotFoundError:
            POLICY_CONTROL = 'control'
        return (user is not None and bool(getattr(user, 'id', None))
                and getattr(user, 'is_admin', False) is True
                and isinstance(entity_id, str) and entity_id.startswith('camera.')
                and getattr(user, 'permissions', None) is not None
                and bool(user.permissions.check_entity(entity_id, POLICY_CONTROL)))

    def _busy(self):
        hub = self.hub
        ring = getattr(hub, 'ring_listener', None)
        return (bool(getattr(hub, 'consumers', ()))
                or getattr(hub, 'session', None) is not None
                or _alive(getattr(hub, 'thread', None))
                or bool(getattr(ring, 'enabled', False))
                or getattr(ring, 'session', None) is not None
                or _alive(getattr(ring, 'thread', None))
                or _active_capture(getattr(hub, 'ring_image', None))
                or _active_capture(getattr(hub, 'manual_snapshot', None)))

    async def execute(self, operation, *, confirm=False, user=None, entity_id=None,
                      udp_port=None, legacy_discovery_absent=False):
        report = {'operation': operation, 'status': 'not_validated',
                  'provenance': 'explicit_local_experiment', 'reason': None}
        if operation not in _OPERATIONS:
            raise ValueError('Unknown experimental diagnostic')
        if not self._permission(user, entity_id):
            raise PermissionError('Experimental diagnostic requires admin and entity control')
        if confirm is not True:
            report['reason'] = 'explicit_confirmation_required'
            return report
        if self.hub.protocol_family != ProtocolFamily.LEGACY:
            report['reason'] = 'legacy_protocol_required'
            return report
        if operation == 'udt_handshake':
            if legacy_discovery_absent is not True:
                report['reason'] = 'legacy_discovery_absence_required'
                return report
            if type(udp_port) is not int or not 1 <= udp_port <= 65535:
                report['reason'] = 'established_udp_endpoint_required'
                return report
        if self._stopped or getattr(self.hub, 'stopped', False):
            report['reason'] = 'entry_stopped'
            return report
        if operation in self._attempted:
            report['reason'] = 'already_attempted_for_loaded_entry'
            return report
        if self._task is not None or self.hub.lock.locked() or self._busy():
            report['reason'] = 'busy'
            return report
        control_lock = self.hub.control.lock
        if not control_lock.acquire(blocking=False):
            report['reason'] = 'busy'
            return report
        try:
            async with self.hub.lock:
                if self._busy() or self._stopped:
                    report['reason'] = 'busy'
                    return report
                self._attempted.add(operation)
                self._cancel_requested.clear()
                if operation == 'udt_handshake':
                    self._task = asyncio.create_task(asyncio.to_thread(self._run_udt, udp_port, report))
                else:
                    self._task = asyncio.create_task(asyncio.to_thread(self._run, operation, report))
                try:
                    return await asyncio.shield(self._task)
                except asyncio.CancelledError:
                    self._cancel_requested.set()
                    if self._session is not None:
                        self._session.cancel()
                    try:
                        await _finish_task(self._task, cancel_on_cancel=False)
                    except asyncio.CancelledError:
                        # Repeated cancellation is propagated only once the
                        # owned thread has settled, keeping both locks held.
                        pass
                    raise
                finally:
                    self._task = None
        finally:
            control_lock.release()

    async def stop(self):
        self._stopped = True
        self._cancel_requested.set()
        if self._session is not None:
            self._session.cancel()
        task = self._task
        if task is not None:
            await _finish_task(task, cancel_on_cancel=False)

    def _run_udt(self, udp_port, report):
        """One explicit endpoint, no discovery, OWSP login, media or credentials."""
        from .experimental_udt import probe_handshake

        if self._stopped or self._cancel_requested.is_set():
            report.update(reason='entry_stopped', error_type='ConnectionAbortedError')
            return report
        try:
            report.update(probe_handshake(self.hub.entry.data['host'], udp_port,
                                         cancel_event=self._cancel_requested))
        except Exception as exc:
            # Exception text can include the private endpoint; never expose it.
            report.update(error_type=type(exc).__name__, last_stage='udt_probe')
        return report

    def _run(self, operation, report):
        started = time.monotonic()
        data = self.hub.entry.data
        additional = operation == 'additional_camera'
        session = _DiagnosticSession(data['host'], data['username'], data['password'],
                                     18 if additional else 0, stream=1 if additional else 3,
                                     mode=2 if additional else 0, operation=operation,
                                     cancel_event=self._cancel_requested)
        self._session = session
        session.expected_uid = getattr(self.hub.entry, 'unique_id', None)
        start_attempted = False
        report.update(login_accepted=False, request_attempted=False, request_sent=False,
                      response_correlated=False, last_stage='discovery', error_type=None,
                      decoded_frames=0, received_media_packets=0, source_mapping_status='not_validated')
        cleanup = {'stop_av_attempted': False, 'stop_av_sent': False,
                   'stop_av_response_received': False, 'stop_av_result': None,
                   'session_stop_attempted': False, 'session_stop_sent': False,
                   'tcp_closed': False, 'error_types': []}
        reader_usable = True
        try:
            if self._stopped or self._cancel_requested.is_set():
                session.cancel()
                raise ConnectionAbortedError('Experimental entry stopped')
            session.connect()
            report['login_accepted'] = True
            report['last_stage'] = 'sending_request'
            report['request_attempted'] = True
            if additional:
                if self.hub.variant == DeviceVariant.V1:
                    session.enable_v1_video_receive()
                start_attempted = True
                session.send_start_av()
            else:
                session.send_manufacturer(version_query())
            report['request_sent'] = True
            session.set_read_budget(OBSERVATION_BUDGET)
            report['last_stage'] = 'observing'
            if additional:
                self._observe_camera(session, report)
            else:
                self._observe_version(session, report)
        except Exception as exc:
            reader_usable = False
            report['error_type'] = type(exc).__name__
            if not report['login_accepted']:
                report['last_stage'] = session.connection_stage
        finally:
            # Use the existing protected Stop AV and native session stop; do
            # not invent a release handshake or claim physical release on EOF.
            session.begin_cleanup()
            if start_attempted and session.sock is not None:
                cleanup['stop_av_attempted'] = True
                try:
                    session.send_stop_av()
                    cleanup['stop_av_sent'] = True
                    if reader_usable and not session.cancelled.is_set():
                        while time.monotonic() < session.read_deadline:
                            replies = [body for kind, body in session.read() if kind == 5010]
                            if replies:
                                _, result, _ = decode_stop_av_reply(session.info.uid, session.encryption_profile, replies[0])
                                cleanup['stop_av_response_received'] = True
                                cleanup['stop_av_result'] = result
                                break
                except Exception as exc:
                    cleanup['error_types'].append(type(exc).__name__)
            if session.authenticated and session.sock is not None:
                cleanup['session_stop_attempted'] = True
                try:
                    session.send_session_stop()
                    cleanup['session_stop_sent'] = True
                except Exception as exc:
                    cleanup['error_types'].append(type(exc).__name__)
            session.close()
            cleanup['tcp_closed'] = session.closed.is_set()
            report['cleanup'] = cleanup
            report['elapsed_ms'] = round((time.monotonic() - started) * 1000)
            report['requests_attempted'] = {str(key): value for key, value in session.sent_counts.items()}
            self._session = None
        return report

    def _observe_version(self, session, report):
        report['unrelated_private_responses'] = 0
        while time.monotonic() < session.read_deadline:
            for kind, body in session.read():
                if kind != 510:
                    continue
                _, clear = decode_private_reply(session.info.uid, body)
                metadata = decode_version_response(clear)
                if metadata is None:
                    report['unrelated_private_responses'] += 1
                    continue
                report.update(status='observed', response_correlated=True,
                              metadata=metadata, last_stage='version_response')
                return
        raise TimeoutError('Experimental version response not observed')

    def _observe_camera(self, session, report):
        import av
        from .media import StreamFormat, inspect_h264_packet, normalize_h264_packet
        from .v1_video import V1VideoReceiver
        counters = {}
        class Observer:
            def video_event(self, key):
                counters[key] = counters.get(key, 0) + 1
        receiver = V1VideoReceiver(Observer()) if session.v1_video_receive else None
        decoder = av.CodecContext.create('h264', 'r')
        decoder.thread_count = 1
        decoder.options = {'max_pixels': str(4096 * 4096)}
        report['start_accepted'] = False
        try:
            while time.monotonic() < session.read_deadline:
                for kind, body in session.read():
                    if kind == 5008:
                        _, result, _ = decode_start_av_reply(session.info.uid, session.encryption_profile, body)
                        report.update(start_result=result, start_accepted=result == 1,
                                      response_correlated=True)
                        if result != 1:
                            report.update(status='not_validated', last_stage='start_non_success')
                            return
                    if kind == 203:
                        fmt = StreamFormat.parse(body)
                        report['announced_dimensions'] = [fmt.width, fmt.height]
                    packet = None
                    if receiver is not None:
                        found = receiver.receive(kind, body)
                        if found is not None:
                            packet = found[0]
                    elif kind in (97, 99, 100, 101):
                        info = inspect_h264_packet(body)
                        if info.detected:
                            packet = normalize_h264_packet(body, info.framing)
                    if packet is None:
                        continue
                    report['received_media_packets'] += 1
                    for frame in decoder.decode(av.Packet(packet)):
                        if not 0 < frame.width <= 4096 or not 0 < frame.height <= 4096:
                            raise ProtocolError('Experimental decoded dimensions outside bounds')
                        report['decoded_frames'] += 1
                        report['decoded_dimensions'] = [frame.width, frame.height]
                        report.update(status='observed', last_stage='frame_decoded')
                        if report['decoded_frames'] >= MAX_DECODED_FRAMES:
                            return
        finally:
            decoder.flush_buffers()
