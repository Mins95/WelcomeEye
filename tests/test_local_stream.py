"""Local sockets and synthetic frames; no camera or cloud endpoint is used."""
import asyncio
from fractions import Fraction
from io import BytesIO
import math
import struct
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from load_integration import load

stream_module = load('local_stream')


class Hub:
    def __init__(self):
        self.stopped = False
        self.frame_listeners = set()
        self.consumers = set()
        self.ready = asyncio.Event()
        self.ready.set()
        self.acquired = asyncio.Queue()
        self.sessions = 0
        self.releases = []
        self.failure = None

    async def acquire(self, owner):
        if not self.consumers:
            self.sessions += 1
        self.consumers.add(owner)
        self.acquired.put_nowait(owner)
        await self.ready.wait()
        if self.failure:
            raise self.failure

    async def release(self, owner, *, reason):
        self.releases.append((owner, reason))
        self.consumers.discard(owner)

    def emit(self, frame, kind='video'):
        for callback in tuple(self.frame_listeners):
            callback(kind, frame)


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


class LocalStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = Hub()
        self.stream = stream_module.LocalVideoStream(self.hub)
        self.writers = []
        self.encode = Mock(side_effect=lambda frame, received_at: ((str(frame) + '\n').encode(), True))
        self.encoders = []
        owner = self

        class Encoder:
            def __init__(self, *, audio=False):
                self.thread = threading.get_ident()
                self.closed = False
                owner.encoders.append(self)

            def encode(self, frame, received_at, force_keyframe=False):
                owner.assertEqual(threading.get_ident(), self.thread)
                return owner.encode(frame, received_at)

            def close(self):
                owner.assertEqual(threading.get_ident(), self.thread)
                self.closed = True

        self.encoder = patch.object(stream_module, '_TransportEncoder', Encoder)
        self.encoder.start()
        self.loop_errors = []
        self.loop = asyncio.get_running_loop()
        self.previous_handler = self.loop.get_exception_handler()
        self.loop.set_exception_handler(lambda _loop, context: self.loop_errors.append(context))

    async def asyncTearDown(self):
        await self.stream.close()
        for writer in self.writers:
            await self.disconnect(writer)
        self.encoder.stop()
        self.loop.set_exception_handler(self.previous_handler)
        self.assertFalse(self.hub.consumers)
        self.assertFalse(self.stream._handlers)
        self.assertIsNone(self.stream._encoder_task)
        self.assertFalse(self.hub.frame_listeners)
        self.assertTrue(all(encoder.closed for encoder in self.encoders))
        self.assertEqual(self.loop_errors, [])

    async def connect(self, path=None, *, request=True):
        url = urlsplit(await self.stream.source())
        reader, writer = await asyncio.open_connection(url.hostname, url.port)
        self.writers.append(writer)
        if request:
            writer.write(f'GET {url.path if path is None else path} HTTP/1.1\r\nHost: localhost\r\n\r\n'.encode())
            await writer.drain()
        return reader, writer

    async def disconnect(self, writer):
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 1)
        except (ConnectionError, OSError):
            pass

    async def headers(self, reader):
        return await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 2)

    async def part(self, reader):
        return await asyncio.wait_for(reader.readline(), 2)

    async def test_resolving_source_is_idle_loopback_and_stable(self):
        first, second = await asyncio.gather(self.stream.source(), self.stream.source())
        self.assertEqual(first, second)
        self.assertEqual(urlsplit(first).hostname, '127.0.0.1')
        self.assertGreaterEqual(len(urlsplit(first).path), 40)
        self.assertEqual(self.hub.sessions, 0)
        self.assertIsNone(self.stream._encoder_task)
        self.assertNotIn(urlsplit(first).path, str(self.stream.diagnostics))

    async def test_unknown_path_does_not_acquire(self):
        reader, _ = await self.connect('/not-the-token')
        self.assertIn(b'404 Not Found', await self.headers(reader))
        self.assertEqual(await reader.read(), b'')
        self.assertEqual(self.hub.sessions, 0)

    async def test_oversized_header_does_not_acquire(self):
        with patch.object(stream_module, 'HEADER_LIMIT', 256):
            reader, writer = await self.connect(request=False)
            writer.write(b'GET / HTTP/1.1\r\nX: ' + b'a' * 512 + b'\r\n\r\n')
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(), 2), b'')
        self.assertEqual(self.hub.sessions, 0)

    async def test_incomplete_header_times_out_without_acquisition(self):
        with patch.object(stream_module, 'HEADER_TIMEOUT', 0.03):
            reader, writer = await self.connect(request=False)
            writer.write(b'GET ')
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(), 2), b'')
        self.assertEqual(self.hub.sessions, 0)

    async def test_disconnect_during_acquisition_releases_immediately(self):
        self.hub.ready.clear()
        _, writer = await self.connect()
        await asyncio.wait_for(self.hub.acquired.get(), 1)
        await self.disconnect(writer)
        await until(lambda: not self.hub.consumers and not self.stream._handlers)
        self.assertEqual(self.hub.sessions, 1)
        self.assertEqual(len(self.hub.releases), 1)

    async def test_disconnect_without_frames_does_not_wait_for_idle_timeout(self):
        reader, writer = await self.connect()
        self.assertIn(b'200 OK', await self.headers(reader))
        await self.disconnect(writer)
        await until(lambda: not self.hub.consumers and not self.stream._handlers)

    async def test_two_http_viewers_share_encoder_and_preserve_webrtc_lease(self):
        await self.hub.acquire('synthetic-webrtc')
        first, first_writer = await self.connect()
        second, second_writer = await self.connect()
        self.assertIn(b'200 OK', await self.headers(first))
        self.assertIn(b'200 OK', await self.headers(second))
        self.hub.emit('shared')
        payloads = await asyncio.gather(self.part(first), self.part(second))
        self.assertEqual(payloads, [b'shared\n'] * 2)
        self.assertEqual(self.encode.call_count, 1)
        self.assertEqual(self.hub.sessions, 1)
        await self.disconnect(first_writer)
        await until(lambda: len(self.hub.consumers) == 2)
        await asyncio.sleep(0.11)
        self.hub.emit('remaining')
        self.assertEqual(await self.part(second), b'remaining\n')
        await self.disconnect(second_writer)
        await until(lambda: self.hub.consumers == {'synthetic-webrtc'})
        await self.stream.close()
        self.assertEqual(self.hub.consumers, {'synthetic-webrtc'})
        await self.hub.release('synthetic-webrtc', reason='test_finished')

    async def test_acquisition_deadline_returns_503_and_releases(self):
        self.hub.ready.clear()
        with patch.object(stream_module, 'ACQUIRE_TIMEOUT', 0.03):
            reader, _ = await self.connect()
            self.assertIn(b'503 Service Unavailable', await self.headers(reader))
            self.assertEqual(await reader.read(), b'')
            await until(lambda: not self.hub.consumers)
        self.assertEqual(self.stream.diagnostics['reason'], 'acquisition_timeout')
        self.assertEqual(self.hub.sessions, 1)

    async def test_frame_deadline_closes_without_restarting_session(self):
        with patch.object(stream_module, 'FRAME_TIMEOUT', 0.03):
            reader, _ = await self.connect()
            self.assertIn(b'200 OK', await self.headers(reader))
            self.assertEqual(await asyncio.wait_for(reader.read(), 2), b'')
            await until(lambda: not self.hub.consumers)
        self.assertEqual(self.stream.diagnostics['reason'], 'idle_timeout')
        self.assertEqual(self.hub.sessions, 1)

    async def test_other_channel_busy_is_not_retried(self):
        self.hub.failure = RuntimeError('Synthetic other channel busy')
        reader, _ = await self.connect()
        self.assertIn(b'503 Service Unavailable', await self.headers(reader))
        self.assertEqual(await reader.read(), b'')
        self.assertEqual(self.hub.sessions, 1)
        self.assertEqual(len(self.hub.releases), 1)

    async def test_close_clients_keeps_source_reusable(self):
        url = await self.stream.source()
        reader, _ = await self.connect()
        await self.headers(reader)
        await self.stream.close_clients()
        self.assertEqual(await reader.read(), b'')
        self.assertFalse(self.hub.consumers)
        self.assertEqual(await self.stream.source(), url)
        reader, _ = await self.connect()
        self.assertIn(b'200 OK', await self.headers(reader))
        self.hub.emit('new-session')
        self.assertEqual(await self.part(reader), b'new-session\n')
        self.assertEqual(self.hub.sessions, 2)

    async def test_close_is_permanent_and_idempotent(self):
        url = urlsplit(await self.stream.source())
        reader, _ = await self.connect()
        await self.headers(reader)
        await asyncio.wait_for(self.stream.close(), 1)
        await self.stream.close()
        self.assertEqual(await reader.read(), b'')
        with self.assertRaises(RuntimeError):
            await self.stream.source()
        with self.assertRaises(OSError):
            await asyncio.open_connection(url.hostname, url.port)

    async def test_jitter_does_not_halve_a_30_fps_source(self):
        self.stream._queues.add(asyncio.Queue())
        clock = SimpleNamespace(time=lambda: now)
        try:
            for index in range(90):
                now = 100 + index / 30 + (.0008 if index % 2 else 0)
                with patch.object(stream_module.asyncio, 'get_running_loop', return_value=clock):
                    self.stream._frame('video', index)
                self.assertEqual(self.stream._frames.popleft()[1], index)
            self.assertEqual(self.stream.diagnostics['rate_limited_video_frames'], 0)
        finally:
            self.stream._queues.clear()

    async def test_native_overflow_waits_for_a_new_keyframe(self):
        self.stream._queues.add(asyncio.Queue())
        def packet(key=False):
            return SimpleNamespace(is_h264=True, is_keyframe=key, payload=b'SYNTHETIC_H264')
        try:
            self.stream._video_packet(packet(True))
            for _ in range(stream_module.MAX_VIDEO_FRAMES):
                self.stream._video_packet(packet())
            self.assertFalse(self.stream._frames)
            self.stream._video_packet(packet())
            self.assertFalse(self.stream._frames)
            self.stream._video_packet(packet(True))
            self.assertEqual(len(self.stream._frames), 1)
            self.assertTrue(self.stream._frames[0][1].is_keyframe)
        finally:
            self.stream._queues.clear()

    async def test_pending_audio_is_bounded_without_starting_a_video(self):
        self.stream._audio_enabled = True
        self.stream._queues.add(asyncio.Queue())
        try:
            for number in range(100):
                self.stream._frame('audio', number)
            self.assertEqual(len(self.stream._audio_frames), stream_module.MAX_AUDIO_FRAMES)
            self.assertEqual(self.stream.diagnostics['dropped_audio_frames'], 75)
            self.assertEqual(self.hub.sessions, 0)
            self.assertIsNone(self.stream._encoder_task)
        finally:
            self.stream._queues.clear()

    async def test_encode_failure_closes_all_consumers(self):
        self.encode.side_effect = ValueError('Synthetic encoder failure')
        readers = [await self.connect(), await self.connect()]
        for reader, _ in readers:
            await self.headers(reader)
        self.hub.emit('broken')
        for reader, _ in readers:
            self.assertEqual(await asyncio.wait_for(reader.read(), 2), b'')
        await until(lambda: not self.hub.consumers)
        self.assertEqual(self.stream.diagnostics['reason'], 'encode_failed')
        self.assertEqual(self.encode.call_count, 1)

    async def test_audio_ignored_and_video_burst_is_rate_limited(self):
        reader, _ = await self.connect()
        await self.headers(reader)
        self.hub.emit('audio', kind='audio')
        await asyncio.sleep(0.01)
        self.encode.assert_not_called()
        for number in range(20):
            self.hub.emit(number)
        self.assertEqual(await self.part(reader), b'0\n')
        self.assertEqual(self.encode.call_count, 1)
        await asyncio.sleep(0.11)
        self.hub.emit('later')
        self.assertEqual(await self.part(reader), b'later\n')
        self.assertEqual(self.encode.call_count, 2)

    async def test_extra_connections_are_rejected_before_acquisition(self):
        pending = [await self.connect(request=False) for _ in range(stream_module.MAX_CLIENTS)]
        reader, _ = await self.connect(request=False)
        self.assertEqual(await asyncio.wait_for(reader.read(), 2), b'')
        self.assertEqual(len(self.stream._handlers), len(pending))
        self.assertEqual(self.hub.sessions, 0)

    async def test_cancelled_close_joins_inflight_encoder_before_releasing(self):
        started, finish = threading.Event(), threading.Event()

        def encode(_frame, _received_at):
            started.set()
            finish.wait(3)
            return b'synthetic-transport', True

        self.encode.side_effect = encode
        reader, _ = await self.connect()
        await self.headers(reader)
        self.hub.emit('held')
        await until(started.is_set)
        closing = asyncio.create_task(self.stream.close())
        try:
            await asyncio.sleep(0.01)
            self.assertFalse(closing.done())
            closing.cancel()
            await asyncio.sleep(0)
            closing.cancel()
        finally:
            finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        await self.stream.close()
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.stream._encoder_task)

    async def test_new_reader_waits_for_keyframe(self):
        self.encode.side_effect = lambda frame, timestamp: ((frame + '\n').encode(), frame.startswith('key'))
        first, _ = await self.connect()
        await self.headers(first)
        self.hub.emit('key-first')
        self.assertEqual(await self.part(first), b'key-first\n')
        second, _ = await self.connect()
        await self.headers(second)
        await asyncio.sleep(0.11)
        self.hub.emit('delta')
        self.assertEqual(await self.part(first), b'delta\n')
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(second.readline(), 0.03)
        await asyncio.sleep(0.11)
        self.hub.emit('key-next')
        self.assertEqual(await self.part(first), b'key-next\n')
        self.assertEqual(await self.part(second), b'key-next\n')

    async def test_slow_reader_overflow_closes_only_that_reader(self):
        slow, slow_writer = await self.connect()
        fast, _ = await self.connect()
        await self.headers(slow)
        await self.headers(fast)
        blocked, resume = asyncio.Event(), asyncio.Event()
        address = slow_writer.get_extra_info('sockname')
        original = self.stream._write

        async def write(writer, data):
            if writer.get_extra_info('peername') == address and data == b'0\n':
                blocked.set()
                await resume.wait()
            await original(writer, data)

        try:
            with patch.object(self.stream, '_write', write), patch.object(stream_module, 'MAX_FPS', 1000):
                self.hub.emit(0)
                await asyncio.wait_for(blocked.wait(), 2)
                self.assertEqual(await self.part(fast), b'0\n')
                for number in range(1, stream_module.MAX_PENDING_CHUNKS + 2):
                    await asyncio.sleep(0.003)
                    self.hub.emit(number)
                    self.assertEqual(await self.part(fast), f'{number}\n'.encode())
                resume.set()
                self.assertEqual(await self.part(slow), b'0\n')
                self.assertEqual(await asyncio.wait_for(slow.read(), 2), b'')
                await until(lambda: len(self.hub.consumers) == 1)
                self.assertEqual(self.hub.sessions, 1)
        finally:
            resume.set()


class EncoderTests(unittest.TestCase):
    @staticmethod
    def audio_frame(av, number, *, rate=8000, channels=1):
        samples = rate // 50
        frame = av.AudioFrame(format='s16', layout='mono' if channels == 1 else 'stereo', samples=samples)
        frame.sample_rate = rate
        values = [round(7000 * math.sin(2 * math.pi * 440 * (number * samples + i) / rate))
                  for i in range(samples) for _ in range(channels)]
        frame.planes[0].update(struct.pack('<' + 'h' * len(values), *values))
        frame.pts, frame.time_base = number * samples, Fraction(1, rate)
        return frame

    def test_native_h264_with_aac_preserves_video_and_audio(self):
        import av
        media = load('connect3.protocol')
        source = av.CodecContext.create('libx264', 'w')
        source.width, source.height, source.pix_fmt = 96, 64, 'yuv420p'
        source.time_base, source.framerate = Fraction(1, 30), Fraction(30)
        source.thread_count, source.gop_size, source.max_b_frames = 1, 15, 0
        source.options = {'preset': 'ultrafast', 'tune': 'zerolatency',
                          'x264-params': 'repeat-headers=1:scenecut=0'}
        packets = []
        for number in range(60):
            frame = av.VideoFrame(96, 64, 'yuv420p')
            for index, plane in enumerate(frame.planes):
                plane.update(bytes((40 + number if index == 0 else 128,)) * plane.buffer_size)
            frame.pts, frame.time_base = number, Fraction(1, 30)
            packets.extend(source.encode(frame))
        packets.extend(source.encode(None))
        self.assertEqual(len(packets), 60)
        encoder = stream_module._TransportEncoder(audio=True)
        chunks, shared = [], []
        try:
            for tick in range(300):
                if tick % 5 == 0:
                    item = packets[tick // 5]
                    packet = media.MediaFrame(1 if item.is_keyframe else 0, 1,
                        96, 64, 30, 0, 0, bytes(item))
                    chunks.append(encoder.remux(packet, 100 + tick / 150)[0])
                if tick % 3 == 0:
                    frame = self.audio_frame(av, tick // 3)
                    before = frame.pts, frame.time_base, bytes(frame.planes[0])
                    chunks.append(encoder.encode_audio(frame, 100 + tick / 150)[0])
                    shared.append((frame, before))
            self.assertTrue(encoder.copy_video)
            self.assertEqual(encoder.audio_errors, 0)
            self.assertGreater(encoder.audio_frames, 80)
        finally:
            encoder.close()
        output = b''.join(chunks)
        with av.open(BytesIO(output), format='mpegts') as container:
            self.assertEqual(container.streams.video[0].codec_context.name, 'h264')
            self.assertEqual(container.streams.audio[0].codec_context.name, 'aac')
            video = list(container.decode(video=0))
        self.assertGreaterEqual(len(video), 55)
        # Compare against decoding the original packets: no generational loss.
        decoder = av.CodecContext.create('h264', 'r')
        original = [frame for packet in packets for frame in decoder.decode(av.Packet(bytes(packet)))]
        self.assertEqual([bytes(frame.planes[0]) for frame in video],
                         [bytes(frame.planes[0]) for frame in original[:len(video)]])
        with av.open(BytesIO(output), format='mpegts') as container:
            audio = list(container.decode(audio=0))
        self.assertGreater(sum(frame.samples for frame in audio), 80000)
        self.assertTrue(all(frame.sample_rate == 48000 for frame in audio))
        self.assertTrue(any(any(bytes(plane)) for frame in audio for plane in frame.planes))
        self.assertLess(abs(float(video[0].pts * video[0].time_base)
                            - float(audio[0].pts * audio[0].time_base)), .1)
        for frame, before in shared:
            self.assertEqual((frame.pts, frame.time_base, bytes(frame.planes[0])), before)

    def test_bad_audio_does_not_stop_video(self):
        import av
        encoder = stream_module._TransportEncoder(audio=True)
        frame = av.VideoFrame(64, 48, 'yuv420p')
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        try:
            encoder.encode(frame, 0)
            bad = self.audio_frame(av, 0)
            bad.sample_rate = 0
            encoder.encode_audio(bad, .01)
            self.assertEqual(encoder.audio_errors, 1)
            chunks = [encoder.encode(frame, number / 30)[0] for number in range(1, 20)]
            self.assertTrue(any(chunks))
        finally:
            encoder.close()

    def test_missing_audio_supplies_only_silence_without_blocking_video(self):
        import av
        encoder = stream_module._TransportEncoder(audio=True)
        frame = av.VideoFrame(64, 48, 'yuv420p')
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        try:
            chunks = [encoder.encode(frame, number / 30)[0] for number in range(30)]
            self.assertEqual(encoder.audio_frames, 0)
            self.assertGreater(encoder.silence_frames, 20)
            self.assertEqual(encoder.audio_errors, 0)
        finally:
            encoder.close()
        with av.open(BytesIO(b''.join(chunks)), format='mpegts') as container:
            self.assertGreater(len(list(container.decode(video=0))), 20)
        with av.open(BytesIO(b''.join(chunks)), format='mpegts') as container:
            audio = list(container.decode(audio=0))
            self.assertGreater(len(audio), 20)
            self.assertFalse(any(any(bytes(plane)) for frame in audio for plane in frame.planes))

    def test_audio_resampling_supports_mono_and_stereo_without_mutating_input(self):
        import av
        for rate, channels in ((16000, 1), (48000, 2)):
            with self.subTest(rate=rate, channels=channels):
                encoder = stream_module._TransportEncoder(audio=True)
                video = av.VideoFrame(64, 48, 'yuv420p')
                for plane in video.planes:
                    plane.update(bytes(plane.buffer_size))
                try:
                    for tick in range(150):
                        if tick % 5 == 0:
                            encoder.encode(video, tick / 150)
                        if tick % 3 == 0:
                            audio = self.audio_frame(av, tick // 3, rate=rate, channels=channels)
                            before = audio.pts, audio.time_base, bytes(audio.planes[0])
                            encoder.encode_audio(audio, tick / 150)
                            self.assertEqual((audio.pts, audio.time_base, bytes(audio.planes[0])), before)
                    self.assertEqual(encoder.audio_errors, 0)
                    self.assertGreater(encoder.audio_frames, 40)
                finally:
                    encoder.close()

    def test_real_ts_preserves_elapsed_time_and_shared_frame(self):
        try:
            import av
        except ImportError:
            self.skipTest('PyAV is tested in the media CI environment')
        frame = av.VideoFrame(1920, 1080, 'yuv420p')
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        frame.pts, frame.time_base = 9000, Fraction(1, 90000)
        before = [bytes(plane) for plane in frame.planes]
        encoder = stream_module._TransportEncoder()
        chunks = []
        try:
            for timestamp in (100, 100.1, 100.2, 100.5, 101, 101.1):
                chunks.append(encoder.encode(frame, timestamp))
        finally:
            encoder.close()
        self.assertTrue(chunks[0][1])
        self.assertTrue(chunks[4][1])
        with av.open(BytesIO(b''.join(chunk for chunk, key in chunks)), format='mpegts') as container:
            self.assertEqual(container.streams.video[0].codec_context.name, 'h264')
            frames = list(container.decode(video=0))
        self.assertEqual(len(frames), 6)
        self.assertEqual((frames[0].width, frames[0].height), (1920, 1080))
        self.assertEqual([round(float((item.pts - frames[0].pts) * item.time_base), 3)
                          for item in frames], [0, 0.1, 0.2, 0.5, 1, 1.1])
        # A new viewer can begin directly at the next keyframe's TS chunk.
        with av.open(BytesIO(b''.join(chunk for chunk, key in chunks[4:])), format='mpegts') as container:
            self.assertEqual(len(list(container.decode(video=0))), 2)
        self.assertEqual((frame.width, frame.height, frame.format.name), (1920, 1080, 'yuv420p'))
        self.assertEqual((frame.pts, frame.time_base), (9000, Fraction(1, 90000)))
        self.assertEqual([bytes(plane) for plane in frame.planes], before)

    def test_identity_reformat_does_not_change_input_timestamps(self):
        try:
            import av
        except ImportError:
            self.skipTest('PyAV is tested in the media CI environment')
        frame = av.VideoFrame(640, 480, 'yuv420p')
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        frame.pts, frame.time_base = 987654, Fraction(1, 1000)
        encoder = stream_module._TransportEncoder()
        try:
            chunk, key = encoder.encode(frame, 50)
            self.assertTrue(chunk)
            self.assertTrue(key)
        finally:
            encoder.close()
        self.assertEqual((frame.pts, frame.time_base), (987654, Fraction(1, 1000)))


if __name__ == '__main__':
    unittest.main()
