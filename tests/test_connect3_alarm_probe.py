"""Real loopback control setup; no intercom, cloud, video or physical actions."""
import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load

ring = load('connect3.ring_listener')
protocol = load('connect3.alarm_protocol')


def reply(encryption=2, sha=1, command=0xA9, result=0):
    header = bytearray(32)
    header[0], header[9], header[10], header[11] = command, result, encryption, sha
    return bytes(header)


class SecretGuard(dict):
    def get(self, key, default=None):
        if key in ('auth_code', 'opening_code', 'credential_device_uid', 'stream_key'):
            raise AssertionError('Credential read by unauthenticated probe')
        return super().get(key, default)


def hub():
    return SimpleNamespace(entry=SimpleNamespace(data=SecretGuard(host='127.0.0.1',
        media_transport='connect3_tcp', media_tcp_approved=True)), stopped=False,
        _media_claim=None, _task=None, control=SimpleNamespace(_busy=False),
        talkback=SimpleNamespace(active=False, owner=None),
        live=SimpleNamespace(task=None, consumers=set()), channel2=None,
        check_tls_trust=Mock(), report_tls_error=Mock(return_value=None), _notify=Mock())


class AlarmProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = hub()
        self.probe = ring.AutonomousDoorbellDiagnostic(self.hub)

    async def asyncTearDown(self):
        self.probe.finish()
        await self.probe.wait_closed()

    @asynccontextmanager
    async def peer(self, response=reply(), *, hold=False, mutate=None, chunked=False):
        received, closed = asyncio.Event(), asyncio.Event()
        packets, tasks = [], set()

        async def handle(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                packet = await reader.readexactly(32)
                packets.append(packet)
                if mutate:
                    mutate()
                received.set()
                if not hold:
                    if chunked:
                        for byte in response:
                            writer.write(bytes((byte,)))
                            await writer.drain()
                    else:
                        writer.write(response)
                        await writer.drain()
                    if len(response) < 32:
                        writer.write_eof()
                trailing = await reader.read(256)
                if trailing:
                    packets.append(trailing)
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                writer.close()
                await writer.wait_closed()
                closed.set()
                tasks.discard(task)

        server = await asyncio.start_server(handle, '127.0.0.1', 0)
        address = server.sockets[0].getsockname()

        async def open_local(host, port, observation):
            self.assertEqual((host, port), ('127.0.0.1', 34567))
            reader, writer = await asyncio.open_connection(*address)
            observation['media_tcp_connected'] = True
            return reader, writer

        try:
            with patch.object(ring, 'open_connect3_media_tcp', side_effect=open_local) as connector, \
                    patch.object(ring, 'open_media_tls', new_callable=AsyncMock) as tls:
                yield packets, received, closed, connector, tls
        finally:
            self.probe.execute('stop')
            await self.probe.wait_closed()
            server.close()
            await server.wait_closed()
            if tasks:
                await asyncio.gather(*tasks)

    async def complete(self):
        async with asyncio.timeout(2):
            await self.probe.wait_closed()
        return self.probe.execute('status')

    async def test_real_setup_then_close_when_local_credential_missing(self):
        async with self.peer(chunked=True) as (packets, received, closed, connection, tls):
            first = self.probe.execute('start')
            self.assertEqual(first['status'], 'starting')
            result = await self.complete()
            await closed.wait()
            self.assertEqual(packets, [protocol.build_control_setup_request()])
            connection.assert_awaited_once()
            tls.assert_not_called()
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['last_error_reason'], 'local_auth_code_required')
        self.assertTrue(result['setup_accepted'])
        self.assertTrue(result['protected_login_capability'])
        self.assertFalse(result['control_endpoint_confirmed'])
        for key in ('listening', 'listen_established', 'credentials_sent', 'video_session_created',
                    'microphone_session_created', 'tls_verified'):
            self.assertFalse(result[key], key)
        for key in ('subscription_request_count', 'login_request_count', 'ring_events_emitted',
                    'effective_duration_seconds'):
            self.assertEqual(result[key], 0, key)
        self.assertNotIn('127.0.0.1', json.dumps(result))

    async def test_weak_legacy_or_rejected_peer_never_receives_credentials(self):
        for response in (reply(0, 0), reply(1, 1), reply(command=0xBE),
                         reply(result=1), reply(result=51)):
            with self.subTest(response=response):
                async with self.peer(response) as (packets, _, closed, connection, _tls):
                    self.probe.execute('start')
                    result = await self.complete()
                    await closed.wait()
                    self.assertEqual(packets, [protocol.build_control_setup_request()])
                    self.assertEqual(result['status'], 'blocked')
                    self.assertFalse(result['protected_login_capability'])
                    self.assertEqual(result['login_request_count'], 0)

    async def test_unexpected_message_is_not_a_bell_and_does_not_escape_diagnostics(self):
        async with self.peer(b'PRIVATE_KEY_TOKEN_PASSWORD_UID!!'.ljust(32, b'X')) as values:
            self.probe.execute('start')
            result = await self.complete()
        self.assertEqual(result['last_error_reason'], 'control_setup_invalid')
        self.assertEqual(result['events'], [])
        self.assertNotIn('PRIVATE', json.dumps(result))

    async def test_incomplete_response_and_timeout_close_once(self):
        async with self.peer(b'\xa9\0'):
            self.probe.execute('start')
            result = await self.complete()
            self.assertEqual(result['last_error_reason'], 'control_setup_incomplete')
            self.assertEqual(result['bytes_received'], 2)
        with patch.object(ring, 'SETUP_TIMEOUT', 0.03):
            async with self.peer(hold=True) as (packets, _, closed, connection, _tls):
                self.probe.execute('start')
                result = await self.complete()
                await closed.wait()
                self.assertEqual(result['close_reason'], 'timeout')
                connection.assert_awaited_once()

    async def test_stop_duplicate_start_and_unload_drain_socket(self):
        for operation in ('stop', 'unload'):
            self.probe = ring.AutonomousDoorbellDiagnostic(self.hub)
            async with self.peer(hold=True) as (packets, received, closed, connection, _tls):
                self.probe.execute('start')
                await received.wait()
                with self.assertRaisesRegex(ValueError, 'already running'):
                    self.probe.execute('start')
                if operation == 'stop':
                    self.probe.execute('stop')
                else:
                    self.probe.finish()
                await self.probe.wait_closed()
                await closed.wait()
                self.assertFalse(self.probe.active)
                self.assertTrue(self.probe.diagnostics()['connection_closed'])
                connection.assert_awaited_once()

    async def test_cancel_before_task_starts_never_opens_socket(self):
        with patch.object(ring, 'open_connect3_media_tcp', new_callable=AsyncMock) as connection:
            self.probe.execute('start')
            self.probe.execute('stop')
            await self.probe.wait_closed()
            connection.assert_not_called()
            self.assertFalse(self.probe.active)
            self.assertEqual(self.probe.diagnostics()['status'], 'stopped')

    async def test_busy_main_secondary_control_or_microphone_never_opens_socket(self):
        mutations = (
            lambda: self.hub.live.consumers.add('viewer'),
            lambda: setattr(self.hub, '_media_claim', object()),
            lambda: setattr(self.hub.control, '_busy', True),
            lambda: setattr(self.hub.talkback, 'active', True),
            lambda: setattr(self.hub, 'channel2', SimpleNamespace(
                live=SimpleNamespace(task=None, consumers={'secondary'}))),
        )
        for mutate in mutations:
            self.hub = hub()
            self.probe = ring.AutonomousDoorbellDiagnostic(self.hub)
            mutate()
            with patch.object(ring, 'open_connect3_media_tcp') as connection:
                result = self.probe.execute('start')
                self.assertEqual(result['last_error_reason'], 'device_busy')
                self.assertFalse(self.probe.active)
                connection.assert_not_called()

    async def test_transport_approval_revocation_and_network_error_no_fallback(self):
        self.hub.entry.data['media_tcp_approved'] = False
        result = self.probe.execute('start')
        self.assertEqual(result['last_error_reason'], 'endpoint_approval_required')
        self.hub.entry.data['media_tcp_approved'] = True
        with patch.object(ring, 'open_connect3_media_tcp', side_effect=OSError('PRIVATE_ENDPOINT')) as tcp, \
                patch.object(ring, 'open_media_tls') as tls:
            self.probe.execute('start')
            result = await self.complete()
            tcp.assert_awaited_once()
            tls.assert_not_called()
            self.assertNotIn('PRIVATE_ENDPOINT', json.dumps(result))

    async def test_endpoint_changed_while_awaiting_response_does_not_continue(self):
        async with self.peer(mutate=lambda: self.hub.entry.data.update(host='192.0.2.3')):
            self.probe.execute('start')
            result = await self.complete()
        self.assertEqual(result['last_error_reason'], 'control_setup_invalid')
        self.assertEqual(result['login_request_count'], 0)
