"""Shared media session that exists only while a consumer holds a lease."""
import asyncio
from collections import deque
import logging
import secrets
import threading
import time

from .client import AuthenticationError, Session, V1IdleTimeout, discovery_diagnostics
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, RING_HOLD_SECONDS
from .lifecycle import exit_reason, new_lifecycle
from .control import DeviceController
from .media import (MediaPipeline, StreamFormat, inspect_h264_packet,
                    normalize_h264_packet)
from .protected import (START_AV_RESPONSE, STOP_AV_RESPONSE, ProtocolError,
                        decode_private_reply, decode_start_av_reply, decode_stop_av_reply,
                        parse_tlvs)
from .ring import RingListener
from .v1_video_diagnostics import V1VideoDiagnostics
from .v1_video import V1VideoReceiver
from .talkback import Talkback

_LOGGER = logging.getLogger(__name__)

# Profiles observed in the WelcomeEye/Qv SDK. The Connect 2 path remains first;
# compatibility profiles are tried only when a stream format is announced but no
# usable video follows. No output/open command is ever sent here.
_MEDIA_PROFILES = (
    ("connect2", 16, 1, 2),
    ("compat_default", 1, 1, 2),
    ("legacy_cloud", 1, 1, 1),
    ("compat_channel0", 0, 1, 2),
    ("legacy_channel0", 0, 1, 1),
)
_PROFILE_VIDEO_WAIT = 3.0
_V1_VIDEO_WAIT = 12.0
_LIVE_READ_TIMEOUT = 10.0
_VIDEO_TLVS = (97, 99, 100, 101)


def _safe_error_message(exc):
    # Library exceptions can contain endpoints or credentials. Export stages
    # and types instead of attempting to redact arbitrary exception text.
    return None


class WelcomeEyeHub:
    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.control = DeviceController(self)
        self.loop = asyncio.get_running_loop()
        self.talkback = Talkback(self)
        self._last_v1_stop_started = float('-inf')
        self.ring_listener = RingListener(self.loop, entry, self._ring, self._ring_state)
        self.ring_connected = self.ringing = False
        self.ring_error = self.ring_timer = None
        self.ring_count = 0
        self.connected = False
        self.connection_count = 0
        self.image = self.format = self.error = None
        self.image_generation = 0
        self.image_event = asyncio.Event()
        self.snapshot_requests = 0
        self.snapshot_successes = 0
        self.snapshot_timeouts = 0
        self.snapshot_errors = 0
        self.snapshot_started_media = 0
        self.snapshot_reused_media = 0
        self.snapshot_wait_elapsed_ms = 0
        self.snapshot_last_error_type = None
        self.last_announced_format = None
        self.device_model = entry.data.get('detected_model', 'WelcomeEye')
        self.device_model_confidence = entry.data.get('detected_model_confidence', 'unknown')
        self.device_model_source = entry.data.get('detected_model_source')
        self.listeners, self.frame_listeners, self.queues = set(), set(), set()
        self.buffer, self.buffer_size = deque(), 0
        self.stop_event = threading.Event()
        self.ready = asyncio.Event()
        self.lock = asyncio.Lock()
        self.session = self.server = self.thread = None
        self.handlers, self.consumers, self.close_listeners = set(), set(), set()
        self.generation = 0
        self.stopped = True
        self._stop_task = None
        self.release_reason = None
        self.lifecycle = new_lifecycle()
        self.previous_lifecycles = deque(maxlen=3)
        self.last_media_lifecycle = None
        self.codec_diagnostics = {}
        self.codec_totals = {}
        self.acquire_diagnostics = {}
        self.shutdown_diagnostics = {}
        self.worker_stage = 'idle'
        self.path = '/' + secrets.token_urlsafe(32) + '/live.ts'
        self.url = None
        self.media_tlv_counts = {97: 0, 98: 0, 99: 0, 100: 0, 101: 0, 203: 0}
        self.media_all_tlv_counts = {}
        self.last_media_tlv = None
        self.video_packets_received = 0
        self.selected_video_tlv = None
        self.h264_detected_counts = {97: 0, 99: 0, 100: 0, 101: 0}
        self.h264_idr_counts = {97: 0, 99: 0, 100: 0, 101: 0}
        self.h264_any_tlv_counts = {}
        self.h264_any_idr_counts = {}
        self.h264_nal_types = set()
        self.h264_framing_counts = {}
        self.selected_media_profile = None
        self.profile_attempts = 0
        self.current_profile = None
        self.last_error_message = None
        self.media_framing_diagnostics = {}
        self.v1_video_diagnostics = None
        self.v1_video_previous_sessions = deque(maxlen=2)
        self.v1_apk_profile_used = False
        self.lt_apk_profile_attempts = 0
        self.lt_start_av_request_sent = 0
        self.lt_start_av_request_errors = 0
        self.lt_start_av_response_count = 0
        self.lt_start_av_decode_failures = 0
        self.lt_start_av_result = None
        self.lt_start_av_reply_reserved = None
        self.lt_stop_av_request_sent = 0
        self.lt_stop_av_request_errors = 0
        self.lt_stop_av_response_count = 0
        self.lt_stop_av_decode_failures = 0
        self.lt_stop_av_result = None
        self.lt_query_stream_mode_sent = 0
        self.lt_private_request_errors = 0
        self.lt_private_response_count = 0
        self.lt_private_decode_failures = 0
        self.lt_last_manu_command = None
        self.lt_last_manu_subcommand = None
        self.lt_stream_mode_wire = None
        self.lt_stream_mode_app = None
        self.webrtc_diagnostics = {
            'stage': 'idle',
            'failed_at_stage': None,
            'last_exception_type': None,
            'connection_state': None,
            'requested_tracks': [],
            'created_tracks': [],
            'active_viewers': 0,
            'candidate_event': None,
        }

    async def start(self):
        # Config flow validates credentials. Startup must not seize video.
        self.server = await asyncio.start_server(self._serve, '127.0.0.1', 0, limit=8192)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f'http://127.0.0.1:{port}{self.path}'
        self.stopped = False
        self.ring_listener.start()


    def _observe_device_model(self, fmt, video_tlv=None):
        """Infer model from validated media signatures and update HA's device registry."""
        model = None
        confidence = None
        source = None

        # Community-tested signatures: Connect V1 announces CIF 352x288, while
        # the validated Connect 2 unit announces 720x576 on the same live profile.
        if fmt.width == 352 and fmt.height == 288:
            model = 'WelcomeEye Connect V1'
            confidence = 'high' if video_tlv in (97, 99) else 'probable'
            source = 'media_352x288' if video_tlv is None else f'media_352x288_tlv{video_tlv}'
        elif fmt.width == 720 and fmt.height == 576:
            model = 'WelcomeEye Connect 2'
            confidence = 'high' if video_tlv in (100, 101) else 'probable'
            source = 'media_720x576' if video_tlv is None else f'media_720x576_tlv{video_tlv}'

        if not model:
            return
        changed = (model != self.device_model or confidence != self.device_model_confidence)
        self.device_model = model
        self.device_model_confidence = confidence
        self.device_model_source = source

        data = dict(self.entry.data)
        data_changed = (
            data.get('detected_model') != model
            or data.get('detected_model_confidence') != confidence
            or data.get('detected_model_source') != source
        )
        if data_changed:
            data['detected_model'] = model
            data['detected_model_confidence'] = confidence
            data['detected_model_source'] = source
        default_titles = {'WelcomeEye Connect 2', 'WelcomeEye', 'Philips WelcomeEye'}
        title = model if self.entry.title in default_titles else self.entry.title
        if data_changed or title != self.entry.title:
            self.hass.config_entries.async_update_entry(
                self.entry, data=data, title=title
            )

        registry = dr.async_get(self.hass)
        device = registry.async_get_device_by_identifier(
            (DOMAIN, self.entry.unique_id), self.entry.entry_id
        )
        if device and device.model != model:
            registry.async_update_device(device.id, model=model)
        if changed:
            self._notify()

    def _ring_state(self, connected, error):
        self.ring_connected, self.ring_error = connected, error
        self._notify()

    def _ring(self, message):
        if self.stopped:
            return
        self.ring_count += 1
        self.ringing = True
        if self.ring_timer:
            self.ring_timer.cancel()
        self.ring_timer = self.loop.call_later(RING_HOLD_SECONDS, self._clear_ring)
        self.hass.bus.async_fire('welcomeeye_local.ring', {
            'entry_id': self.entry.entry_id,
            'channel': message.channel,
        })
        self._notify()

    def _clear_ring(self):
        self.ringing = False
        self.ring_timer = None
        self._notify()

    def subscribe(self, listener):
        self.listeners.add(listener)
        return lambda: self.listeners.discard(listener)

    def _notify(self):
        for listener in tuple(self.listeners):
            listener()

    async def acquire(self, consumer):
        started = time.monotonic()
        lease_added = False
        details = {'media_acquired': False, 'reused_worker': False,
                   'wait_elapsed_ms': None, 'cleanup_elapsed_ms': 0,
                   'error_type': None, 'connection_stage_at_failure': None}
        try:
            async with self.lock:
                if self.stopped:
                    raise ConnectionError('Integration is stopped')
                if isinstance(self.error, AuthenticationError):
                    raise self.error
                if self.thread and self.thread.is_alive() and self.stop_event.is_set():
                    raise ConnectionError('Previous media worker is still stopping')
                self.consumers.add(consumer)
                lease_added = True
                details['reused_worker'] = bool(self.thread and self.thread.is_alive())
                if not self.thread or not self.thread.is_alive():
                    self.generation += 1
                    self.stop_event = threading.Event()
                    self.release_reason = None
                    self.ready.clear()
                    self.error = None
                    self.thread = threading.Thread(
                        target=self._worker,
                        args=(self.generation, self.stop_event),
                        name='welcomeeye-media', daemon=True,
                    )
                    self.thread.start()
            await asyncio.wait_for(self.ready.wait(), 38)
            if self.stopped:
                raise ConnectionError('Integration is stopped')
            if self.error:
                raise self.error
            if not self.connected:
                raise ConnectionError('Video session is unavailable')
            details['media_acquired'] = True
            details['wait_elapsed_ms'] = round((time.monotonic() - started) * 1000)
            return details['reused_worker']
        except BaseException as exc:
            details['wait_elapsed_ms'] = round((time.monotonic() - started) * 1000)
            details['error_type'] = type(exc).__name__
            details['connection_stage_at_failure'] = (
                self.session.connection_stage if self.session else
                self.media_framing_diagnostics.get('connection_stage')
            )
            cleanup_started = time.monotonic()
            try:
                if lease_added:
                    await self.release(consumer)
            finally:
                details['cleanup_elapsed_ms'] = round((time.monotonic() - cleanup_started) * 1000)
            raise
        finally:
            self.acquire_diagnostics = details

    async def release(self, consumer, *, reason='explicit_consumer_release'):
        async with self.lock:
            # Failed acquisitions and competing cleanup callbacks may release
            # the same lease again. Do not re-run a failed join for a non-owner.
            if consumer not in self.consumers:
                return
            self.consumers.discard(consumer)
            if not self.consumers:
                if not self.stopped:
                    self.release_reason = reason
                await self._halt_media()

    async def _halt_media(self):
        started = time.monotonic()
        self.shutdown_diagnostics = {'stage': 'interrupting_read', 'error_type': None,
                                     'worker_stage': self.worker_stage, 'elapsed_ms': 0}
        self.stop_event.set()
        self.generation += 1
        # Revoke readiness before a join that can fail. A live stopping worker
        # must never masquerade as reusable media or accept a new output lease.
        self._state(False)
        if self.session:
            # Do not tear down the write side immediately: the media worker's
            # finally block must still be able to send native Stop AV (5009).
            self.session.interrupt_read()
        if self.thread:
            self.shutdown_diagnostics['stage'] = 'joining_worker'
            await asyncio.to_thread(self.thread.join, 15)
            self.shutdown_diagnostics['elapsed_ms'] = round((time.monotonic() - started) * 1000)
            if self.thread.is_alive():
                self.shutdown_diagnostics.update(stage='worker_stop_timeout', error_type='RuntimeError',
                                                  worker_stage=self.worker_stage)
                raise RuntimeError('Media worker did not stop')
            self.thread = None
        self.shutdown_diagnostics.update(stage='stopped', elapsed_ms=round((time.monotonic() - started) * 1000))
        self._state(False)

    def _dispatch(self, generation, callback, *args):
        if generation == self.generation and not self.stopped:
            callback(*args)

    def _state(self, connected, fmt=None, error=None):
        if connected and not self.connected:
            self.connection_count += 1
        self.connected, self.error = connected, error
        self.last_error_message = _safe_error_message(error) if error else None
        if fmt:
            self.format = fmt
        if not connected:
            # Keep the last still for display state, never as a fresh snapshot.
            self.image_event.set()
            self.buffer.clear()
            self.buffer_size = 0
            for queue in tuple(self.queues):
                self._end_queue(queue)
        self.ready.set()
        self._notify()

    def _image(self, data):
        self.image = data
        self.image_generation += 1
        self.image_event.set()

    async def wait_for_image(self, after_generation, timeout):
        """Wait for a JPEG newer than ``after_generation`` without stale fallback."""
        deadline = time.monotonic() + timeout
        while True:
            if self.stopped:
                raise ConnectionError('Integration is stopped')
            if self.error or not self.connected:
                raise ConnectionError('Video session is unavailable')
            if self.image_generation > after_generation:
                return self.image
            self.image_event.clear()
            # All image/state callbacks run on this loop. No await separates
            # the checks from clear(), so another waiter cannot lose a wakeup.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(self.image_event.wait(), remaining)
            except asyncio.TimeoutError:
                return None

    def _frame(self, kind, frame):
        for listener in tuple(self.frame_listeners):
            listener(kind, frame)

    @staticmethod
    def _end_queue(queue):
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(None)

    def _ts(self, data):
        if not self.connected:
            return
        now = time.monotonic()
        self.buffer.append((now, data))
        self.buffer_size += len(data)
        while self.buffer and (
            self.buffer_size > 1024 * 1024 or self.buffer[0][0] < now - 3
        ):
            self.buffer_size -= len(self.buffer.popleft()[1])
        for queue in tuple(self.queues):
            if queue.full():
                self._end_queue(queue)
                self.queues.discard(queue)
            else:
                queue.put_nowait(data)

    async def _serve(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        queue = eof = next_chunk = None
        try:
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
            line = header.split(b'\r\n', 1)[0]
            if line not in (
                f'GET {self.path} HTTP/1.1'.encode(),
                f'GET {self.path} HTTP/1.0'.encode(),
            ):
                writer.write(b'HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n')
                await writer.drain()
                return
            if self.stopped or len(self.handlers) > 4:
                writer.write(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n')
                await writer.drain()
                return
            await self.acquire(task)
            writer.write(
                b'HTTP/1.1 200 OK\r\nContent-Type: video/mp2t\r\n'
                b'Cache-Control: no-store\r\nConnection: close\r\n\r\n'
            )
            queue = asyncio.Queue(maxsize=256)
            self.queues.add(queue)
            for _, chunk in self.buffer:
                writer.write(chunk)
            await asyncio.wait_for(writer.drain(), 5)
            eof = asyncio.create_task(reader.read(1))
            while True:
                next_chunk = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    (next_chunk, eof), timeout=15,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if eof in done or next_chunk not in done:
                    break
                chunk = next_chunk.result()
                if chunk is None:
                    break
                writer.write(chunk)
                await asyncio.wait_for(writer.drain(), 5)
        except (
            OSError, TimeoutError, AuthenticationError,
            asyncio.IncompleteReadError, asyncio.LimitOverrunError,
        ):
            pass
        finally:
            pending = [t for t in (eof, next_chunk) if t is not None]
            for task_item in pending:
                task_item.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if queue is not None:
                self.queues.discard(queue)
            try:
                await self.release(task, reason='http_stream_release')
            finally:
                try:
                    writer.close()
                    await asyncio.wait_for(writer.wait_closed(), 5)
                except (OSError, TimeoutError) as exc:
                    _LOGGER.debug('HTTP stream close failed (%s)', type(exc).__name__)
                finally:
                    self.handlers.discard(task)

    def _profile_order(self):
        # QvLtPlayerCore.startPlaying() uses logical channel + 15,
        # stream 1, mode 2. Once a V1 is identified, do not churn
        # through speculative channel/mode variants.
        if self.device_model == 'WelcomeEye Connect V1':
            return [('lt_apk_v1', 16, 1, 2)]
        profiles = list(_MEDIA_PROFILES)
        configured_channel = self.entry.data.get('channel', 16)
        configured = ('configured', configured_channel, 1, 2)
        if configured_channel not in {item[1] for item in profiles}:
            profiles.insert(0, configured)
        if self.selected_media_profile:
            selected = tuple(
                self.selected_media_profile[key]
                for key in ('name', 'channel', 'stream', 'mode')
            )
            profiles = [selected] + [item for item in profiles if item != selected]
        return profiles

    def _record_h264(self, kind, info):
        if not info.detected:
            return
        self.h264_any_tlv_counts[kind] = self.h264_any_tlv_counts.get(kind, 0) + 1
        if info.keyframe:
            self.h264_any_idr_counts[kind] = self.h264_any_idr_counts.get(kind, 0) + 1
        if kind in self.h264_detected_counts:
            self.h264_detected_counts[kind] += 1
            if info.keyframe:
                self.h264_idr_counts[kind] += 1
        self.h264_nal_types.update(info.nal_types)
        if info.framing:
            self.h264_framing_counts[info.framing] = (
                self.h264_framing_counts.get(info.framing, 0) + 1
            )

    def _send_lt_start_av(self, session):
        try:
            session.send_start_av()
        except Exception as exc:
            self.lt_start_av_request_errors += 1
            _LOGGER.debug('WelcomeEye LT native Start AV failed (%s)', type(exc).__name__)
            return False
        self.lt_start_av_request_sent += 1
        return True

    def _send_lt_stop_av(self, session):
        try:
            session.send_stop_av()
        except Exception as exc:
            self.lt_stop_av_request_errors += 1
            _LOGGER.debug('WelcomeEye LT native Stop AV failed (%s)', type(exc).__name__)
            return False
        self.lt_stop_av_request_sent += 1
        return True

    def _record_lt_stop_av_response(self, session, body):
        try:
            _device_time, result, _reserved = decode_stop_av_reply(
                session.info.uid, session.encryption_profile, body
            )
        except (ProtocolError, ValueError, TypeError):
            self.lt_stop_av_decode_failures += 1
            return
        self.lt_stop_av_response_count += 1
        self.lt_stop_av_result = result
        self.lifecycle.update(stop_av_response_received=True, stop_av_result=result)

    def _send_lt_request(self, session, payload, request_name):
        try:
            session.send_manufacturer(payload)
        except Exception as exc:
            self.lt_private_request_errors += 1
            _LOGGER.debug('WelcomeEye LT %s request failed (%s)', request_name, type(exc).__name__)
            return False
        if request_name == 'query_stream_mode':
            self.lt_query_stream_mode_sent += 1
        return True

    def _record_lt_start_av_response(self, session, body):
        try:
            device_time, result, reserved = decode_start_av_reply(
                session.info.uid, session.encryption_profile, body
            )
        except (ProtocolError, ValueError, TypeError):
            self.lt_start_av_decode_failures += 1
            return
        self.lt_start_av_response_count += 1
        self.lt_start_av_result = result
        self.lt_start_av_reply_reserved = reserved
        if device_time > 0:
            session.device_time = device_time
            session.clock_received = time.monotonic()

    def _record_lt_private_response(self, session, body):
        try:
            _device_time, data = decode_private_reply(session.info.uid, body)
        except (ProtocolError, ValueError, TypeError):
            self.lt_private_decode_failures += 1
            return
        self.lt_private_response_count += 1
        candidates = [data]
        if len(data) >= 8 and int.from_bytes(data[:4], 'big') == len(data) - 4:
            try:
                candidates.extend(payload for _kind, payload in parse_tlvs(data[8:]))
            except ProtocolError:
                pass
        for payload in candidates:
            if len(payload) < 4 or payload[0] != 1 or payload[1] < 4:
                continue
            command, subcommand = payload[2], payload[3]
            self.lt_last_manu_command = command
            self.lt_last_manu_subcommand = subcommand
            if command == 3:
                self.lt_stream_mode_wire = subcommand
                self.lt_stream_mode_app = {0: 0, 2: 1, 1: 2}.get(subcommand)
                break

    def _observe_v1_media(self, session):
        if self.v1_video_diagnostics is not None:
            self.v1_video_previous_sessions.append(self.v1_video_diagnostics.snapshot())
        self.v1_video_diagnostics = V1VideoDiagnostics()
        session.media_observer = self.v1_video_diagnostics

    def _record_codec(self, pipeline):
        if pipeline is not None:
            self.codec_diagnostics = {
                key: getattr(pipeline, key, 0) for key in (
                    'video_decode_errors', 'video_decoder_resets',
                    'video_dropped_until_keyframe', 'video_waiting_for_keyframe',
                    'decoded_video_pts',
                )
            }
            previous = getattr(pipeline, '_reported_counters', {})
            for key in ('video_decode_errors', 'video_decoder_resets', 'video_dropped_until_keyframe'):
                value = self.codec_diagnostics[key]
                self.codec_totals[key] = self.codec_totals.get(key, 0) + max(0, value - previous.get(key, 0))
            pipeline._reported_counters = dict(self.codec_diagnostics)

    def _finish_session(self, session, pipeline, start_av_sent, is_v1):
        """Worker-only, bounded writes; every cleanup step attempted exactly once."""
        diag = self.lifecycle
        if is_v1:
            self._last_v1_stop_started = time.monotonic()

        def attempt(stage, callback):
            self.worker_stage = 'cleanup_' + stage
            try:
                callback()
                return True
            except Exception as exc:
                diag['cleanup_errors'].append({'stage': stage, 'type': type(exc).__name__})
                _LOGGER.warning('WelcomeEye media cleanup failed (%s at %s)', type(exc).__name__, stage)
                return False

        # Resolve on this original session, before any reconnect can start.
        attempt('stop_talk', lambda: self.talkback.media_closed(session))
        attempt('pending_output', lambda: self.control.v1_media.media_closed(session))
        if start_av_sent:
            diag['stage'] = 'av_stopping'
            diag['stop_av_attempted'] = True
            if attempt('stop_write_timeout', lambda: session.sock.settimeout(2)):
                diag['stop_av_sent'] = self._send_lt_stop_av(session)
        if is_v1 and session.info is not None and session.sock is not None:
            diag['stage'] = 'session_stopping'
            diag['session_stop_attempted'] = True
            if attempt('session_stop_timeout', lambda: session.sock.settimeout(2)):
                diag['session_stop_sent'] = attempt('session_stop', lambda: session.send_session_stop())
        def snapshot_transport():
            snapshot = session.framing_diagnostics()
            self.media_framing_diagnostics = snapshot
            diag['transport'] = snapshot
            if session.media_observer is not None:
                diag['v1_video_receive'] = session.media_observer.snapshot()['video_receive']

        attempt('framing_snapshot', snapshot_transport)
        diag['stage'] = 'tcp_closing'
        diag['tcp_close_reason'] = diag['worker_exit_reason']
        diag['tcp_closed'] = attempt('tcp_close', session.close)
        self.session = None
        self._record_codec(pipeline)
        diag['codec'] = dict(self.codec_diagnostics)
        if pipeline:
            attempt('pipeline_close', pipeline.close)
        diag['stage'] = 'closed' if diag['tcp_closed'] else 'close_failed'
        self.worker_stage = diag['stage']
        self.previous_lifecycles.append(dict(diag))
        if diag['video_packets_received']:
            # Discovery failures must not erase the session that lost video.
            self.last_media_lifecycle = dict(diag)

    def _worker(self, generation, stop_event):
        def emit(callback, *args):
            self.loop.call_soon_threadsafe(
                self._dispatch, generation, callback, *args
            )

        delay = 2
        while not stop_event.is_set():
            last_error = None
            found_video = False

            for name, channel, stream, mode in self._profile_order():
                if stop_event.is_set():
                    return

                pipeline = None
                fmt = None
                v1_format = False
                v1_receiver = None
                start_av_attempted = False
                start_av_sent = False
                live_read_deadline = None
                session_started = time.monotonic()
                apk_lt_profile = (channel, stream, mode) == (16, 1, 2)
                session = Session(
                    self.entry.data['host'],
                    self.entry.data['username'],
                    self.entry.data['password'],
                    channel=channel,
                    stream=stream,
                    mode=mode,
                )
                self.session = session
                self.current_profile = {
                    'name': name, 'channel': channel, 'stream': stream, 'mode': mode
                }
                self.profile_attempts += 1
                self.lifecycle = new_lifecycle()
                self.codec_diagnostics = {}
                worker_error = None
                authenticated = False
                stage = self.worker_stage = 'login'
                known_v1 = self.device_model == 'WelcomeEye Connect V1'

                def deliver_frame(kind, frame):
                    self.lifecycle['decoded_' + kind + '_frames'] += 1
                    emit(self._frame, kind, frame)

                if self.device_model == 'WelcomeEye Connect V1':
                    self._observe_v1_media(session)

                try:
                    # QvLtPlayerCore.start(): two seconds since LtVariates'
                    # last stop. Preserve this V1 guard without retrying outputs.
                    reopen_wait = max(0.0, 2.0 - (time.monotonic() - self._last_v1_stop_started)) if known_v1 else 0.0
                    self.lifecycle['reopen_wait_ms'] = round(reopen_wait * 1000)
                    if reopen_wait and stop_event.wait(reopen_wait):
                        return
                    parts = session.connect()
                    authenticated = True
                    known_v1 = self.device_model == 'WelcomeEye Connect V1'
                    if known_v1:
                        session.enable_v1_video_receive()
                        v1_receiver = V1VideoReceiver(self.v1_video_diagnostics)
                    if known_v1 and apk_lt_profile:
                        stage = self.worker_stage = 'av_starting'
                        self.v1_apk_profile_used = True
                        self.lt_apk_profile_attempts += 1
                        start_av_attempted = True
                        start_av_sent = self._send_lt_start_av(session)
                        # The optional protected stream-mode query (509) closes
                        # the tester's V1 transport after a few frames. Format
                        # arrives in 203; video needs only the existing Start AV.
                    wait_time = _V1_VIDEO_WAIT if known_v1 and apk_lt_profile else _PROFILE_VIDEO_WAIT
                    session.sock.settimeout(min(2.0, wait_time))
                    deadline = time.monotonic() + wait_time

                    while not stop_event.is_set():
                        self.worker_stage = 'processing_tlvs'
                        self.talkback.observe(session, parts)
                        self.control.v1_media.observe(session, parts)
                        for kind, body in parts:
                            self.media_all_tlv_counts[kind] = (
                                self.media_all_tlv_counts.get(kind, 0) + 1
                            )
                            if kind in self.media_tlv_counts:
                                self.media_tlv_counts[kind] += 1
                                self.last_media_tlv = kind

                            if kind == START_AV_RESPONSE and (start_av_attempted or known_v1 or v1_format):
                                self._record_lt_start_av_response(session, body)
                                continue

                            if kind == STOP_AV_RESPONSE and start_av_sent:
                                self._record_lt_stop_av_response(session, body)
                                continue

                            if kind == 510 and (known_v1 or v1_format):
                                self._record_lt_private_response(session, body)
                                continue

                            if kind == 203:
                                stage = self.worker_stage = 'pipeline'
                                fmt = StreamFormat.parse(body)
                                self.last_announced_format = fmt
                                emit(self._observe_device_model, fmt)
                                v1_format = fmt.width == 352 and fmt.height == 288
                                if v1_format and session.media_observer is None:
                                    self._observe_v1_media(session)
                                if v1_format:
                                    session.enable_v1_video_receive()
                                    v1_receiver = V1VideoReceiver(self.v1_video_diagnostics)
                                if v1_format and apk_lt_profile:
                                    self.v1_apk_profile_used = True
                                    if not known_v1:
                                        self.lt_apk_profile_attempts += 1
                                    if not start_av_attempted:
                                        start_av_attempted = True
                                        start_av_sent = self._send_lt_start_av(session)
                                    # Keep the same omission when this first
                                    # format response identifies a V1.
                                    deadline = time.monotonic() + _V1_VIDEO_WAIT
                                    session.sock.settimeout(2.0)
                                else:
                                    deadline = time.monotonic() + _PROFILE_VIDEO_WAIT
                                if pipeline:
                                    pipeline.close()
                                pipeline = MediaPipeline(
                                    fmt,
                                    lambda data: emit(self._ts, data),
                                    lambda data: emit(self._image, data),
                                    deliver_frame,
                                    v1_recovery=known_v1 or v1_format,
                                )
                                continue

                            if not pipeline:
                                continue

                            if kind == 98:
                                stage = self.worker_stage = 'decode_audio'
                                pipeline.feed(kind, body)
                                continue

                            if v1_receiver is not None:
                                video = v1_receiver.receive(kind, body)
                                if video is None:
                                    continue
                                video_body, keyframe = video
                                self._record_h264(kind, inspect_h264_packet(video_body))
                            else:
                                # Inspect every non-audio/non-format TLV. The beta 7/8
                                # allow-list could miss a legacy video TLV entirely.
                                info = inspect_h264_packet(body)
                                self._record_h264(kind, info)

                                if kind in (100, 101):
                                    # Connect 2 semantics remain authoritative.
                                    keyframe = kind == 100 or info.keyframe
                                elif kind in (97, 99):
                                    if not info.detected:
                                        continue
                                    keyframe = info.keyframe
                                else:
                                    # Unknown TLVs are promoted only for an identified
                                    # V1 and only when their payload is structurally H.264.
                                    if not (v1_format and info.detected):
                                        continue
                                    keyframe = info.keyframe

                                video_body = normalize_h264_packet(body, info.framing)

                            self.video_packets_received += 1
                            self.lifecycle['video_packets_received'] += 1
                            self.lifecycle['last_video_packet_elapsed_ms'] = round(
                                (time.monotonic() - session_started) * 1000
                            )
                            stage = self.worker_stage = 'decode_video'
                            accepted = pipeline.feed_video(
                                video_body, keyframe=keyframe
                            )
                            self._record_codec(pipeline)
                            if accepted and pipeline.started and not found_video:
                                found_video = True
                                self.selected_video_tlv = kind
                                self.selected_media_profile = dict(self.current_profile)
                                emit(self._observe_device_model, fmt, kind)
                                emit(self._state, True, fmt)
                                delay = 2
                                session.sock.settimeout(_LIVE_READ_TIMEOUT)
                                live_read_deadline = time.monotonic() + _LIVE_READ_TIMEOUT

                        if stop_event.is_set():
                            return
                        self.worker_stage = 'send_pending'
                        self.control.v1_media.send_pending(session)
                        stage = self.worker_stage = 'read'
                        self.lifecycle['stage'] = 'av_active' if found_video else 'av_starting'
                        if found_video:
                            try:
                                parts = session.read()
                            except V1IdleTimeout as exc:
                                # V1 reads poll the socket every 2 s. An untouched
                                # header is safe to resume on this SAME session;
                                # partial reads, EOF and reset remain fatal. Keep
                                # the original 10 s live idle budget, not 2 s.
                                self.lifecycle['live_idle_poll_count'] += 1
                                if time.monotonic() >= live_read_deadline:
                                    raise TimeoutError('V1 media idle deadline exceeded') from exc
                                parts = []
                            else:
                                if parts:
                                    live_read_deadline = time.monotonic() + _LIVE_READ_TIMEOUT
                            continue
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                f'No H264 video packets for media profile {name}'
                            )
                        try:
                            parts = session.read()
                        except TimeoutError:
                            if time.monotonic() >= deadline:
                                raise TimeoutError(
                                    f'No H264 video packets for media profile {name}'
                                )
                            parts = []

                except AuthenticationError as exc:
                    worker_error = exc
                    emit(self._state, False, None, exc)
                    return
                except Exception as exc:
                    worker_error = exc
                    last_error = exc
                    if found_video and not stop_event.is_set():
                        message = _safe_error_message(exc)
                        if message:
                            _LOGGER.warning(
                                'WelcomeEye connection interrupted (%s): %s',
                                type(exc).__name__, message,
                            )
                        else:
                            _LOGGER.warning(
                                'WelcomeEye connection interrupted (%s)',
                                type(exc).__name__,
                            )
                        emit(self._state, False, None, exc)
                    elif not stop_event.is_set():
                        _LOGGER.debug(
                            'WelcomeEye media profile %s did not yield video (%s)',
                            name, type(exc).__name__,
                        )
                finally:
                    if stage == 'login':
                        stage = session.connection_stage.removeprefix('failed_')
                    self.lifecycle.update(
                        session_duration_ms=round((time.monotonic() - session_started) * 1000),
                        connection=session.connection_diagnostics(),
                        worker_exit_reason=exit_reason(worker_error, stage, stop_event.is_set(), self.release_reason, session),
                        worker_exception_type=type(worker_error).__name__ if worker_error else None,
                        worker_exception_stage=stage if worker_error else None,
                        stop_event_set_at_exit=stop_event.is_set(),
                        socket_closed_by_peer=bool(getattr(session, 'remote_eof', False) and not stop_event.is_set()),
                        **self.control.v1_media.pending_snapshot(session),
                    )
                    self._finish_session(session, pipeline, start_av_sent, authenticated and (known_v1 or v1_format))

                if found_video:
                    break
                if v1_format:
                    # The APK gives us one authoritative LT profile. Once a
                    # 352x288 V1 has answered on it, do not hide the failure by
                    # cycling speculative profiles.
                    break

            if stop_event.is_set():
                return
            if not found_video:
                emit(
                    self._state, False, None,
                    last_error or TimeoutError(
                        'No WelcomeEye media profile produced H264 video'
                    ),
                )
            if stop_event.wait(delay):
                return
            delay = min(delay * 2, 60)

    async def stop(self, *, reason='integration_unload'):
        if self._stop_task is None:
            self.release_reason = reason
            self._stop_task = asyncio.create_task(self._stop())
        await asyncio.shield(self._stop_task)

    async def _stop(self):
        self.stopped = True
        self.ready.set()
        self.image_event.set()
        errors = []

        def attempt_sync(callback):
            try:
                callback()
            except Exception as exc:
                errors.append(exc)
                _LOGGER.warning('WelcomeEye shutdown cleanup failed (%s)', type(exc).__name__)

        async def attempt(callback):
            try:
                await callback()
            except Exception as exc:
                errors.append(exc)
                _LOGGER.warning('WelcomeEye shutdown cleanup failed (%s)', type(exc).__name__)
        if self.device_model == 'WelcomeEye Connect V1':
            # Cancel pending output work before waiting for the ring worker.
            attempt_sync(self.control.close)
        attempt_sync(self.ring_listener.close)
        if self.ring_timer:
            self.ring_timer.cancel()
            self.ring_timer = None
        self.ringing = self.ring_connected = False
        if self.ring_listener.thread:
            await attempt(lambda: asyncio.to_thread(self.ring_listener.thread.join, 15))
            if self.ring_listener.thread.is_alive():
                _LOGGER.error('Doorbell listener did not stop within 15 seconds')
                errors.append(RuntimeError('Doorbell listener did not stop'))
        attempt_sync(self.control.close)
        for close in tuple(self.close_listeners):
            await attempt(lambda: asyncio.wait_for(close(), 30))
        if self.server:
            attempt_sync(self.server.close)
            await attempt(lambda: asyncio.wait_for(self.server.wait_closed(), 5))
            self.server = None
        for task in tuple(self.handlers):
            task.cancel()
        if self.handlers:
            await asyncio.gather(*tuple(self.handlers), return_exceptions=True)
        async with self.lock:
            self.consumers.clear()
            await attempt(self._halt_media)
        if errors:
            raise ExceptionGroup('WelcomeEye shutdown cleanup failed', errors)
