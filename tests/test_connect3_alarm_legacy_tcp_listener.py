"""Actual loopback legacy alarm TCP peers; synthetic credentials and CGI only."""
import asyncio
from contextlib import ExitStack
from hashlib import sha256
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load

ring = load('connect3.ring_listener')
cgi = load('connect3.cgi')

AUTH = 'SYNTHETIC_LEGACY_TCP_LISTENER_AUTH'
KEY = 'SYNTHETIC_LEGACY_TCP_LISTENER_KEY_' + 'K' * 32
PIN = 'a' * 64
PRIVATE_NOTIFICATION = b'SYNTHETIC_PRIVATE_TCP_ALARM_BODY'
SETUP_REQUEST = b'\xa9' + bytes(8) + b'\x64' + bytes(22)
LEGACY_RESPONSE = b'\xa9' + bytes(8) + b'\x01' + bytes(22)
SUBTYPES = (*range(1, 14), 157, 159, 160, 161, *range(174, 191), 192)


def expected_login(command):
    """Independent IPv4 SDK fixture, without a native cipher envelope."""
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
        raise AssertionError('Synthetic TCP packet exceeded the fixture limit')
    return header + await reader.readexactly(length)


class LegacyTCPListenerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.handlers, self.writers = set(), set()
        self.handler_errors, self.connections, self.endpoint_calls = [], [], []
        self.first_closed, self.second_closed = asyncio.Event(), asyncio.Event()
        self.notification_sent, self.cgi_called = asyncio.Event(), asyncio.Event()
        self.reconnect_started = asyncio.Event()
        self.cgi_release, self.reconnect_release = asyncio.Event(), asyncio.Event()
        self.hold_cgi = self.hold_reconnect = False
        self.cgi_verified, self.cgi_accepted = True, True
        self.mutate_on_setup = self.mutate_on_reconnect = self.mutate_in_cgi = None
        self.reject_login = self.fail_reconnect = self.allow_early_close = False
        self.response_sequence = None
        self.second_opened = False
        self.material_returned = False
        self.hub = SimpleNamespace(entry=SimpleNamespace(data={}), stopped=False,
            _media_claim=None, _task=None, control=SimpleNamespace(_busy=False),
            talkback=SimpleNamespace(active=False, owner=None),
            live=SimpleNamespace(task=None, consumers=set()), channel2=None,
            check_tls_trust=Mock(), report_tls_error=Mock(return_value=None),
            _notify=Mock(), hass=SimpleNamespace(bus=SimpleNamespace(async_fire=Mock())))
        self.hub.entry.data.update(host='127.0.0.1', media_transport='connect3_tcp',
            media_tcp_approved=True, legacy_alarm_tcp_approved=True,
            certificate_sha256=PIN, cgi_port=443, auth_code=AUTH)
        self.probe = ring.AutonomousDoorbellDiagnostic(self.hub)
        self.material = cgi.StreamMaterial(KEY)
        self.server = await asyncio.start_server(self.serve, '127.0.0.1', 0)
        self.port = self.server.sockets[0].getsockname()[1]

        async def connect(host, port, observation):
            self.endpoint_calls.append((host, port))
            self.assertEqual((host, port), ('127.0.0.1', 34567))
            self.assertLessEqual(len(self.endpoint_calls), 2, 'A second reconnect is forbidden')
            if len(self.endpoint_calls) == 2 and self.fail_reconnect:
                raise OSError('SYNTHETIC_PRIVATE_RECONNECT_ERROR')
            # Route the fixed production endpoint to a private ephemeral loopback
            # listener; the codec and socket lifecycle remain actual TCP.
            reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
            observation.update(media_tcp_connected=True, media_tls_verified=False,
                media_transport='connect3_tcp', media_tls_policy='connect3_explicit_tcp')
            if len(self.endpoint_calls) == 2:
                self.second_opened = True
                self.reconnect_started.set()
                try:
                    if self.hold_reconnect:
                        await self.reconnect_release.wait()
                    if self.mutate_on_reconnect is not None:
                        self.mutate_on_reconnect()
                except BaseException:
                    writer.close()
                    await writer.wait_closed()
                    raise
            return reader, writer

        async def fetch(host, auth, **options):
            self.assertEqual(self.endpoint_calls, [('127.0.0.1', 34567)] * 2)
            self.assertEqual((host, auth), ('127.0.0.1', AUTH))
            self.assertEqual(options['port'], 443)
            self.assertEqual(options['certificate_sha256'], PIN)
            self.assertTrue(self.hub.entry.data['legacy_alarm_tcp_approved'])
            self.assertTrue(self.probe.diagnostics()['control_reconnect_performed'])
            self.assertFalse(self.probe.diagnostics()['tls_verified'])
            async with asyncio.timeout(1):
                await self.first_closed.wait()
            self.cgi_called.set()
            if self.hold_cgi:
                await self.cgi_release.wait()
            options['diagnostics'].update(tls_verified=self.cgi_verified,
                tls_policy='certificate_pin',
                authentication_status='accepted' if self.cgi_accepted else 'rejected')
            if self.mutate_in_cgi is not None:
                self.mutate_in_cgi()
            self.material_returned = True
            return self.material

        self.guards = ExitStack()
        self.tcp_connector = self.guards.enter_context(patch.object(ring,
            'open_connect3_media_tcp', side_effect=connect))
        self.tls_connector = self.guards.enter_context(patch.object(ring,
            'open_media_tls', new_callable=AsyncMock,
            side_effect=AssertionError('Unexpected TLS or port fallback')))
        self.cgi_fetch = self.guards.enter_context(patch.object(ring,
            'read_stream_material', side_effect=fetch))

    async def asyncTearDown(self):
        self.allow_early_close = True
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
            self.assertIsNone(writer.get_extra_info('ssl_object'))
            async with asyncio.timeout(4):
                if index == 1:
                    packet = await reader.readexactly(32)
                    packets.append(packet)
                    self.assertEqual(packet, SETUP_REQUEST)
                    if self.mutate_on_setup is not None:
                        self.mutate_on_setup()
                    writer.write(LEGACY_RESPONSE)
                    await writer.drain()
                    self.assertEqual(await reader.read(), b'')
                    self.first_closed.set()
                    return
                self.assertEqual(index, 2, 'A second reconnect is forbidden')
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
                response = (b''.join(self.response_sequence)
                            if self.response_sequence is not None else notification())
                writer.write(response[:9])
                await writer.drain()
                writer.write(response[9:])
                await writer.drain()
                self.notification_sent.set()
                self.assertEqual(await reader.read(), b'')
        except (asyncio.IncompleteReadError, ConnectionError):
            if not self.allow_early_close:
                self.handler_errors.append('unexpected_early_peer_close')
        except Exception as error:
            self.handler_errors.append(type(error).__name__)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            self.writers.discard(writer)
            self.handlers.discard(task)
            if index == 1:
                self.first_closed.set()
            elif index == 2:
                self.second_closed.set()

    async def complete(self):
        async with asyncio.timeout(3):
            await self.probe.wait_closed()
            if self.endpoint_calls:
                await self.first_closed.wait()
            if self.second_opened:
                await self.second_closed.wait()
        return self.probe.diagnostics()

    async def wait_for_notification(self):
        async with asyncio.timeout(2):
            await self.notification_sent.wait()
            while self.probe.diagnostics()['notification_candidates'] != 1:
                self.assertTrue(self.probe.active)
                await asyncio.sleep(0.005)

    def assert_closed(self, result):
        self.assertTrue(result['connection_closed'])
        self.assertFalse(result['probe_active'])
        self.assertFalse(result['listening'])
        self.assertFalse(result['tls_verified'])
        self.assertFalse(result['control_peer_authenticated'])
        self.assertFalse(result['native_checksum_verified'])
        self.assertEqual(result['ring_events_emitted'], 0)
        self.assertFalse(result['physical_ring_confirmed'])
        self.assertFalse(result['video_session_created'])
        self.assertFalse(result['microphone_session_created'])
        self.hub.hass.bus.async_fire.assert_not_called()
        self.tls_connector.assert_not_awaited()
        raw = json.dumps(result)
        for secret in (AUTH, KEY, sha256(AUTH.encode()).hexdigest(), PIN,
                       '127.0.0.1', PRIVATE_NOTIFICATION.decode(), 'SYNTHETIC_PRIVATE'):
            self.assertNotIn(secret, raw)
        if self.material_returned:
            self.assertEqual(self.material.key, '')

    def assert_no_credentials(self, result, *, cgi_requested=False):
        self.assert_closed(result)
        self.assertFalse(result['credentials_sent'])
        self.assertEqual(result['login_request_count'], 0)
        self.assertEqual(result['subscription_request_count'], 0)
        self.assertEqual(result['effective_duration_seconds'], 0)
        self.assertEqual(result['heartbeat_count'], 0)
        if cgi_requested:
            self.cgi_fetch.assert_awaited_once()
        else:
            self.cgi_fetch.assert_not_awaited()
        if len(self.connections) > 1:
            self.assertEqual(self.connections[1], [])

    def assert_authenticated(self, result):
        self.assert_closed(result)
        self.assertEqual(self.endpoint_calls, [('127.0.0.1', 34567)] * 2)
        self.assertEqual(result['connection_attempts'], 2)
        self.assertEqual(result['setup_request_count'], 1)
        self.assertEqual(result['setup_variant'], 'legacy_v2')
        self.assertEqual(result['setup_response_selector'], 1)
        self.assertEqual(result['crypto_mode_source'], 'legacy_sdk_defaults')
        self.assertEqual(result['protocol_version'], 2)
        self.assertEqual((result['encryption_mode'], result['sha_mode']), (0, 0))
        self.assertFalse(result['protected_login_capability'])
        self.assertTrue(result['control_reconnect_required'])
        self.assertTrue(result['control_reconnect_performed'])
        self.assertTrue(result['cgi_https_verified'])
        self.assertEqual(result['login_request_count'], 2)
        self.assertTrue(result['credentials_sent'])
        self.assertEqual(result['control_login_profile'], 'legacy_v2_explicit_tcp')
        self.assertEqual(result['credential_protection'], 'reusable_hash_unencrypted_tcp')
        self.assertEqual(self.connections[0], [SETUP_REQUEST])
        self.assertEqual(self.connections[1][:2],
            [expected_login(0x0A), expected_login(0xA0)])
        self.cgi_fetch.assert_awaited_once()

    async def test_explicit_tcp_setup_reconnect_login_subscription_and_deadline(self):
        self.probe.execute('start', duration=1)
        result = await self.complete()
        self.assert_authenticated(result)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['close_reason'], 'deadline')
        self.assertEqual(result['authentication_status'], 'accepted')
        self.assertTrue(result['listen_established'])
        self.assertEqual(result['subscription_request_count'], 35)
        self.assertEqual(self.connections[1][2:],
            [expected_subscription(subtype) for subtype in SUBTYPES])
        self.assertEqual(result['notification_candidates'], 1)
        self.assertEqual(result['command_counts'], {'105': 1})
        self.assertEqual(result['events'][0]['subtype'], 254)
        self.assertEqual(result['events'][0]['callback_type'], 354)
        self.assertIsNone(result['events'][0]['channel'])
        self.assertGreater(result['effective_duration_seconds'], 0)
        self.assertLessEqual(result['effective_duration_seconds'], 1.1)

    async def test_missing_specific_tcp_approval_refuses_before_cgi_and_reconnect(self):
        self.hub.entry.data.pop('legacy_alarm_tcp_approved')
        self.probe.execute('start', duration=90)
        result = await self.complete()
        self.assert_no_credentials(result)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(self.endpoint_calls, [('127.0.0.1', 34567)])
        self.assertEqual(self.connections, [[SETUP_REQUEST]])
        self.assertFalse(result['control_reconnect_performed'])

    async def test_header_only_legacy_states_are_observed_on_actual_tcp_without_false_bell(self):
        def state(mask):
            response = bytearray(32)
            response[0] = 0xB1
            struct.pack_into('<III', response, 16, mask, 0, 0)
            return bytes(response)
        self.response_sequence = [state(0), state(1), state(1)]
        self.probe.execute('start', duration=1)
        result = await self.complete()
        self.assert_authenticated(result)
        self.assertEqual(result['legacy_records_observed'], 3)
        self.assertEqual(result['legacy_state_frames'], 3)
        self.assertEqual(result['legacy_state_changes'], 1)
        self.assertEqual(result['notification_candidates'], 0)
        self.assertFalse(result['firmware_subscription_confirmed'])
        # The wire sequence remains the published SDK mode-1 profile. Only
        # passive reception has changed; no speculative subscription is sent.
        self.assertEqual(self.connections[1][2:],
                         [expected_subscription(subtype) for subtype in SUBTYPES])

    async def test_truthy_specific_tcp_approval_is_not_consent(self):
        self.hub.entry.data['legacy_alarm_tcp_approved'] = 1
        self.probe.execute('start', duration=90)
        result = await self.complete()
        self.assert_no_credentials(result)
        self.assertEqual(self.endpoint_calls, [('127.0.0.1', 34567)])

    async def test_specific_approval_does_not_replace_media_tcp_approval(self):
        self.hub.entry.data['media_tcp_approved'] = False
        result = self.probe.execute('start', duration=90)
        self.assert_no_credentials(result)
        self.assertEqual(result['status'], 'unavailable')
        self.tcp_connector.assert_not_awaited()

    async def test_configuration_change_during_first_setup_refuses_before_reconnect(self):
        self.mutate_on_setup = lambda: self.hub.entry.data.update(
            auth_code='SYNTHETIC_CHANGED_TCP_AUTH')
        self.probe.execute('start', duration=90)
        result = await self.complete()
        self.assert_no_credentials(result)
        self.assertEqual(self.endpoint_calls, [('127.0.0.1', 34567)])
        self.assertEqual(self.connections, [[SETUP_REQUEST]])
        self.assertFalse(result['control_reconnect_performed'])

    async def test_specific_approval_revoked_during_first_setup_refuses_before_reconnect(self):
        self.mutate_on_setup = lambda: self.hub.entry.data.update(
            legacy_alarm_tcp_approved=False)
        self.probe.execute('start', duration=90)
        result = await self.complete()
        self.assert_no_credentials(result)
        self.assertEqual(self.endpoint_calls, [('127.0.0.1', 34567)])
        self.assertEqual(self.connections, [[SETUP_REQUEST]])
        self.assertFalse(result['control_reconnect_performed'])

    async def test_full_configuration_change_during_reconnect_refuses_before_cgi(self):
        self.allow_early_close = True
        self.mutate_on_reconnect = lambda: self.hub.entry.data.update(
            auth_code='SYNTHETIC_CHANGED_TCP_AUTH')
        self.probe.execute('start', duration=90)
        result = await self.complete()
        self.assert_no_credentials(result)
        self.assertEqual(result['last_error_reason'], 'configuration_changed_during_listen')
        self.assertEqual(self.endpoint_calls, [('127.0.0.1', 34567)] * 2)

    async def test_specific_approval_revoked_during_reconnect_refuses_before_cgi(self):
        self.allow_early_close = True
        self.mutate_on_reconnect = lambda: self.hub.entry.data.update(
            legacy_alarm_tcp_approved=False)
        self.probe.execute('start', duration=90)
        result = await self.complete()
        self.assert_no_credentials(result)
        self.assertEqual(self.endpoint_calls, [('127.0.0.1', 34567)] * 2)

    async def test_media_approval_revoked_during_reconnect_refuses_before_cgi(self):
        self.allow_early_close = True
        self.mutate_on_reconnect = lambda: self.hub.entry.data.update(media_tcp_approved=False)
        self.probe.execute('start', duration=90)
        result = await self.complete()
        self.assert_no_credentials(result)
        self.assertEqual(self.endpoint_calls, [('127.0.0.1', 34567)] * 2)

    async def test_unverified_fresh_cgi_refuses_native_login(self):
        self.allow_early_close = True
        self.cgi_verified = False
        self.probe.execute('start', duration=90)
        result = await self.complete()
        self.assert_no_credentials(result, cgi_requested=True)
        self.assertEqual(result['last_error_reason'], 'cgi_trust_required')

    async def test_rejected_fresh_cgi_refuses_native_login(self):
        self.allow_early_close = True
        self.cgi_accepted = False
        self.probe.execute('start', duration=90)
        result = await self.complete()
        self.assert_no_credentials(result, cgi_requested=True)
        self.assertEqual(result['last_error_reason'], 'cgi_trust_required')

    async def test_specific_approval_revoked_in_cgi_refuses_before_native_login(self):
        self.allow_early_close = True
        self.mutate_in_cgi = lambda: self.hub.entry.data.update(legacy_alarm_tcp_approved=False)
        self.probe.execute('start', duration=90)
        result = await self.complete()
        self.assert_no_credentials(result, cgi_requested=True)

    async def test_configuration_change_in_cgi_refuses_before_native_login(self):
        self.allow_early_close = True
        self.mutate_in_cgi = lambda: self.hub.entry.data.update(
            certificate_sha256='b' * 64)
        self.probe.execute('start', duration=90)
        result = await self.complete()
        self.assert_no_credentials(result, cgi_requested=True)

    async def test_rejected_native_login_closes_without_subscription_or_retry(self):
        self.reject_login = True
        self.probe.execute('start', duration=90)
        result = await self.complete()
        self.assert_authenticated(result)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['authentication_status'], 'rejected')
        self.assertEqual(result['native_login_result'], 7)
        self.assertEqual(result['subscription_request_count'], 0)
        self.assertEqual(result['effective_duration_seconds'], 0)
        self.assertFalse(result['listen_established'])
        self.assertEqual(len(self.connections[1]), 2)

    async def test_failed_reconnect_never_retries_or_fetches_cgi(self):
        self.fail_reconnect = True
        self.probe.execute('start', duration=90)
        result = await self.complete()
        self.assert_no_credentials(result)
        self.assertEqual(self.endpoint_calls, [('127.0.0.1', 34567)] * 2)
        self.assertEqual(self.connections, [[SETUP_REQUEST]])
        self.assertFalse(result['control_reconnect_performed'])

    async def test_stop_during_reconnect_drains_socket_before_cgi(self):
        self.hold_reconnect = self.allow_early_close = True
        self.probe.execute('start', duration=90)
        async with asyncio.timeout(2):
            await self.reconnect_started.wait()
        self.probe.execute('stop')
        result = await self.complete()
        self.assert_no_credentials(result)
        self.assertEqual(result['status'], 'stopped')

    async def test_unload_during_cgi_drains_socket_before_native_login(self):
        self.hold_cgi = self.allow_early_close = True
        self.probe.execute('start', duration=90)
        async with asyncio.timeout(2):
            await self.cgi_called.wait()
        self.probe.finish()
        result = await self.complete()
        self.assert_no_credentials(result, cgi_requested=True)
        self.assertEqual(result['status'], 'stopped')
        self.assertTrue(result['integration_unloaded'])

    async def test_stop_ninety_second_observation_closes_without_fake_elapsed_time(self):
        self.probe.execute('start', duration=90)
        await self.wait_for_notification()
        # Give the real observation a measurable lifetime on Windows too.
        # An immediate loopback notification can arrive within the same clock
        # tick; its elapsed duration then legitimately rounds to 0.000 s.
        await asyncio.sleep(0.02)
        self.probe.execute('stop')
        result = await self.complete()
        self.assert_authenticated(result)
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(result['requested_duration_seconds'], 90)
        self.assertGreater(result['effective_duration_seconds'], 0)
        self.assertLess(result['effective_duration_seconds'], 3)
        self.assertEqual(result['notification_candidates'], 1)

    async def test_unload_ninety_second_observation_drains_reader_and_socket(self):
        self.probe.execute('start', duration=90)
        await self.wait_for_notification()
        self.probe.finish()
        result = await self.complete()
        self.assert_authenticated(result)
        self.assertEqual(result['status'], 'stopped')
        self.assertTrue(result['integration_unloaded'])
        self.assertLess(result['effective_duration_seconds'], 3)

    def test_duration_is_bounded_before_connection(self):
        for duration in (0, 91, True, 1.5, '90'):
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError):
                    self.probe.execute('start', duration=duration)
        self.tcp_connector.assert_not_called()
        self.cgi_fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
