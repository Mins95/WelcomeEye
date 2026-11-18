"""History-only alarm renewal with fake HTTPS responses, never vendor traffic."""
import asyncio
from dataclasses import replace
import unittest
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree as ET

import test_qv_cloud_api as existing
from test_qv_cloud_protocol import envelope
from load_integration import load

api = existing.api
p = existing.p
history = load('qv_cloud_history')
EMPTY_PAGE = envelope('<maxid>0</maxid><record/>')


class Session(existing.Session):
    def request(self, method, url, **kwargs):
        if (url.endswith(p.ALARM_PATH) and kwargs.get('data') is not None
                and ET.fromstring(kwargs['data']).findtext('header/command')
                == 'client-query-recordlist'):
            self.owner.calls.append((self.kind, method, url, kwargs))
            status, payload = self.owner.history_responses.pop(0)
            return existing.Response(payload, status=status)
        return super().request(method, url, **kwargs)


class HistoryRecoveryTests(unittest.IsolatedAsyncioTestCase):
    fixture = existing.QvApiTests.fixture

    async def ready(self, responses, profile=p.CONNECT3_PROFILE):
        client = self.fixture(profile=profile)
        self.history_responses = list(responses)

        def factory(kind):
            session = Session(self, kind)
            self.sessions.append(session)
            return session

        client._session_factory = factory
        await client.async_login('PRIVATE_EMAIL', 'PRIVATE_PASSWORD')
        devices = await client.async_list_devices()
        await client.async_register('PRIVATE_FCM')
        self.calls.clear()
        return client, devices[0]

    def commands(self):
        return [ET.fromstring(args['data']).findtext('header/command')
                for _, method, _, args in self.calls
                if method == 'POST' and args.get('data') is not None]

    async def test_404_rechecks_authorization_and_renews_only_alarm_once(self):
        for profile in (p.CONNECT3_PROFILE, p.R002_PROFILE):
            with self.subTest(profile=profile):
                client, device = await self.ready([(404, b''), (200, EMPTY_PAGE)], profile)
                account, poll = client._account, client._poll_task
                result = await client.async_history(device)
                self.assertEqual(self.commands(), ['client-query-recordlist',
                    'get-device-list', 'client-login', 'client-query-recordlist'])
                self.assertEqual(result['session_refreshes'], 1)
                self.assertEqual(result['live_events_emitted'], 0)
                self.assertIs(client._account, account)
                self.assertIs(client._poll_task, poll)
                self.assertTrue(client._registered)
                self.assertTrue(all(args['ssl'] is True and not args['allow_redirects']
                                    for _, _, _, args in self.calls))
                self.assertFalse(any('PRIVATE' in str(value) for value in result.values()))
                await client.async_close()
                self.assertIsNone(client._alarm_token)

    async def test_second_404_stops_and_repeated_reads_obey_cooldown(self):
        client, device = await self.ready([(404, b'')] * 3)
        with self.assertRaisesRegex(p.QvCloudError, 'provider_rejected'):
            await client.async_history(device)
        self.assertEqual(self.commands().count('client-login'), 1)
        with self.assertRaisesRegex(p.QvCloudError, 'provider_rejected'):
            await client.async_history(device)
        self.assertEqual(self.commands().count('client-login'), 1)
        self.assertEqual(self.commands().count('client-query-recordlist'), 3)

    async def test_other_http_failures_do_not_renew_or_retry(self):
        for status in (400, 401, 403, 429, 500, 503):
            with self.subTest(status=status):
                client, device = await self.ready([(status, b'')])
                with self.assertRaises(p.QvCloudError):
                    await client.async_history(device)
                self.assertEqual(self.commands(), ['client-query-recordlist'])
                await client.async_close()

    async def test_empty_malformed_and_unverified_provider_codes_are_not_expiration(self):
        for body in (b'', b'not XML', envelope(result=1009), envelope(result=1012)):
            with self.subTest(body=body):
                client, device = await self.ready([(200, body)])
                with self.assertRaises(p.QvCloudError):
                    await client.async_history(device)
                self.assertEqual(self.commands(), ['client-query-recordlist'])
                await client.async_close()

    async def test_updated_grants_reject_before_alarm_renewal(self):
        client, device = await self.ready([(404, b'')])

        async def changed_grants():
            client._devices[device.uid] = replace(device, channels=())

        client.async_list_devices = AsyncMock(side_effect=changed_grants)
        with self.assertRaisesRegex(p.QvCloudError, 'unauthorized_device'):
            await client.async_history(device)
        self.assertEqual(self.commands(), ['client-query-recordlist'])

    async def test_failed_auth_channel_or_missing_registration_token_never_renews(self):
        for broken in ('auth', 'token'):
            client, device = await self.ready([(404, b'')])
            if broken == 'auth':
                client._poll_error = ('authentication_failed', False, None)
            else:
                client._alarm_token = None
            with self.assertRaisesRegex(p.QvCloudError, 'provider_rejected'):
                await client.async_history(device)
            self.assertEqual(self.commands(), ['client-query-recordlist'])
            await client.async_close()

    async def test_account_changed_during_read_discards_response(self):
        client, device = await self.ready([(200, EMPTY_PAGE)])
        request = client._request

        async def changed_account(*args, **kwargs):
            result = await request(*args, **kwargs)
            if kwargs.get('history_read'):
                client._account = replace(client._account, session_id='OTHER_SESSION')
            return result

        client._request = changed_account
        with self.assertRaisesRegex(p.QvCloudError, 'not_authenticated'):
            await client.async_history(device)

    async def test_cancel_or_deadline_during_authorization_never_sends_alarm_login(self):
        for expire in (False, True):
            client, device = await self.ready([(404, b'')])
            started = asyncio.Event()

            async def stalled():
                started.set()
                await asyncio.Event().wait()

            client.async_list_devices = AsyncMock(side_effect=stalled)
            with patch.object(history, 'HISTORY_TIMEOUT', 0.05 if expire else 5):
                task = asyncio.create_task(client.async_history(device))
                await asyncio.wait_for(started.wait(), 1)
                if not expire:
                    task.cancel()
                with self.assertRaises(p.QvCloudError if expire else asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
            self.assertEqual(self.commands(), ['client-query-recordlist'])
            self.assertFalse(client._alarm_lock.locked())
            await client.async_close()

    async def test_unload_during_authorization_never_recreates_alarm_session(self):
        client, device = await self.ready([(404, b'')])
        client.async_list_devices = AsyncMock(side_effect=client.async_close)
        with self.assertRaisesRegex(p.QvCloudError, 'closed'):
            await client.async_history(device)
        self.assertFalse(client._sessions)
        self.assertEqual(self.commands(), ['client-query-recordlist'])

    async def test_non_history_404_does_not_get_history_recovery_marker(self):
        client, device = await self.ready([(404, b'')])
        with self.assertRaises(p.QvCloudError) as caught:
            await client._request('alarm', 'POST', client.services.alarm_url,
                p.ALARM_PATH, data=history.build_history_request(client._account.session_id, device))
        self.assertNotIsInstance(caught.exception, api._HistorySessionUnavailable)

    async def test_name_only_refresh_preserves_history_and_subscription_access(self):
        client, device = await self.ready([(404, b''), (200, EMPTY_PAGE)])
        self.device_body = self.device_body.replace(b'<name>Front</name>', b'<name>Renamed</name>')
        self.assertIn(b'Renamed', self.device_body)
        self.assertEqual((await client.async_history(device))['session_refreshes'], 1)
        self.assertEqual(client._devices[device.uid].name, 'Renamed')
        await client.async_query_subscription(device, 0)

    async def test_protocol_scope_changes_still_reject_existing_device(self):
        for changed in ({'shared': True}, {'cloud_type': 1}, {'model': 'OTHER'},
                        {'device_type': 'OTHER'}, {'channels': ()}):
            client, device = await self.ready([(404, b'')])

            async def refreshed():
                client._devices[device.uid] = replace(device, **changed)

            client.async_list_devices = AsyncMock(side_effect=refreshed)
            with self.assertRaisesRegex(p.QvCloudError, 'unauthorized_device'):
                await client.async_history(device)
            self.assertEqual(self.commands(), ['client-query-recordlist'])
            await client.async_close()

    async def test_auth_poll_failure_during_grant_read_prevents_alarm_login(self):
        client, device = await self.ready([(404, b'')])

        async def failed_poll():
            client._poll_error = ('authentication_failed', False, None)

        client.async_list_devices = AsyncMock(side_effect=failed_poll)
        with self.assertRaisesRegex(p.QvCloudError, 'provider_rejected'):
            await client.async_history(device)
        self.assertEqual(self.commands(), ['client-query-recordlist'])

    async def test_registration_queued_behind_history_rechecks_account_before_alarm_send(self):
        client, _ = await self.ready([])
        await client._alarm_lock.acquire()
        task = asyncio.create_task(client.async_register('PRIVATE_FCM'))
        for _ in range(20):
            await asyncio.sleep(0)
            if any(url.endswith(p.PUSH_TOKEN_PATH) for _, _, url, _ in self.calls):
                break
        self.assertFalse(task.done())
        client._account = replace(client._account, session_id='REPLACEMENT')
        client._alarm_lock.release()
        with self.assertRaisesRegex(p.QvCloudError, 'not_authenticated'):
            await task
        self.assertNotIn('client-login', self.commands())

    async def test_later_page_recovery_reuses_cursor_and_body_without_repeating_prior_rows(self):
        from test_qv_cloud_history import page, row
        full = page(''.join(row(str(i)) for i in range(15)))
        client, device = await self.ready([(200, full), (404, b''), (200, page(row('15')))])
        # Use the same authorized UID as the existing history fixtures.
        from test_qv_cloud_history import DEVICE
        client._devices = {DEVICE.uid: DEVICE}
        client.async_list_devices = AsyncMock(return_value=(DEVICE,))
        result = await client.async_history(DEVICE)
        bodies = [args['data'] for _, _, _, args in self.calls
            if args.get('data') is not None and ET.fromstring(args['data']).findtext(
                'header/command') == 'client-query-recordlist']
        self.assertEqual(len(bodies), 3)
        self.assertEqual(bodies[1], bodies[2])
        self.assertEqual(ET.fromstring(bodies[1]).findtext('content/maxid'), '123')
        self.assertEqual(result['record_count'], 16)
        self.assertEqual(result['duplicates_ignored'], 0)
        self.assertEqual(result['pages_read'], 2)

    async def test_cooldown_expiry_allows_one_later_recovery(self):
        client, device = await self.ready([(404, b''), (200, EMPTY_PAGE)] * 2)
        self.assertEqual((await client.async_history(device))['session_refreshes'], 1)
        client._history_refresh_at -= api.HISTORY_SESSION_REFRESH_INTERVAL + 1
        self.assertEqual((await client.async_history(device))['session_refreshes'], 1)
        self.assertEqual(self.commands().count('client-login'), 2)

