"""Explicit, bounded V1 alarm observations; never a doorbell capability.

One owner retains the experimental/media and output locks through TCP cleanup.
Reports contain only counts, numeric message metadata and relative times.
"""
import asyncio
import copy
import struct
import threading
import time

from .capabilities import DeviceVariant, ProtocolFamily
from .client import Session
from .experimental_diagnostics import ExperimentalDiagnostics, _DiagnosticSession
from .protected import ProtocolError, parse_tlvs
from .ring import decode_alarm_observations
from .snapshot import _finish_task

STARTUP_BUDGET = 12.0
DEFAULT_DURATION = MAX_DURATION = 300
MIN_DURATION = 30
CLEANUP_BUDGET = 2.0
KEEPALIVE_INTERVAL = 5.0
MAX_READS = 1024
MAX_RECEIVED_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 128
MAX_MARKERS = 32
MAX_COUNTER_KEYS = 64
PROFILES = {'control': (0, 3, 0), 'long_connection': (0, 7, 0)}


class _ReceiveLimit(ProtocolError):
    """The bounded observation received more traffic than permitted."""


class _TrialSession(_DiagnosticSession):
    """Existing one-shot discovery/login and OWSP reader with a five-second beat."""

    def __init__(self, *args, on_parts, **kwargs):
        super().__init__(*args, operation='v1_doorbell_trial', **kwargs)
        self.on_parts = on_parts
        self.network_deadline = time.monotonic() + STARTUP_BUDGET
        self.deadline = self.network_deadline + CLEANUP_BUDGET
        self.read_deadline = self.network_deadline
        self.partial_frame_at_end = False

    def begin_observation(self, duration):
        self.network_deadline = time.monotonic() + duration
        self.deadline = self.network_deadline + CLEANUP_BUDGET
        self.read_deadline = self.network_deadline

    def send_packet(self, packet):
        if (len(packet) < 12
                or struct.unpack_from('>I', packet)[0] != len(packet) - 4):
            raise ProtocolError('Invalid trial OWSP envelope')
        parts = parse_tlvs(packet[8:])
        kinds = tuple(kind for kind, _ in parts)
        # No manufacturer request, output, Start AV, Stop AV or talk packet.
        if kinds not in ((40, 501), (49,), (5005,)):
            raise ProtocolError('Packet outside trial allowlist')
        if kinds == (49,) and parts[0][1] != bytes((self.channel, 0, 0, 0)):
            raise ProtocolError('Invalid trial keepalive')
        if kinds == (5005,) and parts[0][1]:
            raise ProtocolError('Invalid trial session stop')
        cleanup = kinds == (5005,)
        if self.cancelled.is_set() and not cleanup:
            raise ConnectionAbortedError('Trial cancelled')
        for kind in kinds:
            maximum = MAX_DURATION // int(KEEPALIVE_INTERVAL) + 3 if kind == 49 else 1
            if self.sent_counts.get(kind, 0) >= maximum:
                raise ProtocolError('Trial packet budget exhausted')
        # A partial write consumes its attempt and is never replayed.
        for kind in kinds:
            self.sent_counts[kind] = self.sent_counts.get(kind, 0) + 1
        remaining = (self.deadline if cleanup else self.network_deadline) - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Trial send deadline reached')
        if self.sock is not None:
            self.sock.settimeout(min(0.5, remaining))
        Session.send_packet(self, packet)

    def _exact(self, size):
        if not 0 <= size <= 1048576:
            raise ProtocolError('Trial frame size outside bounds')
        data = bytearray()
        while len(data) < size:
            if self.cancelled.is_set():
                raise ConnectionAbortedError('Trial cancelled')
            remaining = self.read_deadline - time.monotonic()
            if remaining <= 0:
                self.partial_frame_at_end = bool(data) or not getattr(self, '_v1_reading_header', True)
                raise TimeoutError('Trial receive deadline reached')
            if self.read_count > MAX_READS:
                raise _ReceiveLimit('Trial frame count limit reached')
            if self.authenticated and time.monotonic() - self.last_keepalive >= KEEPALIVE_INTERVAL:
                self.send_keepalive()
            self.sock.settimeout(min(0.5, remaining))
            try:
                part = self.sock.recv(size - len(data))
            except TimeoutError:
                # Retain partial words/bodies; one worker remains the reader.
                continue
            if not part:
                raise ConnectionError('Trial peer closed')
            self.received_bytes += len(part)
            if self.received_bytes > MAX_RECEIVED_BYTES:
                raise _ReceiveLimit('Trial byte limit reached')
            data.extend(part)
        return bytes(data)

    def read(self):
        parts = super().read()
        self.on_parts(parts, self.authenticated)
        return parts


class V1DoorbellTrial:
    """Manual start/mark/status/stop controller; service layer authorizes callers."""

    def __init__(self, hub):
        self.hub = hub
        self._guard = ExperimentalDiagnostics(hub)
        self._lock = threading.RLock()
        self._task = self._session = None
        self._ready = None
        self._cancel_requested = threading.Event()
        self._closed = False
        self._started = self._observation_started = None
        self._report = {'status': 'idle', 'result': 'not_validated', 'active': False}

    @property
    def active(self):
        return self._task is not None and not self._task.done()

    def _notify(self):
        self.hub.loop.call_soon_threadsafe(self._deliver_notification)

    def _deliver_notification(self):
        if not self._closed and not self.hub.stopped:
            notify = getattr(self.hub, '_notify', None)
            if notify is not None:
                notify()

    def _elapsed(self):
        if self._observation_started is None:
            return 0
        return max(0, round((time.monotonic() - self._observation_started) * 1000))

    def snapshot(self):
        with self._lock:
            report = copy.deepcopy(self._report)
            report['active'] = self.active
            if self.active:
                report['elapsed_ms'] = self._elapsed()
            return report

    def _denied(self, reason):
        return {'status': 'not_started', 'result': 'not_validated', 'reason': reason,
                'active': self.active}

    async def start(self, *, profile='control', duration=DEFAULT_DURATION, confirm=False):
        if profile not in PROFILES:
            raise ValueError('Unknown V1 trial profile')
        if type(duration) is not int or not MIN_DURATION <= duration <= MAX_DURATION:
            raise ValueError('Trial duration must be between 30 and 300 seconds')
        if confirm is not True:
            return self._denied('explicit_confirmation_required')
        if self.hub.protocol_family != ProtocolFamily.LEGACY or self.hub.variant != DeviceVariant.V1:
            return self._denied('v1_legacy_required')
        if self._closed or getattr(self.hub, 'stopped', False):
            return self._denied('entry_stopped')
        if time.monotonic() - getattr(self.hub, '_last_v1_stop_started', float('-inf')) < 2.0:
            return self._denied('previous_session_settling')
        if self.active or self.hub.lock.locked() or self._guard._busy():
            return self._denied('busy')
        control_lock = self.hub.control.lock
        if not control_lock.acquire(blocking=False):
            return self._denied('busy')
        reserved = False
        try:
            await self.hub.lock.acquire()
            reserved = True
            if self._closed or self.hub.stopped or self._guard._busy():
                return self._denied('busy')
            self._cancel_requested.clear()
            self._ready = asyncio.Event()
            self._started, self._observation_started = time.monotonic(), None
            with self._lock:
                self._report = {
                    'status': 'starting', 'result': 'not_validated', 'active': True,
                    'profile': profile, 'channel': 0, 'stream': PROFILES[profile][1], 'mode': 0,
                    'profile_evidence': ('observed_control_session' if profile == 'control'
                                         else 'sdk_long_connection_candidate'),
                    'duration_seconds': duration, 'keepalive_interval_seconds': KEEPALIVE_INTERVAL,
                    'login_accepted': False, 'reason': None, 'error_type': None,
                    'last_stage': 'discovering', 'elapsed_ms': 0,
                    'top_level_counts': {}, 'inner_tlv_counts': {}, 'alarm_type_counts': {},
                    'events': [], 'events_dropped': 0, 'markers': [], 'decode_failures': 0,
                    'alarm_candidates': 0, 'cleanup': None,
                    'ring_events_emitted': 0,
                }
            self._task = asyncio.create_task(self._run_owned(profile, duration),
                                             name='welcomeeye-v1-doorbell-trial')
            reserved = False  # The task now owns both locks through cleanup.
        finally:
            if reserved:
                self.hub.lock.release()
            if self._task is None or self._task.done():
                control_lock.release()
        self._notify()
        try:
            async with asyncio.timeout(STARTUP_BUDGET + 1):
                await self._ready.wait()
        except TimeoutError:
            await self.stop()
            with self._lock:
                self._report.update(status='failed', reason='startup_timeout')
        except asyncio.CancelledError:
            try:
                await self.stop()
            except asyncio.CancelledError:
                pass
            raise
        return self.snapshot()

    def mark(self):
        with self._lock:
            if not self.active or self._report.get('status') != 'observing':
                return self._denied('not_observing')
            if len(self._report['markers']) >= MAX_MARKERS:
                return self._denied('marker_limit')
            self._report['markers'].append({'sequence': len(self._report['markers']) + 1,
                                            'elapsed_ms': self._elapsed()})
        self._notify()
        return self.snapshot()

    async def stop(self):
        task = self._task
        if task is not None and not task.done():
            self._cancel_requested.set()
            session = self._session
            if session is not None:
                session.cancel()
            await _finish_task(task, cancel_on_cancel=False)
        return self.snapshot()

    async def close(self):
        self._closed = True
        return await self.stop()

    async def _run_owned(self, profile, duration):
        worker = asyncio.create_task(asyncio.to_thread(self._run, profile, duration))
        try:
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                self._cancel_requested.set()
                if self._session is not None:
                    self._session.cancel()
                try:
                    await _finish_task(worker, cancel_on_cancel=False)
                except asyncio.CancelledError:
                    pass
                raise
        finally:
            self.hub.lock.release()
            self.hub.control.lock.release()
            self._ready.set()
            self._notify()

    def _count(self, name, value):
        counts = self._report[name]
        key = str(value)
        if key not in counts and len(counts) >= MAX_COUNTER_KEYS - 1:
            key = 'other'
        counts[key] = counts.get(key, 0) + 1

    def _event(self, **fields):
        if len(self._report['events']) < MAX_EVENTS:
            self._report['events'].append({'elapsed_ms': self._elapsed(), **fields})
        else:
            self._report['events_dropped'] += 1

    def _parts(self, parts, authenticated):
        with self._lock:
            for kind, body in parts:
                self._count('top_level_counts', kind)
                self._event(kind=kind, length=len(body), phase='observation' if authenticated else 'login')
                if not authenticated or kind != 510:
                    continue
                try:
                    observations, inner_counts = decode_alarm_observations(self._session.info.uid, kind, body)
                    for inner_kind, count in inner_counts.items():
                        for _ in range(count):
                            self._count('inner_tlv_counts', inner_kind)
                    for item in observations:
                        alarm_type = item.alarm_type if 0 <= item.alarm_type <= 65535 else 'other'
                        self._count('alarm_type_counts', alarm_type)
                        self._report['alarm_candidates'] += 1
                        self._event(kind=510, inner_kind=14854, alarm_type=alarm_type,
                                    identity_complete=item.message is not None)
                except (ProtocolError, ValueError, TypeError, UnicodeError, struct.error, RecursionError):
                    self._report['decode_failures'] += 1

    def _run(self, profile, duration):
        session = None
        cleanup = {'session_stop_attempted': False, 'session_stop_sent': False,
                   'tcp_closed': False, 'error_types': []}
        try:
            data = self.hub.entry.data
            channel, stream, mode = PROFILES[profile]
            session = _TrialSession(data['host'], data['username'], data['password'], channel,
                                    stream=stream, mode=mode, on_parts=self._parts,
                                    cancel_event=self._cancel_requested)
            self._session = session
            session.expected_uid = getattr(self.hub.entry, 'unique_id', None)
            if self._cancel_requested.is_set():
                raise ConnectionAbortedError('Trial cancelled before connection')
            session.connect()
            if self._cancel_requested.is_set():
                raise ConnectionAbortedError('Trial cancelled during connection')
            session.begin_observation(duration)
            with self._lock:
                self._observation_started = time.monotonic()
                self._report.update(status='observing', login_accepted=True, last_stage='observing')
            self.hub.loop.call_soon_threadsafe(self._ready.set)
            self._notify()
            while time.monotonic() < session.network_deadline:
                session.read()
            with self._lock:
                self._report.update(status='completed', reason='duration_elapsed')
        except Exception as exc:
            with self._lock:
                if self._cancel_requested.is_set():
                    self._report.update(status='stopped', reason='manual_or_unload_stop')
                elif ((isinstance(exc, TimeoutError)
                       or (isinstance(exc, ProtocolError) and isinstance(exc.__cause__, TimeoutError)))
                      and session is not None
                      and session.authenticated and self._observation_started is not None
                      and time.monotonic() >= session.network_deadline):
                    self._report.update(status='completed', reason='duration_elapsed')
                else:
                    self._report.update(status='failed', reason='receive_limit' if isinstance(exc, _ReceiveLimit)
                                        else 'session_failed', error_type=type(exc).__name__)
                    if session is not None:
                        self._report['last_stage'] = ('observing' if self._observation_started is not None
                                                       else session.connection_stage)
        finally:
            if session is not None:
                session.begin_cleanup()
                if session.authenticated and session.sock is not None:
                    cleanup['session_stop_attempted'] = True
                    try:
                        session.send_session_stop()
                        cleanup['session_stop_sent'] = True
                    except Exception as exc:
                        cleanup['error_types'].append(type(exc).__name__)
                try:
                    session.close()
                except Exception as exc:
                    cleanup['error_types'].append(type(exc).__name__)
                cleanup['tcp_closed'] = session.closed.is_set()
            with self._lock:
                self._report.update(cleanup=cleanup, elapsed_ms=self._elapsed(),
                                    total_elapsed_ms=round((time.monotonic() - self._started) * 1000))
                if session is not None:
                    self._report.update(received_bytes=session.received_bytes,
                                        login_accepted=session.authenticated,
                                        received_frames=session.read_count, zero_frames=session.zero_frame_count,
                                        invalid_frames=getattr(session, 'invalid_frame_count', 0),
                                        last_frame_size=getattr(session, 'last_frame_size', None),
                                        keepalive_count=session.keepalive_count,
                                        partial_frame_at_end=session.partial_frame_at_end,
                                        requests_attempted={str(k): v for k, v in session.sent_counts.items()})
            self._session = None
