"""Shared media session that exists only while a consumer holds a lease."""
import asyncio
from collections import deque
import logging
import secrets
import threading
import time

from .client import AuthenticationError, Session
from .control import DeviceController
from .media import MediaPipeline, StreamFormat

_LOGGER = logging.getLogger(__name__)


class WelcomeEyeHub:
    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.control = DeviceController(self)
        self.loop = asyncio.get_running_loop()
        self.connected = False
        self.connection_count = 0
        self.image = self.format = self.error = None
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

    async def start(self):
        # Config flow validates credentials. Startup must not seize video.
        self.server = await asyncio.start_server(self._serve, '127.0.0.1', 0, limit=8192)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f'http://127.0.0.1:{port}{self.path}'
        self.stopped = False

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
                    self.thread = threading.Thread(target=self._worker,
                        args=(self.generation, self.stop_event),
                        name='welcomeeye-media', daemon=True)
                    self.thread.start()
            await asyncio.wait_for(self.ready.wait(), 18)
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
        while self.buffer and (self.buffer_size > 1024 * 1024 or self.buffer[0][0] < now - 3):
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
            if line not in (f'GET {self.path} HTTP/1.1'.encode(), f'GET {self.path} HTTP/1.0'.encode()):
                writer.write(b'HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n')
                await writer.drain()
                return
            if self.stopped or len(self.handlers) > 4:
                writer.write(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n')
                await writer.drain()
                return
            await self.acquire(task)
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: video/mp2t\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n')
            queue = asyncio.Queue(maxsize=256)
            self.queues.add(queue)
            for _, chunk in self.buffer:
                writer.write(chunk)
            await asyncio.wait_for(writer.drain(), 5)
            eof = asyncio.create_task(reader.read(1))
            while True:
                next_chunk = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait((next_chunk, eof), timeout=15,
                                            return_when=asyncio.FIRST_COMPLETED)
                if eof in done or next_chunk not in done:
                    break
                chunk = next_chunk.result()
                if chunk is None:
                    break
                writer.write(chunk)
                await asyncio.wait_for(writer.drain(), 5)
        except (OSError, TimeoutError, AuthenticationError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            pending = [t for t in (eof, next_chunk) if t is not None]
            for t in pending:
                t.cancel()
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

    def _worker(self, generation, stop_event):
        def emit(callback, *args):
            self.loop.call_soon_threadsafe(self._dispatch, generation, callback, *args)
        delay = 2
        while not stop_event.is_set():
            pipeline = None
            session = Session(self.entry.data['host'], self.entry.data['username'],
                              self.entry.data['password'], self.entry.data.get('channel', 16))
            self.session = session
            try:
                if stop_event.is_set():
                    return
                parts = session.connect()
                while not stop_event.is_set():
                    for kind, body in parts:
                        if kind == 203:
                            fmt = StreamFormat.parse(body)
                            if pipeline:
                                pipeline.close()
                            pipeline = MediaPipeline(fmt,
                                lambda data: emit(self._ts, data),
                                lambda data: emit(self._image, data),
                                lambda kind, frame: emit(self._frame, kind, frame))
                            emit(self._state, True, fmt)
                            delay = 2
                        elif pipeline and kind in (98, 100, 101):
                            pipeline.feed(kind, body)
                    if not stop_event.is_set():
                        parts = session.read()
            except AuthenticationError as exc:
                emit(self._state, False, None, exc)
                return
            except Exception as exc:
                if not stop_event.is_set():
                    _LOGGER.warning('WelcomeEye connection interrupted (%s)', type(exc).__name__)
                    emit(self._state, False, None, exc)
            finally:
                session.close()
                self.session = None
                if pipeline:
                    try:
                        pipeline.close()
                    except Exception:
                        pass
            if stop_event.wait(delay):
                return
            delay = min(delay * 2, 60)

    async def stop(self):
        self.stopped = True
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
