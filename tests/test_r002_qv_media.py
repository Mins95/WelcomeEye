"""Offline transport regressions; synthetic protocol bytes are not R002 evidence."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
from test_connect3_media_protocol import KEY, MATERIAL, aes, control_response, frame_bytes, media_response
from test_connect3_media_session import Writer, setup
from test_connect3_talk import open_response

tls = load('connect3.tls')
session = load('connect3.session')
live = load('connect3.live')
talk = load('connect3.talk')
qv = load('connect3.protocol')
cgi = load('connect3.cgi')


class PlainSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.handlers = set()
        self.handler_errors = []
        self.server = None
        self.connections = []
        self.commands = []

    async def asyncTearDown(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for task in tuple(self.handlers):
            task.cancel()
        await asyncio.gather(*tuple(self.handlers), return_exceptions=True)
        self.assertEqual(self.handler_errors, [])

    async def peer(self, callback):
        async def serve(reader, writer):
            self.handlers.add(asyncio.current_task())
            try:
                await callback(reader, writer)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.handler_errors.append(error)
            finally:
                writer.close()
                await writer.wait_closed()
                self.handlers.discard(asyncio.current_task())
        self.server = await asyncio.start_server(serve, '127.0.0.1', 0)
        self.port = self.server.sockets[0].getsockname()[1]
        real_open = asyncio.open_connection

        async def loopback(host, port, **kwargs):
            # Assert the production policy's endpoint, but connect exclusively
            # to this test's ephemeral loopback socket. No device is contacted.
            self.assertEqual((host, port), ('127.0.0.1', 34567))
            self.assertNotIn('ssl', kwargs)
            self.connections.append((host, port))
            return await real_open('127.0.0.1', self.port, **kwargs)
        return patch.object(tls.asyncio, 'open_connection', side_effect=loopback)

    async def test_explicit_plain_policy_opens_once_without_writing_credentials(self):
        received = []
        finished = asyncio.Event()

        async def serve(reader, writer):
            received.append(await reader.read())
            finished.set()

        connection = await self.peer(serve)
        observation = {}
        with connection, patch.object(tls, '_context') as context:
            _, writer = await tls.open_r002_media_tcp('127.0.0.1', 34567, observation)
            await tls.close_writer(writer)
            await asyncio.wait_for(finished.wait(), 1)
        self.assertEqual(received, [b''])
        self.assertEqual(len(self.connections), 1)
        context.assert_not_called()
        self.assertEqual(observation['media_transport'], 'r002_tcp')
        self.assertEqual(observation['media_tls_policy'], 'r002_apk_plain_tcp')
        self.assertFalse(observation['media_tls_verified'])
        self.assertNotIn('127.0.0.1', repr(observation))

    async def test_plain_session_uses_one_reader_and_one_teardown(self):
        received_frame = asyncio.Event()
        finished = asyncio.Event()
        password = 'SYNTHETIC_PASSWORD'
        play_size = len(qv.build_play_request(MATERIAL, username='adminapp2',
            password=password, channel=1, stream=1, timestamp_seconds=0))

        async def serve(reader, writer):
            self.assertEqual(await reader.readexactly(32), qv.build_setup_request())
            self.commands.append(0xA9)
            writer.write(setup())
            await writer.drain()
            play = await reader.readexactly(play_size)
            self.commands.append(aes(play[:32], decrypt=True)[0])
            wire = b''.join(control_response()) + b''.join(media_response(frame_bytes()))
            for offset in range(0, len(wire), 3):
                writer.write(wire[offset:offset + 3])
                await writer.drain()
                await asyncio.sleep(0)
            teardown = await reader.readexactly(len(qv.build_teardown(MATERIAL, timestamp_seconds=0)))
            self.commands.append(aes(teardown[:32], decrypt=True)[0])
            self.assertEqual(await reader.read(), b'')
            finished.set()

        connection = await self.peer(serve)
        observation = {}
        current = session.QVSession('127.0.0.1', 34567, '', KEY, password,
                                    observation, transport='r002_tcp')
        async def on_frame(frame):
            received_frame.set()
        with connection, patch.object(session, 'open_media_tls') as secure:
            task = asyncio.create_task(current.run(on_frame))
            try:
                await asyncio.wait_for(received_frame.wait(), 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await current.close()
            await asyncio.wait_for(finished.wait(), 1)
        secure.assert_not_called()
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(self.commands, [0xA9, 1, 7])
        self.assertEqual(observation['media_packets_accepted'], 1)
        self.assertTrue(observation['play_accepted'])
        self.assertTrue(observation['teardown_sent'])
        self.assertTrue(observation['tcp_closed'])
        self.assertIsNone(current._read_task)
        self.assertIsNone(current._stream_key)
        self.assertIsNone(current._password)

    async def test_talk_inherits_plain_policy_with_its_dedicated_socket(self):
        finished = asyncio.Event()
        password = 'SYNTHETIC_PASSWORD'
        open_size = len(talk.build_open(MATERIAL, password, timestamp_seconds=0))
        request_size = len(talk.build_request(MATERIAL, sending=True, enabled=True,
            codec_index=0, timestamp_seconds=0))

        async def serve(reader, writer):
            self.assertEqual(await reader.readexactly(32), talk.build_setup())
            self.commands.append(0xA9)
            writer.write(setup())
            await writer.drain()
            raw = await reader.readexactly(open_size)
            self.commands.append(aes(raw[:32], decrypt=True)[0])
            writer.write(open_response())
            await writer.drain()
            for command in (12, 13):
                raw = await reader.readexactly(request_size)
                self.commands.append(aes(raw[:32], decrypt=True)[0])
                writer.write(b''.join(control_response(command=command)))
                await writer.drain()
            raw = await reader.readexactly(request_size)
            self.commands.append(aes(raw[:32], decrypt=True)[0])
            raw = await reader.readexactly(len(qv.build_teardown(MATERIAL, timestamp_seconds=0)))
            self.commands.append(aes(raw[:32], decrypt=True)[0])
            self.assertEqual(await reader.read(), b'')
            finished.set()

        connection = await self.peer(serve)
        video_session = session.QVSession('127.0.0.1', 34567, '', KEY, password,
                                          {}, transport='r002_tcp')
        hub = SimpleNamespace(stopped=False)
        hub.live = live.LiveMedia(hub)
        hub.live.connected = True
        hub.live.session = video_session
        hub.live.observation['play_accepted'] = True
        microphone = talk.Talkback(hub)
        with connection, patch.object(talk, 'open_media_tls') as secure:
            try:
                await microphone.start('viewer')
                self.assertTrue(microphone.active)
                self.assertIs(hub.live.session, video_session)
            finally:
                await microphone.stop('viewer')
            await asyncio.wait_for(finished.wait(), 1)
        secure.assert_not_called()
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(self.commands, [0xA9, 11, 12, 13, 13, 7])
        self.assertTrue(hub.live.connected)
        self.assertFalse(microphone.diagnostics['media_tls_verified'])
        self.assertIsNone(microphone._read_task)
        self.assertIsNone(microphone._material)


class PolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_microphone_stops_when_live_close_begins_before_connected_is_cleared(self):
        current = SimpleNamespace(_close_task=None)
        hub = SimpleNamespace(stopped=False, live=SimpleNamespace(connected=True, session=current))
        microphone = talk.Talkback(hub)
        microphone._live_session = current
        microphone.owner = 'viewer'
        microphone.active = microphone._requested = True
        microphone.heartbeat('viewer')
        encoder = SimpleNamespace(feed=Mock(return_value=[b'NEVER_SEND_AFTER_VIDEO_CLOSE']))
        microphone._encoder = encoder
        writer = microphone._writer = Writer()
        self.assertTrue(microphone._live_valid())
        # Session cleanup has started but a pending teardown/drain means the
        # outer LiveMedia cleanup has not yet cleared connected or identity.
        current._close_task = object()
        await microphone.feed('viewer', object())
        encoder.feed.assert_not_called()
        self.assertEqual(writer.writes, [])
        self.assertTrue(writer.closed)
        self.assertFalse(microphone.active)
        self.assertIsNone(microphone.owner)
        self.assertTrue(hub.live.connected)
        self.assertEqual(microphone.diagnostics['last_error_reason'], 'microphone_heartbeat_or_media_lost')

    async def test_plain_policy_rejects_other_ports_and_invalid_hosts_without_io(self):
        with patch.object(tls.asyncio, 'open_connection') as connection:
            for host, port in (('127.0.0.1', 8443), ('127.0.0.1', 0),
                               ('127.0.0.1', True), ('0.0.0.0', 34567),
                               ('224.0.0.1', 34567), ('255.255.255.255', 34567),
                               ('invalid', 34567)):
                with self.subTest(host=host, port=port), self.assertRaises(tls.MediaTLSFailure):
                    await tls.open_r002_media_tcp(host, port, {})
        connection.assert_not_called()
        for transport, port in (('plain', 34567), ('udt', 34567), ('r002_tcp', 8443)):
            with self.assertRaisesRegex(tls.MediaTLSFailure, '^invalid_media_transport_policy$'):
                session.QVSession('192.0.2.1', port, '', KEY, 'SYNTHETIC_PASSWORD',
                                  {}, transport=transport)

    async def test_default_and_explicit_tls_failures_never_try_plain_or_send(self):
        for options in ({}, {'transport': 'tls'}):
            with self.subTest(options=options):
                observation = {}
                current = session.QVSession('192.0.2.1', 8443, 'a' * 64, KEY,
                    'SYNTHETIC_PASSWORD', observation, **options)
                with patch.object(session, 'open_media_tls', AsyncMock(side_effect=
                        tls.MediaTLSFailure('media_certificate_pin_mismatch'))) as secure, \
                        patch.object(session, 'open_r002_media_tcp') as plain:
                    with self.assertRaisesRegex(tls.MediaTLSFailure, 'media_certificate_pin_mismatch'):
                        await current.run(AsyncMock())
                secure.assert_awaited_once()
                plain.assert_not_called()
                self.assertEqual(observation['messages_sent'], 0)
                self.assertFalse(observation['setup_sent'])
                self.assertTrue(observation['tcp_closed'])

    async def test_talk_tls_failure_never_tries_plain(self):
        current = object()
        hub = SimpleNamespace(stopped=False, live=SimpleNamespace(connected=True,
            session=current, talk_parameters=lambda: dict(host='192.0.2.1', port=8443,
                pin='a' * 64, stream_key=KEY, password='SYNTHETIC_PASSWORD', transport='tls')))
        microphone = talk.Talkback(hub)
        with patch.object(talk, 'open_media_tls', AsyncMock(side_effect=
                tls.MediaTLSFailure('media_certificate_pin_mismatch'))) as secure, \
                patch.object(talk, 'open_r002_media_tcp') as plain:
            with self.assertRaises(tls.MediaTLSFailure):
                await microphone.start('viewer')
        secure.assert_awaited_once()
        plain.assert_not_called()
        self.assertFalse(microphone.diagnostics['setup_sent'])
        self.assertIsNone(microphone.owner)

    async def test_fresh_endpoint_rejection_prevents_cgi_and_media(self):
        hub = SimpleNamespace(stopped=False, _task=None, capabilities=SimpleNamespace(live_media=True),
            entry=SimpleNamespace(data={'host': '192.0.2.1', 'auth_code': 'SYNTHETIC_PASSWORD'}),
            prepare_media_endpoint=AsyncMock(side_effect=cgi.CGIError('r002_identity_not_matched')),
            _notify=Mock())
        current = live.LiveMedia(hub)
        with patch.object(live, 'read_stream_material') as read, patch.object(live, 'QVSession') as factory:
            with self.assertRaises(RuntimeError):
                await current.acquire('viewer')
        hub.prepare_media_endpoint.assert_awaited_once()
        read.assert_not_called()
        factory.assert_not_called()
        self.assertEqual(current.observation['last_error_reason'], 'r002_identity_not_matched')
        self.assertFalse(current.consumers)

    async def test_endpoint_hook_precedes_pinned_cgi_and_supplies_only_media_transport(self):
        events = []
        endpoint = {'port': 34567, 'cgi_port': 443, 'transport': 'r002_tcp'}
        async def prepare(observation):
            events.append('fresh_discovery')
            return endpoint
        async def read(*args, diagnostics=None, **kwargs):
            events.append('cgi')
            self.assertEqual(kwargs, {'port': 443, 'certificate_sha256': 'a' * 64})
            diagnostics['authentication_status'] = 'accepted'
            return cgi.StreamMaterial(KEY)
        async def run(callback):
            events.append('media')
            raise qv.MediaProtocolError('media_setup_rejected')
        selected = SimpleNamespace(run=run, close=AsyncMock())
        hub = SimpleNamespace(stopped=False, _task=None, capabilities=SimpleNamespace(live_media=True),
            entry=SimpleNamespace(data={'host': '192.0.2.1', 'auth_code': 'SYNTHETIC_PASSWORD',
                'certificate_sha256': 'a' * 64, 'cgi_port': 9999, 'media_port': 8443}),
            prepare_media_endpoint=prepare, _notify=Mock())
        current = live.LiveMedia(hub)
        with patch.object(live, 'read_stream_material', side_effect=read) as reading, \
                patch.object(live, 'discover') as default_discovery, \
                patch.object(live, 'VideoDecoder'), patch.object(live, 'AudioDecoder'), \
                patch.object(live, 'QVSession', return_value=selected) as factory:
            with self.assertRaises(RuntimeError):
                await current.acquire('viewer')
        self.assertEqual(events, ['fresh_discovery', 'cgi', 'media'])
        reading.assert_awaited_once()
        default_discovery.assert_not_called()
        self.assertEqual(factory.call_args.args[:3], ('192.0.2.1', 34567, 'a' * 64))
        self.assertEqual(factory.call_args.kwargs, {'transport': 'r002_tcp'})
        selected.close.assert_awaited_once()
        self.assertEqual(hub._authentication['status'], 'accepted')


if __name__ == '__main__':
    unittest.main()
