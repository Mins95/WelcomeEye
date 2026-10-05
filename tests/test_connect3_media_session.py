"""Synthetic QV peer, fragmented byte stream, single-shot session lifecycle."""
import asyncio
import json
import struct
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


def media_header(*, body_length, extension_length=0, offset=0, encrypted=False):
    raw = bytearray(32)
    raw[0], raw[15] = 0xA1, int(encrypted)
    struct.pack_into('<H', raw, 9, extension_length)
    struct.pack_into('<I', raw, 11, body_length)
    struct.pack_into('<H', raw, 16, offset)
    return aes(bytes(raw))


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
        self.assertEqual(self.obs['media_packets_accepted'], 2)
        self.assertEqual(self.obs['media_packets_rejected'], 0)
        self.assertEqual(self.obs['headers_received'], 4)
        self.assertEqual(self.obs['messages_received'], 4)
        self.assertEqual(self.obs['media_headers_received'], 2)
        self.assertEqual(self.obs['media_headers_rejected'], 0)
        self.assertEqual(self.obs['bytes_received'], len(wire))
        self.assertIsNone(self.obs['last_rejected_media_header'])
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
        self.assertEqual(self.obs['headers_received'], 1)
        self.assertEqual(self.obs['messages_received'], 1)
        self.assertEqual(self.obs['bytes_received'], 32)

    async def test_passive_format_and_command_diagnostics_never_store_content(self):
        observed_commands = []
        def observer(packet):
            observed_commands.append(packet.header.command)
            if packet.header.command == 0xFE:
                raise RuntimeError('Synthetic observer failure must not stop video')
        self.session.control_observer = observer
        secret = b'PRIVATE_ALARM_METADATA_DO_NOT_EXPORT'
        audio = bytearray(frame_bytes(frame_type=2, codec=4, payload=b'\xd5' * 160))
        audio[15] = 1
        struct.pack_into('<H', audio, 16, 8000)
        wire = (setup() + b''.join(control_response())
                + b''.join(control_response(command=0xFE, parameters=secret))
                + b''.join(media_response(bytes(audio))))
        self.reader.feed_data(wire)
        received = asyncio.Event()
        frames = []
        async def on_frame(frame):
            frames.append(frame)
            received.set()
        task = asyncio.create_task(self.session.run(on_frame))
        await asyncio.wait_for(received.wait(), 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.obs['control_command_counts'], {'1': 1, '254': 1})
        self.assertEqual(observed_commands, [1, 254])
        self.assertEqual(self.obs['control_observer_errors'], 1)
        self.assertEqual(self.obs['control_parameter_bytes'], len(secret))
        self.assertEqual(self.obs['frame_type_counts'], {'2': 1})
        self.assertEqual(self.obs['frame_codec_counts'], {'4': 1})
        self.assertEqual(self.obs['frame_formats'], [dict(frame_type=2, codec=4, payload_bytes=160)])
        self.assertTrue(frames[0].is_audio)
        self.assertEqual((frames[0].sample_rate, frames[0].channels), (8000, 1))
        for size in range(100):
            self.session._observe_frame(p.MediaFrame(2, 4, 8000, 0, .25, 0, 0, b'X' * size))
        self.assertEqual(len(self.obs['frame_formats']), 32)
        self.assertGreater(self.obs['frame_formats_overflow'], 0)
        text = json.dumps(self.obs)
        self.assertNotIn(secret.decode(), text)
        self.assertNotIn(secret.hex(), text)
        self.assertNotIn('packed_time', text)
        self.assertEqual(self.commands(), [0xA9, 1, 7])

    async def test_refused_play_teardown_once_no_retry(self):
        self.reader.feed_data(setup()+b''.join(control_response(result=1)))
        with self.assertRaisesRegex(p.MediaProtocolError, '^media_play_rejected$'):
            await self.session.run(AsyncMock())
        self.assertEqual(self.commands(), [0xA9, 1, 7])

    async def test_sha_rejection_and_remote_eof_close(self):
        wire = setup()+b''.join(control_response(bad_sha=True))
        self.reader.feed_data(wire)
        with self.assertRaisesRegex(p.MediaProtocolError, '^control_sha_mismatch$'):
            await self.session.run(AsyncMock())
        self.assertEqual(self.commands(), [0xA9, 1, 7])
        self.assertEqual(self.obs['bytes_received'], len(wire))
        self.assertEqual(self.obs['messages_received'], 2)
        self.assertEqual(self.obs['media_packets_rejected'], 0)

    async def test_eof_before_setup_never_sends_credentials(self):
        self.reader.feed_data(b'\xa9\0')
        self.reader.feed_eof()
        with self.assertRaises(asyncio.IncompleteReadError):
            await self.session.run(AsyncMock())
        self.assertEqual(self.commands(), [0xA9])
        self.assertEqual(self.obs['bytes_received'], 2)
        self.assertEqual(self.obs['headers_received'], 0)
        self.assertEqual(self.obs['messages_received'], 0)
        self.assertEqual(self.obs['last_receive_stage'], 'setup_header_read')

    async def test_initial_timeout_bounded_without_second_attempt(self):
        with patch.object(s, 'START_TIMEOUT', .01):
            with self.assertRaises(TimeoutError):
                await self.session.run(AsyncMock())
        self.assertEqual(self.commands(), [0xA9])
        self.assertEqual(self.obs['bytes_received'], 0)
        self.assertEqual(self.obs['headers_received'], 0)
        self.assertEqual(self.obs['messages_received'], 0)

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

    async def test_rejected_media_offset_does_not_read_body(self):
        for offset in (32, 33):
            with self.subTest(offset=offset):
                # Run a fresh fixture for the second malformed peer response.
                if offset == 33:
                    self.reader, self.writer, self.obs = asyncio.StreamReader(), Writer(), {}
                    self.session = s.QVSession('192.0.2.1', 8443, 'a'*64, KEY,
                                               'SYNTHETIC_PASSWORD', self.obs)
                    self.connect.return_value = (self.reader, self.writer)
                header = media_header(body_length=32, offset=offset)
                wire = setup() + b''.join(control_response()) + header
                self.reader.feed_data(wire + b'UNREAD_SYNTHETIC_BODY')
                with patch.object(self.reader, 'readexactly', wraps=self.reader.readexactly) as read:
                    with self.assertRaisesRegex(p.MediaProtocolError, '^media_offset$'):
                        await self.session.run(AsyncMock())
                self.assertEqual([call.args[0] for call in read.await_args_list], [32, 32, 32, 32])
                self.assertEqual(self.obs['bytes_received'], len(wire))
                self.assertEqual(self.obs['headers_received'], 3)
                self.assertEqual(self.obs['messages_received'], 2)
                self.assertEqual(self.obs['media_headers_received'], 1)
                self.assertEqual(self.obs['media_headers_rejected'], 1)
                self.assertEqual(self.obs['media_packets_rejected'], 1)
                self.assertEqual(self.obs['media_packets_accepted'], 0)
                self.assertEqual(self.obs['media_packets'], 0)
                metadata = self.obs['last_rejected_media_header']
                self.assertEqual(metadata['media_offset'], offset)
                self.assertFalse(metadata['offset_within_body'])
                self.assertEqual(metadata['header_validation_errors'], ['media_offset'])
                self.assertEqual(metadata['rejection_reason'], 'media_offset')
                self.assertEqual(self.obs['last_receive_stage'], 'media_header_rejected')
                self.assertEqual(self.commands(), [0xA9, 1, 7])
                self.assertEqual(self.writer.close_count, 1)

    async def test_rejected_extension_is_recorded_before_any_body_read(self):
        wire = setup() + b''.join(control_response()) + media_header(
            body_length=32, extension_length=48)
        self.reader.feed_data(wire)
        with patch.object(self.reader, 'readexactly', wraps=self.reader.readexactly) as read:
            with self.assertRaisesRegex(p.MediaProtocolError, '^media_extension_size$'):
                await self.session.run(AsyncMock())
        self.assertEqual(read.await_count, 4)
        self.assertEqual(self.obs['bytes_received'], len(wire))
        self.assertEqual(self.obs['media_headers_received'], 1)
        self.assertEqual(self.obs['media_headers_rejected'], 1)
        self.assertEqual(self.obs['media_packets_rejected'], 1)
        self.assertFalse(self.obs['last_media_header']['extension_within_body'])
        self.assertEqual(self.obs['last_rejected_media_header']['rejection_reason'], 'media_extension_size')

    async def test_eof_in_packet_header_counts_only_consumed_partial_bytes(self):
        wire = setup() + b''.join(control_response()) + b'PARTIAL'
        self.reader.feed_data(wire)
        self.reader.feed_eof()
        with self.assertRaises(asyncio.IncompleteReadError):
            await self.session.run(AsyncMock())
        self.assertEqual(self.obs['bytes_received'], len(wire))
        self.assertEqual(self.obs['headers_received'], 2)
        self.assertEqual(self.obs['messages_received'], 2)
        self.assertEqual(self.obs['media_headers_received'], 0)
        self.assertEqual(self.obs['media_packets_rejected'], 0)
        self.assertEqual(self.obs['last_receive_stage'], 'packet_header_read')
        self.assertEqual(self.commands(), [0xA9, 1, 7])

    async def test_eof_in_media_body_counts_partial_and_rejects_packet(self):
        wire = setup() + b''.join(control_response()) + media_header(body_length=32) + b'PARTIAL'
        self.reader.feed_data(wire)
        self.reader.feed_eof()
        with self.assertRaises(asyncio.IncompleteReadError):
            await self.session.run(AsyncMock())
        self.assertEqual(self.obs['bytes_received'], len(wire))
        self.assertEqual(self.obs['headers_received'], 3)
        self.assertEqual(self.obs['messages_received'], 2)
        self.assertEqual(self.obs['media_headers_received'], 1)
        self.assertEqual(self.obs['media_headers_rejected'], 0)
        self.assertEqual(self.obs['media_packets_rejected'], 1)
        self.assertEqual(self.obs['media_packets_accepted'], 0)
        self.assertEqual(self.obs['last_rejected_media_header']['rejection_reason'], 'incomplete_media_body')
        self.assertEqual(self.obs['last_receive_stage'], 'media_body_read')

    async def test_media_body_decode_failure_counts_complete_message(self):
        wire = setup() + b''.join(control_response()) + media_header(
            body_length=31, encrypted=True) + bytes(31)
        self.reader.feed_data(wire)
        with self.assertRaisesRegex(p.MediaProtocolError, '^unaligned_cipher_input$'):
            await self.session.run(AsyncMock())
        self.assertEqual(self.obs['bytes_received'], len(wire))
        self.assertEqual(self.obs['headers_received'], 3)
        self.assertEqual(self.obs['messages_received'], 3)
        self.assertEqual(self.obs['media_headers_received'], 1)
        self.assertEqual(self.obs['media_headers_rejected'], 0)
        self.assertEqual(self.obs['media_packets_rejected'], 1)
        self.assertEqual(self.obs['media_packets_accepted'], 0)
        self.assertEqual(self.obs['last_rejected_media_header']['rejection_reason'], 'unaligned_cipher_input')
        self.assertEqual(self.obs['last_receive_stage'], 'media_body_rejected')

    async def test_media_before_play_response_is_rejected_not_accepted(self):
        wire = setup() + b''.join(media_response(frame_bytes()))
        self.reader.feed_data(wire)
        with self.assertRaisesRegex(p.MediaProtocolError, '^media_before_play_acceptance$'):
            await self.session.run(AsyncMock())
        self.assertEqual(self.obs['bytes_received'], len(wire))
        self.assertEqual(self.obs['headers_received'], 2)
        self.assertEqual(self.obs['messages_received'], 2)
        self.assertEqual(self.obs['media_headers_received'], 1)
        self.assertEqual(self.obs['media_headers_rejected'], 0)
        self.assertEqual(self.obs['media_packets_rejected'], 1)
        self.assertEqual(self.obs['media_packets_accepted'], 0)
        self.assertEqual(self.obs['media_packets'], 0)
        self.assertEqual(self.obs['last_rejected_media_header']['rejection_reason'],
                         'media_before_play_acceptance')
        self.assertEqual(self.commands(), [0xA9, 1, 7])

    async def test_inactivity_timeout_preserves_one_partial_body_reader(self):
        prefix = setup() + b''.join(control_response()) + media_header(body_length=32)
        self.reader.feed_data(prefix + b'PARTIAL')
        with patch.object(s, 'INACTIVITY_TIMEOUT', .02), patch.object(
                self.reader, 'readexactly', wraps=self.reader.readexactly) as read:
            with self.assertRaisesRegex(TimeoutError, '^media_inactivity_timeout$'):
                await self.session.run(AsyncMock())
        self.assertEqual(read.await_count, 5)
        # StreamReader.readexactly keeps an unfinished read buffered on cancellation.
        self.assertEqual(self.obs['bytes_received'], len(prefix))
        self.assertEqual(self.obs['headers_received'], 3)
        self.assertEqual(self.obs['messages_received'], 2)
        self.assertEqual(self.obs['media_packets_rejected'], 0)
        self.assertEqual(self.obs['last_receive_stage'], 'media_body_read')
        self.assertEqual(self.commands(), [0xA9, 1, 7])

    async def test_cancel_partial_header_preserves_counts_and_closes_once(self):
        prefix = setup() + b''.join(control_response())
        self.reader.feed_data(prefix + b'PARTIAL')
        task = asyncio.create_task(self.session.run(AsyncMock()))
        async with asyncio.timeout(1):
            while not self.obs.get('play_accepted'):
                await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.gather(self.session.close(), self.session.close())
        self.assertEqual(self.obs['bytes_received'], len(prefix))
        self.assertEqual(self.obs['headers_received'], 2)
        self.assertEqual(self.obs['messages_received'], 2)
        self.assertEqual(self.obs['media_packets_rejected'], 0)
        self.assertEqual(self.commands(), [0xA9, 1, 7])
        self.assertEqual(self.writer.close_count, 1)

    async def test_media_diagnostic_allowlist_drops_private_fields_and_reasons(self):
        metadata = {'command': 0xA1, 'body_length': 32, 'extension_length': 0,
            'media_offset': 32, 'media_encrypted': False,
            'extension_within_body': True, 'offset_within_body': False,
            'offset_before_extension': False,
            'header_validation_errors': ['media_offset', 'PRIVATE_EXCEPTION_TEXT'],
            'stream_key': KEY, 'timestamp': 123456789, 'plaintext': b'PRIVATE_HEADER',
            'password': 'SYNTHETIC_PASSWORD', 'payload': b'PRIVATE_PAYLOAD',
            'raw_header_hex': 'PRIVATE_HEADER_HEX'}
        safe = s._safe_media_header(metadata)
        self.assertEqual(set(safe), s.MEDIA_HEADER_FIELDS | {'header_validation_errors'})
        self.assertEqual(safe['header_validation_errors'], ['media_offset'])
        serialized = json.dumps(safe)
        for private in ('PRIVATE', KEY, 'SYNTHETIC_PASSWORD', '123456789'):
            self.assertNotIn(private, serialized)
        self.obs.update(media_packets_rejected=0, media_headers_rejected=0)
        self.session._reject_media(metadata, 'PRIVATE_EXCEPTION_TEXT', header=True)
        self.assertEqual(self.obs['last_rejected_media_header']['rejection_reason'], 'media_packet_invalid')
        self.assertNotIn('PRIVATE', json.dumps(self.obs))


if __name__ == '__main__':
    unittest.main()
