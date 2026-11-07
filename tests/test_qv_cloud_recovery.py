"""Temporary cloud outages, bounded retries and lifecycle safety; no network."""
import asyncio
from datetime import datetime, timezone
from email.utils import format_datetime
import errno
import json
import ssl
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiohttp

from load_integration import load
import test_qv_cloud as existing
import test_qv_cloud_api as http
import test_qv_cloud_discovery as directory
from test_qv_cloud_events import payload

cloud = existing.cloud
p = existing.protocol
api = load('qv_cloud_api')
fcm = load('v1_cloud_fcm')
retry = load('cloud_retry')


def wrapped(reason, cause):
    try:
        raise cause
    except Exception:
        try:
            raise fcm.FCMError(reason) from None
        except fcm.FCMError as error:
            return error


class ControlledReceiver(existing.Receiver):
    def __init__(self, owner, *, flap=False):
        super().__init__(owner)
        self.flap = flap
        self.disconnect = asyncio.Event()

    async def run(self):
        self.connected = True
        self.owner._notify()
        self.ready.set()
        try:
            if not self.flap:
                await self.disconnect.wait()
            raise fcm.FCMError('server_closed')
        finally:
            self.connected = False
            self.owner._notify()


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    fixture = existing.QVControllerTests.fixture
    subscription_state = staticmethod(existing.QVControllerTests.subscription_state)
    sent = existing.QVControllerTests.sent

    async def test_long_outage_uses_capped_backoff_then_recovers_without_reload(self):
        for variant in ('connect3', 'connect2_r002'):
            controller, receiver, _, _, _ = self.fixture(variant=variant)
            self.addAsyncCleanup(controller.stop)
            authorize = controller._authorize.side_effect
            attempts, delays = 0, []
            real_sleep = asyncio.sleep

            async def interrupted_authorize(**kwargs):
                nonlocal attempts
                attempts += 1
                if attempts <= 8:
                    raise p.QvCloudError('connection_failed', transient=True)
                await authorize(**kwargs)

            async def fast_sleep(delay):
                if delay:
                    delays.append(delay)
                await real_sleep(0)

            controller._authorize.side_effect = interrupted_authorize
            with patch.object(cloud.asyncio, 'sleep', fast_sleep):
                await controller.start()
                await asyncio.wait_for(receiver.ready.wait(), 2)
            self.assertEqual(delays, [5, 30, 60, 120, 300, 900, 900, 900])
            self.assertEqual(attempts, 9)
            report = controller.diagnostics()
            self.assertTrue(report['connected'])
            self.assertEqual(report['consecutive_failures'], 8)
            self.assertIsNone(report['next_retry_delay'])
            self.assertEqual(report['retry_reason'], 'connection_failed')
            self.assertNotIn('PRIVATE', json.dumps(report))
            await controller.stop()

    async def test_auth_identity_protocol_storage_and_unknown_errors_stop_immediately(self):
        errors = [p.QvCloudError(code) for code in (
            'authentication_failed', 'unauthorized_device', 'invalid_channel',
            'invalid_service', 'invalid_response', 'subscription_mismatch',
            'provider_rejected', 'connection_failed')]
        errors += [cloud.QVCloudStateError(), ValueError('PRIVATE_BAD_FRAME'),
                   PermissionError('PRIVATE_PATH')]
        errors += [fcm.FCMError(reason) for reason in (
            'invalid_credentials', 'identity_changed', 'login_rejected',
            'registration_rejected', 'invalid_checkin', 'protocol_version',
            'frame_length', 'partial_frame_timeout', 'stream_error',
            'registration_failed', 'connection_failed')]
        errors += [wrapped('connection_failed', ssl.SSLCertVerificationError('PRIVATE_TLS')),
                   wrapped('registration_failed', ValueError('PRIVATE_BAD_RESPONSE'))]
        for error in errors:
            with self.subTest(error=type(error).__name__, code=str(error)):
                controller, _, _, _, _ = self.fixture()
                controller._authorize.side_effect = error
                await controller.start()
                await asyncio.wait_for(controller._task, 1)
                self.assertEqual(controller._authorize.await_count, 1)
                self.assertEqual(controller.diagnostics()['status'], 'failed_reload_required')
                self.assertIsNone(controller.diagnostics()['next_retry_delay'])
                self.assertNotIn('PRIVATE', json.dumps(controller.diagnostics()))
                await controller.stop()

    async def test_fcm_socket_cause_retries_but_http_status_and_retry_after_are_checked(self):
        controller, receiver, _, _, _ = self.fixture()
        controller._receiver = receiver
        for reason in ('connection_failed', 'registration_failed'):
            error = wrapped(reason, ConnectionResetError(errno.ECONNRESET, 'PRIVATE_ADDRESS'))
            self.assertEqual(controller._retry_failure(error), ('connection_failed', None))
        self.assertEqual(controller._retry_failure(wrapped('connection_failed',
            asyncio.IncompleteReadError(b'', 1))), ('connection_failed', None))
        for status, delay, expected in ((503, None, ('http_rejected', None)),
                (429, 7200, ('http_rejected', 7200)), (403, None, (None, None)),
                (302, None, (None, None)), (503, -1, (None, None))):
            receiver.diagnostics = lambda: {'http_status': status, 'retry_after_seconds': delay}
            self.assertEqual(controller._retry_failure(fcm.FCMError('http_rejected')), expected)

    async def test_flapping_does_not_reset_backoff_but_healthy_mcs_does_and_replay_stays_filtered(self):
        controller, _, client, calls, _ = self.fixture()
        self.addAsyncCleanup(controller.stop)
        receivers = [ControlledReceiver(controller, flap=index < 4) for index in range(6)]
        factory_index = 0

        def receiver_factory():
            nonlocal factory_index
            if factory_index:
                self.assertTrue(receivers[factory_index - 1].closed)
            receiver = receivers[factory_index]
            factory_index += 1
            return receiver

        controller._receiver_factory.side_effect = receiver_factory
        delays, ready_failures = [], []
        healthy = asyncio.Event()
        real_sleep = asyncio.sleep

        async def fast_sleep(delay):
            if delay:
                delays.append(delay)
            await real_sleep(0)

        def state_changed():
            if controller.connected:
                count = controller._diag['consecutive_failures']
                ready_failures.append(count)
                if factory_index == 5 and count == 0:
                    healthy.set()

        controller._on_state = state_changed
        with patch.object(cloud.asyncio, 'sleep', fast_sleep), \
                patch.object(cloud, 'HEALTHY_CONNECTION_SECONDS', 0.05):
            await controller.start()
            await asyncio.wait_for(receivers[4].ready.wait(), 2)
            self.assertEqual(controller.diagnostics()['consecutive_failures'], 4)
            self.assertEqual(delays, [5, 30, 60, 120])
            event_filter = controller._filter
            now = self.sent()
            await controller._message(payload(), 'PRIVATE_REPLAY', now)
            await asyncio.wait_for(healthy.wait(), 1)
            self.assertEqual(controller.diagnostics()['consecutive_failures'], 0)
            receivers[4].disconnect.set()
            await asyncio.wait_for(receivers[5].ready.wait(), 2)
            self.assertEqual(delays[-1], 5)
            self.assertIs(controller._filter, event_filter)
            await controller._message(payload(), 'PRIVATE_REPLAY', now)
        controller._on_ring.assert_called_once_with(1)
        self.assertEqual(controller.diagnostics()['events']['duplicate'], 1)
        self.assertEqual(set(call for call in calls if call[0] == 'register'),
                         {('register', 'PRIVATE_FCM')})
        self.assertEqual(set(call for call in calls if call[0] == 'subscribe'),
                         {('subscribe', 0, True)})
        self.assertEqual(controller._data['subscriptions'], [1])
        self.assertIn(4, ready_failures)
        await controller.stop()
        self.assertIsNone(controller._healthy_timer)

    async def test_stop_disable_and_reconfigure_cancel_a_long_wait_without_late_attempt(self):
        for change in ('stop', 'disable', 'account', 'identity'):
            controller, _, _, _, _ = self.fixture()
            waiting = asyncio.Event()
            controller._on_state = lambda: waiting.set() if controller._diag['status'] == 'retry_wait' else None
            controller._authorize.side_effect = p.QvCloudError(
                'provider_rejected', transient=True, retry_after=7200)
            await controller.start()
            await asyncio.wait_for(waiting.wait(), 1)
            self.assertEqual(controller.diagnostics()['next_retry_delay'], 7200)
            if change == 'disable':
                controller.entry.data['qv_cloud_enabled'] = False
            elif change == 'account':
                controller.entry.data['qv_cloud_account']['password'] = 'CHANGED_PRIVATE'
            elif change == 'identity':
                controller.entry.unique_id = 'CHANGED_PRIVATE'
            await asyncio.wait_for(controller.stop(), 1)
            self.assertTrue(controller._task.done())
            self.assertEqual(controller._authorize.await_count, 1)
            self.assertIsNone(controller.diagnostics()['next_retry_delay'])
            self.assertIsNone(controller._healthy_timer)
            self.assertFalse(controller.connected)

    async def test_configuration_change_is_rechecked_when_backoff_wakes(self):
        controller, _, _, _, _ = self.fixture()
        controller._authorize.side_effect = p.QvCloudError('connection_failed', transient=True)

        async def disable_during_wait(delay):
            controller.entry.data['qv_cloud_enabled'] = False

        with patch.object(cloud.asyncio, 'sleep', disable_during_wait):
            await controller.start()
            await asyncio.wait_for(controller._task, 1)
        self.assertEqual(controller._authorize.await_count, 1)
        self.assertFalse(controller.connected)
        await controller.stop()


class HTTPRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_status_and_retry_after_keep_existing_public_codes(self):
        for status in (400, 401, 403, 429, 500, 503, 599):
            reply = http.Response(b'PRIVATE_RESPONSE', status=status)
            reply.headers = {'Retry-After': '7200'}
            session = SimpleNamespace(request=Mock(return_value=reply), close=AsyncMock())
            client = api.QvCloudClient(http.SERVICES, http.INSTALLATION,
                session_factory=lambda kind: session)
            with self.assertRaises(p.QvCloudError) as caught:
                await client._request('auth', 'GET', http.SERVICES.auth_url, '/test')
            error = caught.exception
            self.assertEqual(error.code, 'authentication_failed' if status in (401, 403)
                             else 'provider_rejected')
            self.assertEqual(error.transient, status == 429 or status >= 500)
            self.assertEqual(error.retry_after, 7200 if error.transient else None)
            self.assertNotIn('PRIVATE', str(error))
            await client.async_close()

    async def test_socket_failures_are_transient_but_tls_and_malformed_data_are_terminal(self):
        for cause, transient in ((ConnectionResetError(errno.ECONNRESET, 'PRIVATE'), True),
                (TimeoutError('PRIVATE'), True), (ssl.SSLCertVerificationError('PRIVATE'), False),
                (aiohttp.ClientPayloadError('PRIVATE'), False),
                (OSError('PRIVATE_PATH'), False), (UnicodeError('PRIVATE_BODY'), False)):
            session = SimpleNamespace(request=Mock(side_effect=cause), close=AsyncMock())
            client = api.QvCloudClient(http.SERVICES, http.INSTALLATION,
                session_factory=lambda kind: session)
            with self.assertRaises(p.QvCloudError) as caught:
                await client._request('auth', 'GET', http.SERVICES.auth_url, '/test')
            self.assertIs(caught.exception.transient, transient)
            await client.async_close()
        client = http.QvApiTests.fixture(self)
        self.down = asyncio.Queue()
        self.down.put_nowait(b'PRIVATE_MALFORMED_XML')
        with self.assertRaises(p.QvCloudError) as caught:
            await client.async_login('a@example.invalid', 'password')
        self.assertEqual(caught.exception.code, 'invalid_response')
        self.assertFalse(caught.exception.transient)

    async def test_downchannel_failure_preserves_retry_classification(self):
        for status in (401, 403, 503):
            client = http.QvApiTests.fixture(self)
            self.down_status = status
            with self.assertRaises(p.QvCloudError) as caught:
                await client.async_login('a@example.invalid', 'password')
            self.assertEqual(caught.exception.transient, status == 503)
            await client.async_close()

    async def test_directory_classifies_http_socket_and_tls_failures_without_retrying_itself(self):
        for reply, transient in ((directory.Reply(status=503), True),
                (directory.Reply(status=429), True), (directory.Reply(status=403), False),
                (directory.Reply(status=302), False),
                (directory.Reply(error=ConnectionResetError(errno.ECONNRESET, 'PRIVATE')), True),
                (directory.Reply(error=ssl.SSLCertVerificationError('PRIVATE')), False),
                (directory.Reply(body=b'PRIVATE_BAD_XML'), False)):
            session = directory.Session(reply)
            with patch.object(directory.discovery, 'bootstrap_ssl_context', return_value=object()):
                with self.assertRaises(p.QvCloudError) as caught:
                    await directory.discovery.async_resolve_services(p.CONNECT3_PROFILE,
                        client_id=p.make_client_id(directory.INSTALLATION), session=session)
            self.assertIs(caught.exception.transient, transient)
            self.assertEqual(len(session.calls), 1)

    def test_retry_after_never_shortens_a_reasonable_server_delay(self):
        self.assertEqual(retry.retry_after_seconds('7200'), 7200)
        self.assertEqual(retry.retry_after_seconds('999999'), retry.RETRY_AFTER_TOO_LONG)
        self.assertIsNone(retry.retry_after_seconds('PRIVATE_HEADER'))
        future = datetime.fromtimestamp(10000, timezone.utc)
        self.assertEqual(retry.retry_after_seconds(format_datetime(future), now=2800), 7200)
        error = p.QvCloudError('provider_rejected', transient=True,
                              retry_after=retry.retry_after_seconds('999999'))
        self.assertFalse(error.transient)


if __name__ == '__main__':
    unittest.main()
