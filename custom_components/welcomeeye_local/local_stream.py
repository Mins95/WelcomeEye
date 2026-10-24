"""On-demand loopback MPEG-TS from the hub's existing decoded video frames."""
import asyncio
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
MAX_FPS = 10
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

    def __init__(self):
        import av
        from av.video.reformatter import VideoReformatter

        self.av = av
        self.reformatter = VideoReformatter()
        self.buffer = _TransportBuffer()
        self.container = self.stream = None
        self.origin = None
        self.last_pts = -1
        self.last_keyframe = -90000

    def encode(self, frame, received_at):
        if not (0 < frame.width <= 4096 and 0 < frame.height <= 4096):
            raise ValueError('Local video dimensions outside bounds')
        if self.container is None:
            scale = min(1280 / frame.width, 720 / frame.height, 1)
            width = max(2, int(frame.width * scale) // 2 * 2)
            height = max(2, int(frame.height * scale) // 2 * 2)
            self.container = self.av.open(self.buffer, mode='w', format='mpegts', options={
                'mpegts_flags': 'resend_headers+pat_pmt_at_frames',
                'flush_packets': '1', 'mpegts_copyts': '1'})
            self.stream = self.container.add_stream('libx264', rate=MAX_FPS)
            self.stream.width, self.stream.height = width, height
            self.stream.pix_fmt = 'yuv420p'
            self.stream.time_base = TIME_BASE
            self.stream.codec_context.time_base = TIME_BASE
            self.stream.codec_context.thread_count = 1
            self.stream.codec_context.gop_size = MAX_FPS
            self.stream.codec_context.max_b_frames = 0
            self.stream.options = {'preset': 'ultrafast', 'tune': 'zerolatency',
                'crf': '23', 'x264-params': 'scenecut=0:repeat-headers=1'}
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
        if self.origin is None:
            self.origin = received_at
        pts = max(self.last_pts + 1, round((received_at - self.origin) * 90000))
        self.last_pts = pts
        converted.pts, converted.time_base = pts, TIME_BASE
        if pts - self.last_keyframe >= 90000:
            converted.pict_type = self.av.video.frame.PictureType.I
        else:
            converted.pict_type = self.av.video.frame.PictureType.NONE
        keyframe = False
        for packet in self.stream.encode(converted):
            keyframe = keyframe or packet.is_keyframe
            self.container.mux(packet)
        if keyframe:
            self.last_keyframe = pts
        return self.buffer.take(), keyframe

    def close(self):
        if self.container is not None:
            try:
                for packet in self.stream.encode(None):
                    self.container.mux(packet)
                    self.buffer.take()
            finally:
                self.container.close()
                self.container = self.stream = None
                self.buffer.take()


class LocalVideoStream:
    """Each HTTP reader owns one lease; all readers share one H.264 encoder.

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
        self._frames = asyncio.Queue(maxsize=1)
        self._encoder_task = None
        self._next_frame = 0.0
        self._encoded_frames = 0
        self._reason = 'idle'
        self._error_type = None
        hub.frame_listeners.add(self._frame)

    @property
    def closed(self):
        return self._closed

    @property
    def diagnostics(self):
        """Only bounded format and lifecycle facts, never the bearer URL."""
        return dict(format='mpegts', max_fps=MAX_FPS, clients=len(self._queues),
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
        if kind != 'video' or not self._queues or self._closed:
            return
        now = asyncio.get_running_loop().time()
        if now < self._next_frame:
            return
        self._next_frame = now + 1 / MAX_FPS
        if self._frames.full():
            self._frames.get_nowait()
        self._frames.put_nowait((frame, now))

    async def _add_queue(self, queue):
        async with self._pipeline_lock:
            if self._closed or self._closing_clients or self.hub.stopped:
                raise RuntimeError('Local video source unavailable')
            if self._encoder_task is not None and self._encoder_task.done():
                raise RuntimeError('Local video encoder unavailable')
            self._queues.add(queue)
            self._waiting_keys.add(queue)
            if self._encoder_task is None:
                self._next_frame = 0.0
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
                while not self._frames.empty():
                    self._frames.get_nowait()

    async def _encode(self):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='welcomeeye-local-video')
        loop = asyncio.get_running_loop()
        encoder = None
        encoding = loop.run_in_executor(executor, _TransportEncoder)
        try:
            await asyncio.wait({encoding})
            encoder = encoding.result()
            encoding = None
            while True:
                frame, received_at = await self._frames.get()
                encoding = loop.run_in_executor(executor, encoder.encode, frame, received_at)
                # Cancellation must join the worker before releasing its frame.
                await asyncio.wait({encoding})
                chunk, keyframe = encoding.result()
                encoding = None
                if not chunk:
                    continue
                if len(chunk) > MAX_CHUNK_BYTES:
                    raise ValueError('Local video transport chunk outside bounds')
                self._encoded_frames += 1
                for queue in tuple(self._queues):
                    if queue in self._ending_queues:
                        continue
                    if queue in self._waiting_keys:
                        if not keyframe:
                            continue
                        self._waiting_keys.discard(queue)
                    if queue.full():
                        # Dropping an H.264 chunk would corrupt its dependants.
                        # Close only this slow reader; other readers continue.
                        self._reason = 'slow_client'
                        self._end_queue(queue)
                    else:
                        queue.put_nowait(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error('encode_failed', exc)
            for queue in tuple(self._queues):
                self._end_queue(queue)
        finally:
            cleanup = asyncio.create_task(self._finish_encoder(executor, encoder, encoding))
            await _finish_task(cleanup, cancel_on_cancel=False)

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
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await _finish_task(self._close_task, cancel_on_cancel=False)

    async def _close(self):
        async with self._server_lock:
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
                self._server = None
            self._url = None
        await self.close_clients()
        self._reason = 'closed'
