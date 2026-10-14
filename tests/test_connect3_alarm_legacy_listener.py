"""Actual synthetic TLS peers for legacy native control; no device/cloud I/O."""
import asyncio
from contextlib import ExitStack
from hashlib import sha256
import json
import ssl
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
import test_connect3_https as https_fixture

ring = load('connect3.ring_listener')
cgi = load('connect3.cgi')
tls = load('connect3.tls')

AUTH = 'SYNTHETIC_LEGACY_LISTENER_AUTH'
KEY = 'SYNTHETIC_LEGACY_LISTENER_KEY_' + 'K' * 32
PRIVATE_NOTIFICATION = b'SYNTHETIC_PRIVATE_ALARM_BODY'
SETUP_REQUEST = b'\xa9' + bytes(8) + b'\x64' + bytes(22)
LEGACY_RESPONSE = b'\xa9' + bytes(8) + b'\x01' + bytes(22)
SUBTYPES = (*range(1, 14), 157, 159, 160, 161, *range(174, 191), 192)


def expected_login(command):
    """Independent SDK IPv4 login, with the app's SHA256 authCode input."""
    parameters = b'adminapp2&&' + sha256(AUTH.encode()).hexdigest().encode()
    header = bytearray(32)
    header[:4] = bytes((command, 3, 1, 0x50))
    struct.pack_into('<I', header, 4, len(parameters))
    header[8] = 255
    header[9:24] = b'A0000G0000C0000'
    header[24], header[25], header[30], header[31] = 4, 1, 0xA1, 0xAA
    return bytes(header) + parameters


def expected_subscription(subtype):
    header = bytearray(32)
    header[0], header[8], header[12] = 0x68, 2, subtype
    return bytes(header)


def notification():
    header = bytearray(32)
    header[0], header[12], header[13] = 0x69, 5, 1
    struct.pack_into('<I', header, 4, len(PRIVATE_NOTIFICATION))
    return bytes(header) + PRIVATE_NOTIFICATION


async def read_native(reader):
    header = await reader.readexactly(32)
    length = struct.unpack_from('<I', header, 4)[0]
    if length > 1024:
        raise AssertionError('Synthetic native packet exceeded the fixture limit')
    return header + await reader.readexactly(length)


class LegacyTLSListenerTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # The existing fixture creates only temporary, freshly generated keys.
        https_fixture.HTTPSTests.setUpClass.__func__(cls)
        cls.changed_certificate = SimpleNamespace()
        https_fixture.HTTPSTests.setUpClass.__func__(cls.changed_certificate)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()
        cls.changed_certificate.directory.cleanup()

    async def asyncSetUp(self):
        self.handlers, self.writers = set(), set()
        self.handler_errors = []
        self.connections = []
        self.endpoint_calls, self.verified_connections = [], []
        self.first_closed, self.second_closed = asyncio.Event(), asyncio.Event()
        self.notification_sent = asyncio.Event()
        self.cgi_called = asyncio.Event()
        self.reject_login = False
        self.accept_second = True
        self.change_configuration = False
        self.handshakes = 0
        self.hub = SimpleNamespace(entry=SimpleNamespace(data={}), stopped=False,
            _media_claim=None, _task=None, control=SimpleNamespace(_busy=False),
            talkback=SimpleNamespace(active=False, owner=None),
            live=SimpleNamespace(task=None, consumers=set()), channel2=None,
            check_tls_trust=Mock(), report_tls_error=Mock(return_value=None),
            _notify=Mock(), hass=SimpleNamespace(bus=SimpleNamespace(async_fire=Mock())))
        self.probe = ring.AutonomousDoorbellDiagnostic(self.hub)
        self.material = cgi.StreamMaterial(KEY)

        def select_certificate(ssl_object, server_name, initial_context):
            self.handshakes += 1
            if self.handshakes == 2 and not self.accept_second:
                ssl_object.context = self.changed_certificate.context

        self.context.set_servername_callback(select_certificate)
        self.server = await asyncio.start_server(self.serve, '127.0.0.1', 0,
            ssl=self.context, ssl_handshake_timeout=1.0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.hub.entry.data.update(host='127.0.0.1', media_transport='tls',
            media_port=self.port, media_certificate_sha256=self.fingerprint,
            certificate_sha256=self.fingerprint, cgi_port=443, auth_code=AUTH)

        real_tls = ring.open_media_tls

        async def connect(host, port, pin, observation):
            self.endpoint_calls.append((host, port, pin))
            self.assertEqual((host, port, pin), ('127.0.0.1', self.port, self.fingerprint))
            reader, writer = await real_tls(host, port, pin, observation)
            self.verified_connections.append(observation.get('media_tls_verified') is True)
            if len(self.endpoint_calls) == 2 and self.change_configuration:
                # A credential change keeps the socket endpoint identical;
                # the reconnect must also preserve the full entry binding.
                self.hub.entry.data['auth_code'] = 'SYNTHETIC_CHANGED_AUTH'
            return reader, writer

        async def fetch(host, auth, **options):
            self.assertEqual(len(self.endpoint_calls), 2)
            self.assertEqual(self.verified_connections, [True, True])
            self.assertEqual((host, auth), ('127.0.0.1', AUTH))
            self.assertEqual(options['port'], 443)
            self.assertEqual(options['certificate_sha256'], self.fingerprint)
            self.assertTrue(self.probe.diagnostics()['control_reconnect_performed'])
            self.assertTrue(self.probe.diagnostics()['tls_verified'])
            async with asyncio.timeout(1):
                await self.first_closed.wait()
            options['diagnostics'].update(tls_verified=True,
                tls_policy='certificate_pin', authentication_status='accepted')
            self.cgi_called.set()
            return self.material

        self.guards = ExitStack()
        self.tls_connector = self.guards.enter_context(patch.object(ring,
            'open_media_tls', side_effect=connect))
        self.tcp_connector = self.guards.enter_context(patch.object(ring,
            'open_connect3_media_tcp', new_callable=AsyncMock,
            side_effect=AssertionError('Unexpected plain TCP fallback')))
        self.cgi_fetch = self.guards.enter_context(patch.object(ring,
            'read_stream_material', side_effect=fetch))

    async def asyncTearDown(self):
        self.probe.finish()
        await self.probe.wait_closed()
        self.server.close()
        await self.server.wait_closed()
        for writer in tuple(self.writers):
            writer.close()
        if self.handlers:
            async with asyncio.timeout(2):
                await asyncio.gather(*tuple(self.handlers), return_exceptions=True)
        self.guards.close()
        self.context.set_servername_callback(None)
        self.assertFalse(self.handlers)
        self.assertFalse(self.writers)
        self.assertEqual(self.handler_errors, [])

    async def serve(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        self.writers.add(writer)
        packets = []
        self.connections.append(packets)
        index = len(self.connections)
        try:
            self.assertIsNotNone(writer.get_extra_info('ssl_object'))
            async with asyncio.timeout(3):
                if index == 1:
                    packet = await reader.readexactly(32)
                    packets.append(packet)
                    self.assertEqual(packet, SETUP_REQUEST)
                    writer.write(LEGACY_RESPONSE)
                    await writer.drain()
                    self.assertEqual(await reader.read(), b'')
                    self.first_closed.set()
                    return
                self.assertEqual(index, 2, 'A second reconnect is forbidden')
                if not self.accept_second or self.change_configuration:
                    self.assertEqual(await reader.read(), b'')
                    return
                for command in (0x0A, 0xA0):
                    packet = await read_native(reader)
                    packets.append(packet)
                    self.assertTrue(self.cgi_called.is_set())
                    self.assertEqual(packet, expected_login(command))
                response = bytearray(32)
                response[0], response[10] = 0xB0, 1
                if self.reject_login:
                    response[8], response[9] = 1, 7
                writer.write(bytes(response))
                await writer.drain()
                if self.reject_login:
                    self.assertEqual(await reader.read(), b'')
                    return
                for subtype in SUBTYPES:
                    packet = await read_native(reader)
                    packets.append(packet)
                    self.assertEqual(packet, expected_subscription(subtype))
                response = notification()
                writer.write(response[:13])
                await writer.drain()
                writer.write(response[13:])
                await writer.drain()
                self.notification_sent.set()
                self.assertEqual(await reader.read(), b'')
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            if self.accept_second and not self.change_configuration:
                self.handler_errors.append('unexpected_early_peer_close')
        except Exception as error:
            self.handler_errors.append(type(error).__name__)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ssl.SSLError):
                pass
            self.writers.discard(writer)
            self.handlers.discard(task)
            if index == 2:
                self.second_closed.set()

    async def complete(self):
        async with asyncio.timeout(3):
            await self.probe.wait_closed()
            await self.first_closed.wait()
            await self.second_closed.wait()
        return self.probe.diagnostics()

    async def wait_for_notification(self):
        async with asyncio.timeout(2):
            await self.notification_sent.wait()
            while self.probe.diagnostics()['notification_candidates'] != 1:
                self.assertTrue(self.probe.active)
                await asyncio.sleep(0.005)

    def assert_common(self, result, *, authenticated):
        self.assertEqual(self.endpoint_calls,
            [('127.0.0.1', self.port, self.fingerprint)] * 2)
        self.assertEqual(result['connection_attempts'], 2)
        self.assertEqual(result['setup_request_count'], 1)
        self.assertEqual(result['setup_variant'], 'legacy_v2')
        self.assertEqual(result['setup_response_selector'], 1)
        self.assertEqual(result['crypto_mode_source'], 'legacy_sdk_defaults')
        self.assertEqual(result['protocol_version'], 2)
        self.assertEqual((result['encryption_mode'], result['sha_mode']), (0, 0))
        self.assertFalse(result['protected_login_capability'])
        self.assertTrue(result['control_reconnect_required'])
        self.assertTrue(result['connection_closed'])
        self.assertFalse(result['probe_active'])
        self.assertFalse(result['listening'])
        self.assertFalse(result['native_checksum_verified'])
        self.assertEqual(result['ring_events_emitted'], 0)
        self.assertFalse(result['physical_ring_confirmed'])
        self.assertFalse(result['video_session_created'])
        self.assertFalse(result['microphone_session_created'])
        self.hub.hass.bus.async_fire.assert_not_called()
        self.tcp_connector.assert_not_awaited()
        self.assertEqual(self.connections[0], [SETUP_REQUEST])
        if authenticated:
            self.cgi_fetch.assert_awaited_once()
            self.assertEqual(result['login_request_count'], 2)
            self.assertTrue(result['credentials_sent'])
            self.assertEqual(result['credential_protection'], 'verified_tls')
            self.assertEqual(result['control_login_profile'], 'legacy_v2_verified_tls')
            self.assertEqual(self.connections[1][:2],
                [expected_login(0x0A), expected_login(0xA0)])
            self.assertEqual(self.material.key, '')
        else:
            self.cgi_fetch.assert_not_awaited()
            self.assertEqual(result['login_request_count'], 0)
            self.assertFalse(result['credentials_sent'])
            self.assertEqual(self.connections[1], [])
        raw = json.dumps(result)
        for secret in (AUTH, KEY, sha256(AUTH.encode()).hexdigest(), self.fingerprint,
                       '127.0.0.1', PRIVATE_NOTIFICATION.decode()):
            self.assertNotIn(secret, raw)

    async def test_actual_tls_setup_reconnect_login_subscription_and_deadline(self):
        self.probe.execute('start', duration=1)
        result = await self.complete()
        self.assert_common(result, authenticated=True)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['close_reason'], 'deadline')
        self.assertTrue(result['control_reconnect_performed'])
        self.assertTrue(result['tls_verified'])
        self.assertTrue(result['control_peer_authenticated'])
        self.assertTrue(result['cgi_https_verified'])
        self.assertTrue(result['listen_established'])
        self.assertEqual(result['authentication_status'], 'accepted')
        self.assertEqual(result['subscription_request_count'], 35)
        self.assertEqual(self.connections[1][2:],
            [expected_subscription(subtype) for subtype in SUBTYPES])
        self.assertEqual(result['notification_candidates'], 1)
        self.assertEqual(result['command_counts'], {'105': 1})
        self.assertEqual(result['events'][0]['subtype'], 254)
        self.assertEqual(result['events'][0]['callback_type'], 354)
        self.assertIsNone(result['events'][0]['channel'])

    async def test_changed_second_certificate_refuses_credentials_and_fallback(self):
        self.accept_second = False
        self.probe.execute('start', duration=1)
        result = await self.complete()
        self.assert_common(result, authenticated=False)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['last_error_type'], 'TLSOrEndpointError')
        self.assertFalse(result['tls_verified'])
        self.assertFalse(result['control_reconnect_performed'])
        self.assertEqual(result['subscription_request_count'], 0)
        self.assertEqual(self.verified_connections, [True])
        self.hub.report_tls_error.assert_called_once()
        error = self.hub.report_tls_error.call_args.args[0]
        self.assertIsInstance(error, tls.MediaTLSFailure)
        self.assertEqual(str(error), 'media_certificate_pin_mismatch')

    async def test_changed_configuration_after_reconnect_closes_before_cgi(self):
        self.change_configuration = True
        self.probe.execute('start', duration=1)
        result = await self.complete()
        self.assert_common(result, authenticated=False)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['last_error_reason'], 'configuration_changed_during_listen')
        self.assertEqual(self.verified_connections, [True, True])
        self.assertEqual(result['subscription_request_count'], 0)
        self.assertFalse(result['control_reconnect_performed'])

    async def test_native_login_rejection_does_not_subscribe_or_retry(self):
        self.reject_login = True
        self.probe.execute('start', duration=1)
        result = await self.complete()
        self.assert_common(result, authenticated=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['authentication_status'], 'rejected')
        self.assertEqual(result['last_error_reason'], 'native_login_rejected')
        self.assertEqual(result['native_login_result'], 7)
        self.assertEqual(result['subscription_request_count'], 0)
        self.assertFalse(result['listen_established'])
        self.assertEqual(len(self.connections[1]), 2)

    async def test_manual_stop_drains_second_tls_socket(self):
        self.probe.execute('start', duration=2)
        await self.wait_for_notification()
        self.probe.execute('stop')
        result = await self.complete()
        self.assert_common(result, authenticated=True)
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(result['close_reason'], 'user_stop')
        self.assertTrue(result['listen_established'])
        self.assertEqual(result['subscription_request_count'], 35)

    async def test_unload_drains_second_tls_socket(self):
        self.probe.execute('start', duration=2)
        await self.wait_for_notification()
        self.probe.finish('integration_unload')
        result = await self.complete()
        self.assert_common(result, authenticated=True)
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(result['close_reason'], 'integration_unload')
        self.assertTrue(result['integration_unloaded'])
        self.assertTrue(result['listen_established'])
        self.assertEqual(result['subscription_request_count'], 35)


if __name__ == '__main__':
    unittest.main()
