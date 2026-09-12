"""Shared media session that exists only while a consumer holds a lease."""
import asyncio
from collections import deque
import logging
import secrets
import threading
import time

from .client import AuthenticationError, Session
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .control import DeviceController
from .media import (MediaPipeline, StreamFormat, inspect_h264_packet,
                    normalize_h264_packet)
from .protected import ProtocolError
from .ring import RingListener

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
_VIDEO_TLVS = (97, 99, 100, 101)


def _safe_error_message(exc):
    if isinstance(exc, (ProtocolError, ValueError, RuntimeError, TimeoutError)):
        return str(exc)
    return None


class WelcomeEyeHub:
    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.control = DeviceController(self)
        self.loop = asyncio.get_running_loop()
        self.ring_listener = RingListener(self.loop, entry, self._ring, self._ring_state)
        self.ring_connected = self.ringing = False
        self.ring_error = self.ring_timer = None
        self.ring_count = 0
        self.connected = False
        self.connection_count = 0
        self.image = self.format = self.error = None
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
        self.path = '/' + secrets.token_urlsafe(32) + '/live.ts'
        self.url = None
        self.media_tlv_counts = {97: 0, 98: 0, 99: 0, 100: 0, 101: 0, 203: 0}
        self.last_media_tlv = None
        self.video_packets_received = 0
        self.selected_video_tlv = None
        self.h264_detected_counts = {97: 0, 99: 0, 100: 0, 101: 0}
        self.h264_idr_counts = {97: 0, 99: 0, 100: 0, 101: 0}
        self.h264_nal_types = set()
        self.h264_framing_counts = {}
        self.selected_media_profile = None
        self.profile_attempts = 0
        self.current_profile = None
        self.last_error_message = None
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
        self.ring_timer = self.loop.call_later(3, self._clear_ring)
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
        try:
            async with self.lock:
                if self.stopped:
                    raise ConnectionError('Integration is stopped')
                if isinstance(self.error, AuthenticationError):
                    raise self.error
                self.consumers.add(consumer)
                if not self.thread or not self.thread.is_alive():
                    self.generation += 1
                    self.stop_event = threading.Event()
                    self.ready.clear()
                    self.error = None
                    self.thread = threading.Thread(
                        target=self._worker,
                        args=(self.generation, self.stop_event),
                        name='welcomeeye-media', daemon=True,
                    )
                    self.thread.start()
            await asyncio.wait_for(self.ready.wait(), 22)
            if self.error:
                raise self.error
            if not self.connected:
                raise ConnectionError('Video session is unavailable')
        except BaseException:
            await self.release(consumer)
            raise

    async def release(self, consumer):
        async with self.lock:
            self.consumers.discard(consumer)
            if not self.consumers:
                await self._halt_media()

    async def _halt_media(self):
        self.stop_event.set()
        self.generation += 1
        if self.session:
            self.session.close()
        if self.thread:
            await asyncio.to_thread(self.thread.join, 15)
            if self.thread.is_alive():
                raise RuntimeError('Media worker did not stop')
            self.thread = None
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
            # Passive thumbnails retain the last still and never reopen video.
            self.buffer.clear()
            self.buffer_size = 0
            for queue in tuple(self.queues):
                self._end_queue(queue)
        self.ready.set()
        self._notify()

    def _image(self, data):
        self.image = data

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
            await self.release(task)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            self.handlers.discard(task)

    def _profile_order(self):
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
        if not info.detected or kind not in self.h264_detected_counts:
            return
        self.h264_detected_counts[kind] += 1
        if info.keyframe:
            self.h264_idr_counts[kind] += 1
        self.h264_nal_types.update(info.nal_types)
        if info.framing:
            self.h264_framing_counts[info.framing] = (
                self.h264_framing_counts.get(info.framing, 0) + 1
            )

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

                try:
                    parts = session.connect()
                    session.sock.settimeout(_PROFILE_VIDEO_WAIT)
                    deadline = time.monotonic() + _PROFILE_VIDEO_WAIT

                    while not stop_event.is_set():
                        for kind, body in parts:
                            if kind in self.media_tlv_counts:
                                self.media_tlv_counts[kind] += 1
                                self.last_media_tlv = kind

                            if kind == 203:
                                fmt = StreamFormat.parse(body)
                                self.last_announced_format = fmt
                                emit(self._observe_device_model, fmt)
                                deadline = time.monotonic() + _PROFILE_VIDEO_WAIT
                                if pipeline:
                                    pipeline.close()
                                pipeline = MediaPipeline(
                                    fmt,
                                    lambda data: emit(self._ts, data),
                                    lambda data: emit(self._image, data),
                                    lambda media_kind, frame: emit(
                                        self._frame, media_kind, frame
                                    ),
                                )
                                continue

                            if not pipeline:
                                continue

                            if kind == 98:
                                pipeline.feed(kind, body)
                                continue

                            if kind not in _VIDEO_TLVS:
                                continue

                            info = inspect_h264_packet(body)
                            self._record_h264(kind, info)

                            # Connect 2 semantics remain authoritative for 100/101.
                            # Legacy 97/99 are promoted to video only when their
                            # payload structurally identifies as H.264.
                            if kind in (97, 99) and not info.detected:
                                continue

                            keyframe = kind == 100 or info.keyframe
                            self.video_packets_received += 1
                            video_body = normalize_h264_packet(body, info.framing)
                            accepted = pipeline.feed(
                                kind, video_body, keyframe=keyframe
                            )
                            if accepted and pipeline.started and not found_video:
                                found_video = True
                                self.selected_video_tlv = kind
                                self.selected_media_profile = dict(self.current_profile)
                                emit(self._observe_device_model, fmt, kind)
                                emit(self._state, True, fmt)
                                delay = 2
                                session.sock.settimeout(10)

                        if stop_event.is_set():
                            return
                        if found_video:
                            parts = session.read()
                            continue
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                f'No H264 video packets for media profile {name}'
                            )
                        parts = session.read()

                except AuthenticationError as exc:
                    emit(self._state, False, None, exc)
                    return
                except Exception as exc:
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
                    session.close()
                    self.session = None
                    if pipeline:
                        try:
                            pipeline.close()
                        except Exception:
                            pass

                if found_video:
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

    async def stop(self):
        self.stopped = True
        self.ring_listener.close()
        if self.ring_timer:
            self.ring_timer.cancel()
            self.ring_timer = None
        self.ringing = self.ring_connected = False
        if self.ring_listener.thread:
            await asyncio.to_thread(self.ring_listener.thread.join, 15)
            if self.ring_listener.thread.is_alive():
                _LOGGER.error('Doorbell listener did not stop within 15 seconds')
        self.control.close()
        for close in tuple(self.close_listeners):
            await close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for task in tuple(self.handlers):
            task.cancel()
        if self.handlers:
            await asyncio.gather(*tuple(self.handlers), return_exceptions=True)
        async with self.lock:
            self.consumers.clear()
            await self._halt_media()
