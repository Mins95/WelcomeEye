"""On-demand loopback H.264/AAC from the hub's existing media session."""
import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import secrets

from .snapshot import _finish_task

MAX_CLIENTS = 4
HEADER_LIMIT = 8192
HEADER_TIMEOUT = 5.0
ACQUIRE_TIMEOUT = 35.0
FRAME_TIMEOUT = 15.0
WRITE_TIMEOUT = 5.0
MAX_FPS = 30
MAX_VIDEO_FRAMES = 4
MAX_AUDIO_FRAMES = 25
MAX_INPUT_AGE = 0.25
AUDIO_RATE = 48000
MAX_CHUNK_BYTES = 1024 * 1024
MAX_PENDING_CHUNKS = 8
TIME_BASE = Fraction(1, 90000)


class _TransportBuffer:
    """One bounded mux output, drained after every encoded frame."""

    def __init__(self):
        self.data = bytearray()

    def writable(self):
        return True

    def write(self, data):
        if len(self.data) + len(data) > MAX_CHUNK_BYTES:
            raise ValueError('Local video transport chunk outside bounds')
        self.data.extend(data)
        return len(data)

    def take(self):
        data = bytes(self.data)
        self.data.clear()
        return data


class _TransportEncoder:
    """Construct, encode and close on one private executor thread."""

    def __init__(self, *, audio=False):
        import av
        from av.video.reformatter import VideoReformatter

        self.av = av
        self.reformatter = VideoReformatter()
        self.buffer = _TransportBuffer()
        self.container = self.stream = None
        self.audio = None
        self.audio_enabled = audio
        self.audio_status = 'waiting_source' if audio else 'disabled'
        self.copy_video = False
        self._audio_resampler = self._audio_format = None
        self._audio_pts = self._last_audio_at = None
        self.audio_frames = self.audio_errors = 0
        self.silence_frames = 0
        self._pending_keyframe = False
        self.origin = None
        self.last_pts = -1
        self.last_keyframe = -90000

    def _open(self, width, height, received_at, *, native_fps=None):
        if not (0 < width <= 4096 and 0 < height <= 4096):
            raise ValueError('Local video dimensions outside bounds')
        if self.container is None:
            self.copy_video = native_fps is not None
            if not self.copy_video:
                scale = min(1920 / width, 1080 / height, 1)
                width = max(2, int(width * scale) // 2 * 2)
                height = max(2, int(height * scale) // 2 * 2)
            self.container = self.av.open(self.buffer, mode='w', format='mpegts', options={
                'mpegts_flags': 'resend_headers+pat_pmt_at_frames',
                'flush_packets': '1', 'mpegts_copyts': '1',
                'max_interleave_delta': '100000', 'max_delay': '0'})
            self.stream = self.container.add_stream('h264' if self.copy_video else 'libx264',
                rate=Fraction(str(native_fps)) if self.copy_video else MAX_FPS)
            self.stream.width, self.stream.height = width, height
            self.stream.pix_fmt = 'yuv420p'
            self.stream.time_base = TIME_BASE
            self.stream.codec_context.time_base = TIME_BASE
            self.stream.codec_context.thread_count = 1
            if not self.copy_video:
                self.stream.codec_context.gop_size = MAX_FPS
                self.stream.codec_context.max_b_frames = 0
                self.stream.options = {'preset': 'ultrafast', 'tune': 'zerolatency',
                    'crf': '20', 'profile': 'baseline',
                    'x264-params': 'scenecut=0:repeat-headers=1'}
            if self.audio_enabled:
                self.audio = self.container.add_stream('aac', rate=AUDIO_RATE)
                self.audio.layout = 'mono'
                self.audio.bit_rate = 64000
                self.audio.time_base = Fraction(1, AUDIO_RATE)
                self.audio.codec_context.thread_count = 1
            self.origin = received_at

    def _take(self, keyframe=False):
        # Audio/video interleaving can defer output until a subsequent mux.
        # Readers must receive every chunk from the submitted IDR onward.
        self._pending_keyframe |= keyframe
        chunk = self.buffer.take()
        key = self._pending_keyframe if chunk else False
        if chunk:
            self._pending_keyframe = False
        return chunk, key

    def encode(self, frame, received_at, force_keyframe=False):
        self._open(frame.width, frame.height, received_at)
        converted = self.reformatter.reformat(frame, width=self.stream.width,
            height=self.stream.height, format='yuv420p')
        if converted is frame:
            # An identity reformat returns the shared input. Copy its planes
            # before setting encoder timestamps or keyframe requests.
            converted = self.av.VideoFrame(frame.width, frame.height, 'yuv420p')
            for source, target in zip(frame.planes, converted.planes):
                pixels, copied = bytes(source), bytearray(target.buffer_size)
                for row in range(target.height):
                    begin = row * target.line_size
                    source_begin = row * source.line_size
                    copied[begin:begin + target.width] = pixels[source_begin:source_begin + target.width]
                target.update(copied)
        pts = max(self.last_pts + 1, round((received_at - self.origin) * 90000))
        self.last_pts = pts
        converted.pts, converted.time_base = pts, TIME_BASE
        if force_keyframe or pts - self.last_keyframe >= 90000:
            converted.pict_type = self.av.video.frame.PictureType.I
        else:
            converted.pict_type = self.av.video.frame.PictureType.NONE
        keyframe = False
        for packet in self.stream.encode(converted):
            keyframe = keyframe or packet.is_keyframe
            self.container.mux(packet)
        if keyframe:
            self.last_keyframe = pts
        self._fill_audio(received_at)
        return self._take(keyframe)

    def remux(self, frame, received_at):
        """Preserve the validated QV H.264 bitstream without a second encode."""
        if (not frame.is_h264 or not 0 < frame.fps <= 60
                or not isinstance(frame.payload, bytes) or not 0 < len(frame.payload) <= MAX_CHUNK_BYTES):
            raise ValueError('Unsupported native local video format')
        if self.container is None and not frame.is_keyframe:
            return b'', False
        self._open(frame.width, frame.height, received_at, native_fps=frame.fps)
        if not self.copy_video or (frame.width, frame.height) != (self.stream.width, self.stream.height):
            raise ValueError('Local native video format changed')
        packet = self.av.Packet(frame.payload)
        packet.stream = self.stream
        packet.time_base = TIME_BASE
        packet.pts = packet.dts = max(self.last_pts + 1,
            round((received_at - self.origin) * 90000))
        packet.duration = max(1, round(90000 / frame.fps))
        packet.is_keyframe = frame.is_keyframe
        self.last_pts = packet.pts
        self.container.mux(packet)
        self._fill_audio(received_at)
        return self._take(frame.is_keyframe)

    def _fill_audio(self, received_at):
        """Keep a declared AAC track usable while source audio is absent.

        Silence is generated only for an already requested reader; diagnostics
        distinguish it from received sound. Never build a backlog after a gap.
        """
        if self.audio is None or (self._last_audio_at is not None
                and received_at - self._last_audio_at <= .1):
            return
        if self._last_audio_at is not None and self.audio_status in ('receiving', 'buffering'):
            self.audio_status = 'source_gap'
        wanted = max(0, round((received_at - self.origin) * AUDIO_RATE))
        self._audio_pts = max(self._audio_pts or 0, wanted - 1024)
        try:
            while self._audio_pts < wanted + 1024:
                frame = self.av.AudioFrame(format='fltp', layout='mono', samples=1024)
                frame.sample_rate, frame.time_base = AUDIO_RATE, Fraction(1, AUDIO_RATE)
                frame.pts = self._audio_pts
                for plane in frame.planes:
                    plane.update(bytes(plane.buffer_size))
                self._audio_pts += frame.samples
                for packet in self.audio.encode(frame):
                    self.container.mux(packet)
                self.silence_frames += 1
        except (self.av.FFmpegError, ValueError, OverflowError):
            self.audio_errors += 1
            self.audio_status = 'source_error'

    def encode_audio(self, frame, received_at):
        """Copy/resample shared PCM; audio errors never stop the video encoder."""
        if self.audio is None:
            return b'', False
        try:
            if (not isinstance(frame, self.av.AudioFrame)
                    or frame.sample_rate not in (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000)
                    or len(frame.layout.channels) not in (1, 2)
                    or not 0 < frame.samples <= frame.sample_rate // 5):
                raise ValueError('Local audio format outside bounds')
            format_key = frame.format.name, frame.layout.name, frame.sample_rate
            if (self._audio_format != format_key or self._last_audio_at is None
                    or received_at - self._last_audio_at > MAX_INPUT_AGE):
                self._audio_resampler = self.av.AudioResampler(
                    format='fltp', layout='mono', rate=AUDIO_RATE, frame_size=1024)
                self._audio_format = format_key
                start = round(max(0, received_at - self.origin - frame.samples / frame.sample_rate) * AUDIO_RATE)
                self._audio_pts = max(self._audio_pts or 0, start)
            copy = self.av.AudioFrame(format=frame.format.name,
                layout=frame.layout.name, samples=frame.samples)
            copy.sample_rate = frame.sample_rate
            for source, target in zip(frame.planes, copy.planes):
                pixels = bytes(source)
                target.update(pixels[:target.buffer_size].ljust(target.buffer_size, b'\0'))
            self._last_audio_at = received_at
            self.audio_status = 'buffering'
            for converted in self._audio_resampler.resample(copy):
                converted.pts, converted.time_base = self._audio_pts, Fraction(1, AUDIO_RATE)
                self._audio_pts += converted.samples
                for packet in self.audio.encode(converted):
                    self.container.mux(packet)
                self.audio_frames += 1
                self.audio_status = 'receiving'
        except (self.av.FFmpegError, ValueError, OverflowError):
            self.audio_errors += 1
            self.audio_status = 'source_error'
            self._audio_resampler = self._audio_format = None
        return self._take()

    def close(self):
        if self.container is not None:
            try:
                if not self.copy_video:
                    for packet in self.stream.encode(None):
                        self.container.mux(packet)
                        self.buffer.take()
                if self.audio is not None:
                    for packet in self.audio.encode(None):
                        self.container.mux(packet)
                        self.buffer.take()
            finally:
                self.container.close()
                self.container = self.stream = None
                self.buffer.take()


class LocalVideoStream:
    """Each HTTP reader owns one lease; all readers share one media mux.

    Resolving the source starts only a loopback listener. The camera owns the
    hub's close-listener registration and calls close_clients() for a reusable
    channel switch, or close() when the camera is removed.
    """

    def __init__(self, hub):
        self.hub = hub
        self._path = '/' + secrets.token_urlsafe(32)
        self._server = None
        self._url = None
        self._server_lock = asyncio.Lock()
        self._pipeline_lock = asyncio.Lock()
        self._closed = False
        self._closing_clients = False
        self._close_task = None
        self._clients_close_task = None
        self._handlers = set()
        self._queues = set()
        self._waiting_keys = set()
        self._ending_queues = set()
        self._frames = deque()
        self._audio_frames = deque()
        self._media_available = asyncio.Event()
        self._encoder_task = None
        self._next_frame = 0.0
        self._encoded_frames = 0
        self._input_video = self._input_audio = 0
        self._dropped_video = self._dropped_audio = self._rate_limited = 0
        self._keyframe_wait_packets = self._preroll_audio = 0
        self._audio_encoded = self._audio_errors = 0
        self._silence_frames = 0
        self._processing_ms = self._queue_age_ms = 0
        self._force_keyframe = False
        self._source_keyframe_required = True
        self._native = hasattr(hub, 'video_packet_listeners')
        self._audio_enabled = bool(getattr(getattr(hub, 'capabilities', None), 'downstream_audio', False))
        self._audio_status = 'waiting_source' if self._audio_enabled else 'disabled'
        self._reason = 'idle'
        self._error_type = None
        hub.frame_listeners.add(self._frame)
        if self._native:
            hub.video_packet_listeners.add(self._video_packet)

    @property
    def closed(self):
        return self._closed

    @property
    def diagnostics(self):
        """Only bounded format and lifecycle facts, never the bearer URL."""
        return dict(format='mpegts', max_fps=60 if self._native else MAX_FPS,
                    video_mode='native_h264' if self._native else 'decoded_h264',
                    audio_enabled=self._audio_enabled, audio_codec='aac' if self._audio_enabled else None,
                    audio_frames=self._audio_encoded, audio_errors=self._audio_errors,
                    silence_frames=self._silence_frames,
                    audio_status=self._audio_status,
                    input_video_frames=self._input_video, input_audio_frames=self._input_audio,
                    dropped_video_frames=self._dropped_video, dropped_audio_frames=self._dropped_audio,
                    keyframe_wait_packets=self._keyframe_wait_packets,
                    skipped_preroll_audio_frames=self._preroll_audio,
                    rate_limited_video_frames=self._rate_limited,
                    last_processing_ms=self._processing_ms, last_queue_age_ms=self._queue_age_ms,
                    clients=len(self._queues),
                    frames=self._encoded_frames, reason=self._reason,
                    error_type=self._error_type)

    async def source(self):
        async with self._server_lock:
            if self._closed or self.hub.stopped:
                raise RuntimeError('Local video source unavailable')
            if self._server is None:
                server = await asyncio.start_server(
                    self._connected, '127.0.0.1', 0, limit=HEADER_LIMIT,
                    backlog=MAX_CLIENTS)
                if self._closed or self.hub.stopped:
                    server.close()
                    await server.wait_closed()
                    raise RuntimeError('Local video source unavailable')
                self._server = server
                port = server.sockets[0].getsockname()[1]
                self._url = f'http://127.0.0.1:{port}{self._path}'
            return self._url

    def _connected(self, reader, writer):
        # Reject before creating another coroutine or retaining a media lease.
        if (self._closed or self._closing_clients or self.hub.stopped
                or len(self._handlers) >= MAX_CLIENTS):
            writer.close()
            return
        task = asyncio.create_task(self._serve(reader, writer), name='welcomeeye-local-http')
        self._handlers.add(task)
        task.add_done_callback(self._handlers.discard)

    def _frame(self, kind, frame):
        if not self._queues or self._closed:
            return
        now = asyncio.get_running_loop().time()
        if kind == 'audio':
            if not self._audio_enabled:
                return
            self._input_audio += 1
            if len(self._audio_frames) >= MAX_AUDIO_FRAMES:
                self._audio_frames.popleft()
                self._dropped_audio += 1
            self._audio_frames.append((frame, now))
            self._media_available.set()
            return
        if kind != 'video' or self._native:
            return
        self._input_video += 1
        interval = 1 / MAX_FPS
        # Preserve the cadence through small scheduling jitter instead of
        # resetting the deadline from every arrival (which halved some feeds).
        if now + interval * .1 < self._next_frame:
            self._rate_limited += 1
            return
        self._next_frame = max(self._next_frame + interval, now + interval / 2)
        if len(self._frames) >= MAX_VIDEO_FRAMES:
            self._frames.popleft()
            self._dropped_video += 1
        self._frames.append(('video', frame, now))
        self._media_available.set()

    def _video_packet(self, frame):
        if not self._queues or self._closed:
            return
        if not (getattr(frame, 'is_h264', False) and isinstance(frame.payload, bytes)
                and 0 < len(frame.payload) <= MAX_CHUNK_BYTES):
            return
        self._input_video += 1
        if len(self._frames) >= MAX_VIDEO_FRAMES:
            # Never continue a compressed prediction chain after discarding
            # one of its packets. Rejoin only at the next real source IDR.
            self._dropped_video += len(self._frames)
            while self._frames:
                self._frames.popleft()
            self._source_keyframe_required = True
        if self._source_keyframe_required:
            if not frame.is_keyframe:
                self._keyframe_wait_packets += 1
                return
            self._source_keyframe_required = False
        self._frames.append(('h264', frame, asyncio.get_running_loop().time()))
        self._media_available.set()

    async def _add_queue(self, queue):
        async with self._pipeline_lock:
            if self._closed or self._closing_clients or self.hub.stopped:
                raise RuntimeError('Local video source unavailable')
            if self._encoder_task is not None and self._encoder_task.done():
                raise RuntimeError('Local video encoder unavailable')
            self._queues.add(queue)
            self._waiting_keys.add(queue)
            self._force_keyframe = True
            if self._encoder_task is None:
                self._next_frame = 0.0
                self._source_keyframe_required = True
                self._encoder_task = asyncio.create_task(
                    self._encode(), name='welcomeeye-local-mpegts')

    async def _remove_queue(self, queue):
        async with self._pipeline_lock:
            self._queues.discard(queue)
            self._waiting_keys.discard(queue)
            self._ending_queues.discard(queue)
            if not self._queues:
                task, self._encoder_task = self._encoder_task, None
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                while self._frames:
                    self._frames.popleft()
                while self._audio_frames:
                    self._audio_frames.popleft()
                self._media_available.clear()
                self._audio_status = 'idle' if self._audio_enabled else 'disabled'

    async def _encode(self):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='welcomeeye-local-video')
        loop = asyncio.get_running_loop()
        encoder = None
        encoding = loop.run_in_executor(executor,
            lambda: _TransportEncoder(audio=self._audio_enabled))
        video_started = False
        try:
            await asyncio.wait({encoding})
            encoder = encoding.result()
            encoding = None
            while True:
                await self._media_available.wait()
                self._media_available.clear()
                while self._frames or (video_started and self._audio_frames):
                    # Video opens the mux with known dimensions. Thereafter
                    # process arrivals in order, with small bounded queues.
                    audio = (video_started and self._audio_frames
                        and (not self._frames
                             or self._audio_frames[0][1] < self._frames[0][2]))
                    if audio:
                        frame, received_at = self._audio_frames.popleft()
                        kind = 'audio'
                    else:
                        kind, frame, received_at = self._frames.popleft()
                    age = loop.time() - received_at
                    self._queue_age_ms = round(age * 1000)
                    if age > MAX_INPUT_AGE:
                        if kind == 'audio':
                            self._dropped_audio += 1
                        elif kind == 'h264':
                            self._dropped_video += len(self._frames) + 1
                            self._source_keyframe_required = True
                            while self._frames:
                                self._frames.popleft()
                        else:
                            self._dropped_video += 1
                        continue
                    if kind != 'audio' and not video_started:
                        # Joining an existing camera waits for its next IDR.
                        # Audio from before that picture is not part of this
                        # viewer's timeline and must not play against it.
                        while self._audio_frames and self._audio_frames[0][1] < received_at:
                            self._audio_frames.popleft()
                            self._preroll_audio += 1
                    if kind == 'audio':
                        encoding = loop.run_in_executor(executor, encoder.encode_audio, frame, received_at)
                    elif kind == 'h264':
                        encoding = loop.run_in_executor(executor, encoder.remux, frame, received_at)
                        video_started = True
                    else:
                        force, self._force_keyframe = self._force_keyframe, False
                        encoding = loop.run_in_executor(executor, encoder.encode, frame, received_at, force)
                        video_started = True
                    started = loop.time()
                    # Cancellation must join the worker before releasing its frame.
                    await asyncio.wait({encoding})
                    chunk, keyframe = encoding.result()
                    encoding = None
                    self._processing_ms = round((loop.time() - started) * 1000, 2)
                    self._audio_encoded = getattr(encoder, 'audio_frames', 0)
                    self._audio_errors = getattr(encoder, 'audio_errors', 0)
                    self._silence_frames = getattr(encoder, 'silence_frames', 0)
                    self._audio_status = getattr(encoder, 'audio_status', self._audio_status)
                    if kind != 'audio':
                        self._encoded_frames += 1
                    self._publish(chunk, keyframe)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error('encode_failed', exc)
            for queue in tuple(self._queues):
                self._end_queue(queue)
        finally:
            cleanup = asyncio.create_task(self._finish_encoder(executor, encoder, encoding))
            await _finish_task(cleanup, cancel_on_cancel=False)

    def _publish(self, chunk, keyframe):
        if not chunk:
            return
        if len(chunk) > MAX_CHUNK_BYTES:
            raise ValueError('Local video transport chunk outside bounds')
        for queue in tuple(self._queues):
            if queue in self._ending_queues:
                continue
            if queue in self._waiting_keys:
                if not keyframe:
                    continue
                self._waiting_keys.discard(queue)
            if queue.full():
                self._reason = 'slow_client'
                self._end_queue(queue)
            else:
                queue.put_nowait(chunk)

    async def _finish_encoder(self, executor, encoder, encoding):
        try:
            if encoding is not None:
                try:
                    await asyncio.wait({encoding})
                    result = encoding.result()
                    if encoder is None:
                        encoder = result
                except Exception:
                    pass
            if encoder is not None:
                closing = asyncio.get_running_loop().run_in_executor(executor, encoder.close)
                await asyncio.wait({closing})
                closing.result()
        except Exception as exc:
            self._error('encode_cleanup_failed', exc)
        finally:
            await asyncio.to_thread(executor.shutdown, wait=True)

    def _end_queue(self, queue):
        self._ending_queues.add(queue)
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(None)

    def _error(self, reason, exc):
        self._reason = reason
        name = type(exc).__name__
        self._error_type = name if name in (
            'TimeoutError', 'ValueError', 'RuntimeError', 'OSError',
            'ConnectionError', 'ConnectionResetError', 'BrokenPipeError',
            'IncompleteReadError', 'LimitOverrunError') else 'StreamError'

    @staticmethod
    async def _write(writer, data):
        writer.write(data)
        await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT)

    async def _serve(self, reader, writer):
        owner = object()
        queue = asyncio.Queue(maxsize=MAX_PENDING_CHUNKS)
        acquisition = disconnected = next_frame = None
        requested = response_started = False
        try:
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), HEADER_TIMEOUT)
            line = header.split(b'\r\n', 1)[0]
            valid = (f'GET {self._path} HTTP/1.1'.encode(),
                     f'GET {self._path} HTTP/1.0'.encode())
            if len(header) > HEADER_LIMIT or line not in valid:
                await self._write(writer, b'HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n')
                return
            disconnected = asyncio.create_task(reader.read(1))
            await self._add_queue(queue)
            if disconnected.done():
                self._reason = 'client_disconnected'
                return
            requested = True
            self._reason, self._error_type = 'acquiring', None
            acquisition = asyncio.create_task(self.hub.acquire(owner))
            done, _ = await asyncio.wait((acquisition, disconnected),
                timeout=ACQUIRE_TIMEOUT, return_when=asyncio.FIRST_COMPLETED)
            if disconnected in done:
                self._reason = 'client_disconnected'
                return
            if acquisition not in done:
                self._reason = 'acquisition_timeout'
                raise TimeoutError
            acquisition.result()
            response_started = True
            await self._write(writer,
                b'HTTP/1.1 200 OK\r\nContent-Type: video/mp2t\r\n'
                b'Cache-Control: no-store\r\nConnection: close\r\n\r\n')
            self._reason = 'streaming'
            while True:
                next_frame = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait((next_frame, disconnected),
                    timeout=FRAME_TIMEOUT, return_when=asyncio.FIRST_COMPLETED)
                if disconnected in done:
                    self._reason = 'client_disconnected'
                    return
                if next_frame not in done:
                    self._reason = 'idle_timeout'
                    raise TimeoutError
                chunk = next_frame.result()
                next_frame = None
                if chunk is None:
                    return
                await self._write(writer, chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason = self._reason if self._reason in (
                'acquisition_timeout', 'idle_timeout') else 'stream_failed'
            self._error(reason, exc)
            if requested and not response_started:
                try:
                    await self._write(writer,
                        b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n')
                except (OSError, TimeoutError):
                    pass
        finally:
            cleanup = asyncio.create_task(self._cleanup_client(
                writer, owner, queue, requested, acquisition, disconnected, next_frame))
            await _finish_task(cleanup, cancel_on_cancel=False)

    async def _cleanup_client(self, writer, owner, queue, requested, *pending):
        tasks = [task for task in pending if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await self._remove_queue(queue)
            if requested:
                await self.hub.release(owner, reason='local_stream_release')
        except Exception as exc:
            self._error('cleanup_failed', exc)
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), WRITE_TIMEOUT)
            except (OSError, TimeoutError):
                pass

    async def close_clients(self):
        """Drain active readers without permanently closing the local source."""
        if self._clients_close_task is None or self._clients_close_task.done():
            self._clients_close_task = asyncio.create_task(self._close_clients())
        await _finish_task(self._clients_close_task, cancel_on_cancel=False)

    async def _close_clients(self):
        self._closing_clients = True
        try:
            tasks = tuple(self._handlers)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._reason = 'clients_closed'
        finally:
            self._closing_clients = False

    async def close(self):
        """Permanently close the listener, all readers and the frame callback."""
        self._closed = True
        self.hub.frame_listeners.discard(self._frame)
        if self._native:
            self.hub.video_packet_listeners.discard(self._video_packet)
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await _finish_task(self._close_task, cancel_on_cancel=False)

    async def _close(self):
        async with self._server_lock:
            server, self._server = self._server, None
            if server is not None:
                server.close()
            self._url = None
        # Python 3.13+ also waits for active transports in wait_closed().
        # Cancel/release HTTP readers first instead of waiting for idle expiry.
        await self.close_clients()
        self._frames.clear()
        self._audio_frames.clear()
        if server is not None:
            await server.wait_closed()
        self._audio_status = 'closed'
        self._reason = 'closed'
