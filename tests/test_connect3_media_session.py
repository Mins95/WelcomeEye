"""Synthetic QV peer, fragmented byte stream, single-shot session lifecycle."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from load_integration import load
from test_connect3_media_protocol import KEY, aes, control_response, frame_bytes, media_response

s = load('connect3.session')
p = load('connect3.protocol')


class Writer:
    def __init__(self):
        self.writes = []
        self.closed = False
        self.close_count = 0
        self.drain = AsyncMock()
        self.wait_closed = AsyncMock()
    def write(self, data):
        self.writes.append(data)
    def is_closing(self):
        return self.closed
    def close(self):
        self.close_count += 1
        self.closed = True


def setup(result=0):
    raw = bytearray(32)
    raw[0], raw[9], raw[10], raw[11] = 0xA9, result, 2, 1
    return bytes(raw)


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reader, self.writer, self.obs = asyncio.StreamReader(), Writer(), {}
        self.session = s.QVSession('192.0.2.1', 8443, 'a'*64, KEY, 'SYNTHETIC_PASSWORD', self.obs)
        self.open = patch.object(s, 'open_media_tls', AsyncMock(return_value=(self.reader, self.writer)))
        self.connect = self.open.start()

    async def asyncTearDown(self):
        await self.session.close()
        self.open.stop()
        self.assertEqual(self.writer.close_count, 1 if self.connect.await_count else 0)
        self.assertIsNone(self.session._read_task)
        self.assertIsNone(self.session._password)
        self.assertIsNone(self.session._stream_key)

    def commands(self):
        return [data[0] if index == 0 else aes(data[:32], decrypt=True)[0]
                for index, data in enumerate(self.writer.writes)]

    async def test_fragmented_live_two_frames_and_single_teardown(self):
        wire = setup() + b''.join(control_response()) + b''.join(media_response(frame_bytes())) * 2
        frames = []
        received = asyncio.Event()
        async def on_frame(frame):
            frames.append(frame)
            if len(frames) == 2:
                received.set()
        task = asyncio.create_task(self.session.run(on_frame))
        for offset in range(0, len(wire), 3):
            self.reader.feed_data(wire[offset:offset+3])
            await asyncio.sleep(0)
        await asyncio.wait_for(received.wait(), 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await self.session.close()
        self.connect.assert_awaited_once()
        self.assertEqual(self.commands(), [0xA9, 1, 7])
        self.assertEqual(self.obs['media_packets'], 2)
        self.assertTrue(self.obs['play_accepted'])
        self.assertTrue(self.obs['teardown_sent'])
        self.assertTrue(self.obs['tcp_closed'])
        self.assertEqual(len(frames), 2)
        for secret in (KEY, 'SYNTHETIC_PASSWORD', '192.0.2.1', 'a'*64):
            self.assertNotIn(secret, repr(self.obs))
        play_header = aes(self.writer.writes[1][:32], decrypt=True)
        self.assertEqual(play_header[13:17], b'\1\0\1\1')  # idc1, action1, ids2->wire1

    async def test_refused_setup_no_play_and_no_retry(self):
        self.reader.feed_data(setup(result=1))
        with self.assertRaisesRegex(p.MediaProtocolError, '^media_setup_rejected$'):
            await self.session.run(AsyncMock())
        self.assertEqual(self.commands(), [0xA9])
        self.assertFalse(self.obs['teardown_attempted'])

    async def test_refused_play_teardown_once_no_retry(self):
        self.reader.feed_data(setup()+b''.join(control_response(result=1)))
        with self.assertRaisesRegex(p.MediaProtocolError, '^media_play_rejected$'):
            await self.session.run(AsyncMock())
        self.assertEqual(self.commands(), [0xA9, 1, 7])

    async def test_sha_rejection_and_remote_eof_close(self):
        self.reader.feed_data(setup()+b''.join(control_response(bad_sha=True)))
        with self.assertRaisesRegex(p.MediaProtocolError, '^control_sha_mismatch$'):
            await self.session.run(AsyncMock())
        self.assertEqual(self.commands(), [0xA9, 1, 7])

    async def test_eof_before_setup_never_sends_credentials(self):
        self.reader.feed_data(b'\xa9\0')
        self.reader.feed_eof()
        with self.assertRaises(asyncio.IncompleteReadError):
            await self.session.run(AsyncMock())
        self.assertEqual(self.commands(), [0xA9])

    async def test_initial_timeout_bounded_without_second_attempt(self):
        with patch.object(s, 'START_TIMEOUT', .01):
            with self.assertRaises(TimeoutError):
                await self.session.run(AsyncMock())
        self.assertEqual(self.commands(), [0xA9])

    async def test_keepalive_does_not_cancel_partial_packet_reader(self):
        response = b''.join(control_response())
        self.reader.feed_data(setup()+response[:33])
        got_frame = asyncio.Event()
        async def on_frame(frame):
            got_frame.set()
        with patch.object(s, 'KEEPALIVE_INTERVAL', .01):
            task = asyncio.create_task(self.session.run(on_frame))
            async with asyncio.timeout(1):
                while not self.obs.get('keepalives_sent'):
                    await asyncio.sleep(.001)
            self.reader.feed_data(response[33:]+b''.join(media_response(frame_bytes())))
            await asyncio.wait_for(got_frame.wait(), 1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.commands().count(1), 1)
        self.assertEqual(self.commands().count(7), 1)
        self.assertIn(0, self.commands())


if __name__ == '__main__':
    unittest.main()
