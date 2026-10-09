"""Synthetic QV TCP security boundaries; only ephemeral loopback peer sockets."""
import asyncio
from hashlib import sha256
import json
import struct
import unittest
from unittest.mock import AsyncMock, Mock, patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from load_integration import load
from test_connect3_media_protocol import KEY, MATERIAL, aes, control_response, frame_bytes, media_response
from test_connect3_media_session import Writer

tls = load('connect3.tls')
session = load('connect3.session')
qv = load('connect3.protocol')
AUTH = 'SYNTHETIC_PRIVATE_LOCAL_AUTHCODE'
PASSWORD = sha256(AUTH.encode()).hexdigest()


def setup(*, mode=2, sha=1, result=0):
    raw = bytearray(32)
    raw[0], raw[9], raw[10], raw[11] = 0xA9, result, mode, sha
    return bytes(raw)


def audio_frame_bytes(payload=b'\xd5' * 160, *, codec=4):
    """Independent native QV20 audio metadata, synthetic 8 kHz mono."""
    header = bytearray(20)
    header[:4] = b'\0\0\1\xe3'
    struct.pack_into('<I', header, 4, len(payload))
    header[14], header[15] = codec, 1
    struct.pack_into('<H', header, 16, 8000)
    return bytes(header) + payload


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reader, self.writer, self.obs = asyncio.StreamReader(), Writer(), {}
        self.current = session.QVSession('192.0.2.1', 34567, '', KEY, PASSWORD,
            self.obs, transport='connect3_tcp', cgi_verified=True)
        self.open = patch.object(session, 'open_connect3_media_tcp',
            AsyncMock(return_value=(self.reader, self.writer)))
        self.connection = self.open.start()

    async def asyncTearDown(self):
        await self.current.close()
        self.open.stop()
        self.assertEqual(self.writer.close_count, 1 if self.connection.await_count else 0)
        self.assertIsNone(self.current._password)
        self.assertIsNone(self.current._stream_key)
        self.assertIsNone(self.current._read_task)
        for private in (KEY, PASSWORD, AUTH, '192.0.2.1'):
            self.assertNotIn(private, json.dumps(self.obs))

    def commands(self):
        return [value[0] if position == 0 else aes(value[:32], decrypt=True)[0]
                for position, value in enumerate(self.writer.writes)]

    async def test_no_verified_https_origin_or_wrong_fixed_port_is_rejected_before_io(self):
        with patch.object(tls.asyncio, 'open_connection') as connect:
            for verified in (False, None, 1, 'true'):
                with self.assertRaisesRegex(tls.MediaTLSFailure, '^connect3_tcp_verified_cgi_required$'):
                    session.QVSession('192.0.2.1', 34567, '', KEY, PASSWORD, {},
                        transport='connect3_tcp', cgi_verified=verified)
            for port in (8443, 0, True, 34568):
                with self.assertRaisesRegex(tls.MediaTLSFailure, '^invalid_media_transport_policy$'):
                    session.QVSession('192.0.2.1', port, '', KEY, PASSWORD, {},
                        transport='connect3_tcp', cgi_verified=True)
        connect.assert_not_called()

    async def test_every_downgrade_fails_before_cipher_material_play_or_retry(self):
        for mode, sha in ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0)):
            reader, stream, obs = asyncio.StreamReader(), Writer(), {}
            # A later strong response is not a retry/fallback authorization.
            reader.feed_data(setup(mode=mode, sha=sha) + setup())
            current = session.QVSession('192.0.2.1', 34567, '', KEY, PASSWORD, obs,
                transport='connect3_tcp', cgi_verified=True)
            with patch.object(session, 'open_connect3_media_tcp', AsyncMock(return_value=(reader, stream))) as connect, \
                    patch.object(session, 'open_media_tls') as secure, \
                    patch.object(session, 'open_r002_media_tcp') as r002, \
                    patch.object(qv, 'CipherMaterial') as material, \
                    patch.object(qv, 'build_play_request') as play:
                with self.assertRaisesRegex(qv.MediaProtocolError, '^connect3_tcp_unsafe_crypto_mode$'):
                    await current.run(AsyncMock())
            connect.assert_awaited_once()
            for other in (secure, r002, material, play):
                other.assert_not_called()
            self.assertEqual(stream.writes, [b'\xa9' + bytes(31)])
            self.assertFalse(obs['play_sent'])
            self.assertFalse(obs['teardown_attempted'])
            self.assertTrue(obs['tcp_closed'])
            self.assertEqual(obs['credential_protection'], 'blocked')
            self.assertIsNone(current._password)

    async def test_invalid_or_refused_setup_never_sends_play(self):
        for response in (setup(mode=3), setup(sha=2), setup(result=1), bytes(32), b'\xa9'):
            reader, stream = asyncio.StreamReader(), Writer()
            reader.feed_data(response)
            reader.feed_eof()
            current = session.QVSession('192.0.2.1', 34567, '', KEY, PASSWORD, {},
                transport='connect3_tcp', cgi_verified=True)
            with patch.object(session, 'open_connect3_media_tcp', AsyncMock(return_value=(reader, stream))):
                with self.assertRaises((qv.MediaProtocolError, asyncio.IncompleteReadError)):
                    await current.run(AsyncMock())
            self.assertEqual(stream.writes, [b'\xa9' + bytes(31)])
            self.assertEqual(stream.close_count, 1)

    async def test_short_or_invalid_private_cgi_key_stops_before_play(self):
        for key in ('short', '', None, 'NON_ASCII_é' * 8):
            reader, stream = asyncio.StreamReader(), Writer()
            reader.feed_data(setup())
            current = session.QVSession('192.0.2.1', 34567, '', key, PASSWORD, {},
                transport='connect3_tcp', cgi_verified=True)
            with patch.object(session, 'open_connect3_media_tcp', AsyncMock(return_value=(reader, stream))), \
                    patch.object(qv, 'build_play_request') as play:
                with self.assertRaises(qv.MediaProtocolError):
                    await current.run(AsyncMock())
            play.assert_not_called()
            self.assertEqual(stream.writes, [b'\xa9' + bytes(31)])
            self.assertIsNone(current._password)

    async def test_bad_checksum_or_wrong_key_ack_never_accepts_media(self):
        good_ack = b''.join(control_response())
        cipher = Cipher(algorithms.AES(b'W' * 32), modes.CBC(b'0' * 16)).encryptor()
        wrong_key_header = cipher.update(aes(good_ack[:32], decrypt=True)) + cipher.finalize()
        for ack in (b''.join(control_response(bad_sha=True)), wrong_key_header + good_ack[32:]):
            reader, stream, obs = asyncio.StreamReader(), Writer(), {}
            reader.feed_data(setup() + ack)
            reader.feed_eof()
            current = session.QVSession('192.0.2.1', 34567, '', KEY, PASSWORD, obs,
                transport='connect3_tcp', cgi_verified=True)
            frames = AsyncMock()
            with patch.object(session, 'open_connect3_media_tcp', AsyncMock(return_value=(reader, stream))) as connect:
                with self.assertRaises((qv.MediaProtocolError, asyncio.IncompleteReadError)):
                    await current.run(frames)
            frames.assert_not_called()
            connect.assert_awaited_once()
            self.assertTrue(obs['play_sent'])
            self.assertFalse(obs['play_accepted'])
            for wire in stream.writes:
                self.assertNotIn(PASSWORD.encode(), wire)
                self.assertNotIn(b'adminapp2', wire)

    async def test_output_is_disabled_even_when_session_state_claims_accepted(self):
        self.current._writer = self.writer
        self.current._material = MATERIAL
        self.obs['play_accepted'] = True
        with patch.object(session, 'build_unlock_request') as build:
            with self.assertRaisesRegex(session.OutputFailure, '^connect3_tcp_outputs_disabled$'):
                await self.current.execute_output(1, 'NEVER_ENCODE_OPENING_CODE')
            with self.assertRaisesRegex(session.OutputFailure, '^connect3_tcp_outputs_disabled$'):
                await self.current._send(b'NEVER_SEND_PHYSICAL_PACKET', physical=True)
        build.assert_not_called()
        self.assertEqual(self.writer.writes, [])
        self.current._writer = None  # No socket was opened by this policy test.

    async def test_write_boundary_rejects_clear_commands_repeated_setup_and_nonlive_orders(self):
        self.current._writer = self.writer
        self.obs['messages_sent'] = 0
        await self.current._send(b'\xa9' + bytes(31))
        with self.assertRaisesRegex(qv.MediaProtocolError, '^connect3_tcp_setup_only$'):
            await self.current._send(b'\xa9' + bytes(31))
        self.current._material = MATERIAL
        self.obs['setup_accepted'] = True
        for packet in (b'adminapp2&&' + PASSWORD.encode(), b''.join(control_response(command=0xFE))):
            with self.assertRaises(qv.MediaProtocolError):
                await self.current._send(packet)
        self.assertEqual(self.writer.writes, [b'\xa9' + bytes(31)])
        self.current._writer = None

    async def test_timeout_and_cancel_send_only_public_setup_and_close_once(self):
        with patch.object(session, 'START_TIMEOUT', .01):
            with self.assertRaises(TimeoutError):
                await self.current.run(AsyncMock())
        self.assertEqual(self.writer.writes, [b'\xa9' + bytes(31)])
        reader, stream, obs = asyncio.StreamReader(), Writer(), {}
        current = session.QVSession('192.0.2.1', 34567, '', KEY, PASSWORD, obs,
            transport='connect3_tcp', cgi_verified=True)
        with patch.object(session, 'open_connect3_media_tcp', AsyncMock(return_value=(reader, stream))):
            task = asyncio.create_task(current.run(AsyncMock()))
            async with asyncio.timeout(1):
                while not obs.get('setup_sent'):
                    await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        await asyncio.gather(current.close(), current.close())
        self.assertEqual(stream.writes, [b'\xa9' + bytes(31)])
        self.assertEqual(stream.close_count, 1)
        self.assertIsNone(current._password)
        self.assertIsNone(current._stream_key)


class LoopbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_tcp_one_encrypted_play_shared_video_audio_keepalive_and_teardown(self):
        commands, captured, handler_errors = [], [], []
        frames, hooks = [], Mock()
        finished, video = asyncio.Event(), asyncio.Event()
        handlers = set()
        async def peer(reader, stream):
            handlers.add(asyncio.current_task())
            try:
                public = await reader.readexactly(32)
                self.assertEqual(public, b'\xa9' + bytes(31))
                captured.append(public)
                commands.append(0xA9)
                stream.write(setup())
                await stream.drain()
                expected = 1
                while True:
                    encrypted_header = await reader.readexactly(32)
                    header = aes(encrypted_header, decrypt=True)
                    if expected == 1:
                        self.assertEqual(header[0], 1)
                    else:
                        self.assertIn(header[0], (0, 7))
                    size = struct.unpack_from('<H', header, 9)[0]
                    encrypted_body = await reader.readexactly(size)
                    captured.append(encrypted_header + encrypted_body)
                    commands.append(header[0])
                    body = aes(encrypted_body, decrypt=True)
                    if header[0] == 1:
                        parameter_length = struct.unpack_from('<H', header, 11)[0]
                        params = b'adminapp2&&' + PASSWORD.encode() + b'\0\0'
                        self.assertEqual(body[:parameter_length], params)
                        self.assertEqual(body[parameter_length:parameter_length + 32],
                                         sha256(header + params).digest())
                        wire = (b''.join(control_response())
                            + b''.join(control_response(command=0xFE, parameters=b'PRIVATE_ALARM'))
                            + b''.join(media_response(audio_frame_bytes()))
                            + b''.join(media_response(frame_bytes(frame_type=7, codec=4)))
                            + b''.join(media_response(frame_bytes())))
                        stream.write(wire)
                        await stream.drain()
                        expected = 0
                    else:
                        self.assertEqual(body[:32], sha256(header).digest())
                        if header[0] == 7:
                            break
                self.assertEqual(await reader.read(), b'')
            except Exception as exc:
                handler_errors.append(exc)
            finally:
                await tls.close_writer(stream)
                handlers.discard(asyncio.current_task())
                finished.set()
        server = await asyncio.start_server(peer, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        real_open = asyncio.open_connection
        async def only_loopback(host, configured_port, **kwargs):
            self.assertEqual((host, configured_port), ('127.0.0.1', 34567))
            self.assertNotIn('ssl', kwargs)
            return await real_open('127.0.0.1', port, **kwargs)
        obs = {}
        current = session.QVSession('127.0.0.1', 34567, '', KEY, PASSWORD, obs,
            transport='connect3_tcp', cgi_verified=True)
        current.control_observer = hooks
        async def on_frame(frame):
            frames.append(frame)
            if frame.is_h264:
                video.set()
        try:
            with patch.object(tls.asyncio, 'open_connection', side_effect=only_loopback) as connect, \
                    patch.object(session, 'open_media_tls') as secure, \
                    patch.object(session, 'open_r002_media_tcp') as r002, \
                    patch.object(session, 'KEEPALIVE_INTERVAL', .01):
                task = asyncio.create_task(current.run(on_frame))
                try:
                    # Debug traceback collection on a mounted Windows source
                    # tree is slow; this is a harness deadline, not a change to
                    # the production connect/start/inactivity deadlines.
                    await asyncio.wait_for(video.wait(), 10)
                    async with asyncio.timeout(10):
                        while not obs.get('keepalives_sent'):
                            await asyncio.sleep(0)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await asyncio.wait_for(finished.wait(), 10)
            connect.assert_awaited_once()
            secure.assert_not_called()
            r002.assert_not_called()
            hooks.assert_not_called()
            self.assertEqual(commands[:2], [0xA9, 1])
            self.assertEqual(commands[-1], 7)
            self.assertGreaterEqual(commands.count(0), 1)
            self.assertEqual(set(commands[2:-1]), {0})
            self.assertEqual(len(frames), 2)
            self.assertTrue(frames[0].is_audio)
            self.assertEqual((frames[0].frame_type, frames[0].codec,
                              frames[0].sample_rate, frames[0].channels), (3, 4, 8000, 1))
            self.assertTrue(frames[1].is_h264)
            self.assertEqual(obs['tcp_nonvideo_frames_ignored'], 1)
            self.assertEqual(obs['credential_protection'], 'qv_aes256_sha256')
            self.assertEqual(obs['media_transport'], 'connect3_tcp')
            self.assertEqual(obs['media_transport_selected'], 'connect3_tcp')
            self.assertEqual(obs['media_port_selected'], 34567)
            self.assertTrue(obs['media_tcp_connected'])
            self.assertFalse(obs['media_tls_verified'])
            self.assertFalse(obs['media_peer_authenticated'])
            self.assertFalse(obs['setup_transcript_authenticated'])
            self.assertFalse(obs['media_integrity_verified'])
            self.assertFalse(obs['media_replay_protected'])
            self.assertTrue(obs['tcp_closed'])
            self.assertEqual(handler_errors, [])
            for private in (b'adminapp2', PASSWORD.encode(), AUTH.encode(), KEY.encode()):
                self.assertNotIn(private, b''.join(captured))
            self.assertIsNone(current._password)
            self.assertIsNone(current._stream_key)
        finally:
            server.close()
            await server.wait_closed()
            pending = tuple(handlers)
            for handler in pending:
                handler.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def test_tcp_endpoint_validation_is_fixed_port_unicast_only(self):
        with patch.object(tls.asyncio, 'open_connection') as connect:
            for host, port in (('invalid', 34567), ('0.0.0.0', 34567),
                               ('224.0.0.1', 34567), ('255.255.255.255', 34567),
                               ('127.0.0.1', 8443), ('127.0.0.1', True)):
                with self.assertRaises(tls.MediaTLSFailure):
                    await tls.open_connect3_media_tcp(host, port, {})
        connect.assert_not_called()

    async def test_refused_plain_socket_has_no_tcp_connected_or_tls_success(self):
        for opener in (tls.open_connect3_media_tcp, tls.open_r002_media_tcp):
            observation = {}
            with patch.object(tls.asyncio, 'open_connection',
                              AsyncMock(side_effect=ConnectionRefusedError('PRIVATE_ENDPOINT'))) as connect:
                with self.assertRaises(ConnectionRefusedError):
                    await opener('127.0.0.1', 34567, observation)
            connect.assert_awaited_once()
            self.assertFalse(observation['media_tcp_connected'])
            self.assertFalse(observation['media_tls_verified'])
            self.assertEqual(observation['stage'], 'media_tcp_connect')
            self.assertNotIn('PRIVATE', repr(observation))


if __name__ == '__main__':
    unittest.main()
