"""Account handshake, independent registration and cancellation with fake HTTP."""
import asyncio
from dataclasses import replace
import unittest
from xml.etree import ElementTree as ET

from load_integration import load
from test_qv_cloud_protocol import DEVICES, INSTALLATION, envelope

api = load('qv_cloud_api')
p = load('qv_cloud_protocol')
SERVICES = p.QvCloudServices(*(f'https://{name}.qvcloud.net/'
                              for name in ('auth', 'alarm', 'oauth', 'openapi')))


class Response:
    def __init__(self, body, *, status=200, on_headers=None):
        self.body, self.status = body, status
        self.content = self
        self.on_headers = on_headers

    async def __aenter__(self):
        if self.on_headers is not None:
            self.on_headers()
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, size):
        if isinstance(self.body, asyncio.Queue):
            self.body = await self.body.get()
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]


class Session:
    def __init__(self, owner, kind):
        self.owner, self.kind, self.closed = owner, kind, False

    def request(self, method, url, **kwargs):
        self.owner.calls.append((self.kind, method, url, kwargs))
        if url.endswith(p.AUTH_DOWN_PATH):
            return Response(self.owner.down, status=self.owner.down_status,
                            on_headers=lambda: setattr(self.owner, 'cookies_ready', True))
        if url.endswith(p.AUTH_UP_PATH):
            if not self.owner.cookies_ready:
                raise AssertionError('Up channel sent before response cookies were applied')
            root = ET.fromstring(kwargs['data'])
            seq = int(root.findtext('header/seq'))
            command = root.findtext('header/command')
            if self.owner.reply:
                body = (envelope('<account-id>PRIVATE_ACCOUNT</account-id>', seq=seq,
                                 result=self.owner.login_result)
                        if command == 'login' else self.owner.device_body.replace(
                            b'<seq>1</seq>', f'<seq>{seq}</seq>'.encode()))
                self.owner.down.put_nowait(body)
            return Response(b'HTTP ACK IS NOT AUTHENTICATION')
        if url.endswith(p.OAUTH_PATH):
            return Response(b'{"access_token":"PRIVATE_ACCESS","refresh_token":"PRIVATE_REFRESH"}')
        if url.endswith(p.ALARM_PATH):
            if ET.fromstring(kwargs['data']).findtext('header/command') == 'client-query-dev':
                return Response(self.owner.query_body)
            return Response(envelope())
        return Response(b'{"code":0}')

    async def close(self):
        self.closed = True


class QvApiTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, *, profile=p.CONNECT3_PROFILE):
        self.calls, self.sessions = [], []
        self.down = asyncio.Queue()
        self.down.put_nowait(b'')  # Initial cookie-bound down channel acknowledgement.
        self.reply, self.login_result = True, 0
        self.down_status, self.cookies_ready = 200, False
        self.device_body = DEVICES
        self.query_body = envelope('<ifpush>1</ifpush><detail>53188875780096</detail>')

        def factory(kind):
            session = Session(self, kind)
            self.sessions.append(session)
            return session
        client = api.QvCloudClient(SERVICES, INSTALLATION, profile=profile,
                                  session_factory=factory)
        self.addAsyncCleanup(client.async_close)
        return client

    async def test_complete_scoped_handshake_and_disable(self):
        client = self.fixture()
        await client.async_login('PRIVATE_EMAIL@example.invalid', 'PRIVATE_PASSWORD')
        devices = await client.async_list_devices()
        self.assertFalse(any('/openapi' in call[2] for call in self.calls))
        await client.async_register('PRIVATE_FCM')
        await client.async_subscribe(devices[0], 1)
        await client.async_subscribe(devices[0], 1, False)
        alarm_calls = [call for call in self.calls if call[2].endswith(p.ALARM_PATH)]
        self.assertEqual(len(alarm_calls), 3)
        subscribed = ET.fromstring(alarm_calls[1][3]['data'])
        disabled = ET.fromstring(alarm_calls[2][3]['data'])
        self.assertEqual(subscribed.findtext('content/devid'), disabled.findtext('content/devid'))
        self.assertEqual(disabled.findtext('content/ifpush'), '0')
        self.assertEqual({s.kind for s in self.sessions}, {'auth', 'alarm', 'openapi'})
        self.assertTrue(all(call[3]['ssl'] is True and call[3]['allow_redirects'] is False for call in self.calls))
        await client.async_close()
        self.assertTrue(all(s.closed for s in self.sessions))
        self.assertIsNone(client._poll_task)

    async def test_http_ack_does_not_complete_login_and_cancel_closes_down_poll(self):
        client = self.fixture()
        self.reply = False
        task = asyncio.create_task(client.async_login('a@example.invalid', 'p'))
        while not any(call[1] == 'POST' for call in self.calls):
            await asyncio.sleep(0)
        self.assertFalse(task.done())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await client.async_close()
        self.assertFalse(client._pending)
        self.assertTrue(all(s.closed for s in self.sessions))

    async def test_initial_down_body_can_wait_for_login_post(self):
        client = self.fixture()
        self.down.get_nowait()  # Headers arrive, but the first body waits for login.
        self.reply = False
        task = asyncio.create_task(client.async_login('a@example.invalid', 'p'))
        try:
            async with asyncio.timeout(2):
                while not any(call[1] == 'POST' for call in self.calls):
                    await asyncio.sleep(0)
                self.assertTrue(self.cookies_ready)
                self.assertFalse(task.done())
                self.assertIsNone(client._account)
                post = next(call for call in self.calls if call[1] == 'POST')
                sequence = int(ET.fromstring(post[3]['data']).findtext('header/seq'))
                self.down.put_nowait(envelope('<account-id>PRIVATE_ACCOUNT</account-id>',
                                             seq=sequence))
                account = await task
                self.assertEqual(account.account_id, 'PRIVATE_ACCOUNT')
        finally:
            if not task.done():
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

    async def test_bad_initial_down_status_never_sends_credentials(self):
        for status, code in ((401, 'authentication_failed'), (403, 'authentication_failed'),
                             (302, 'provider_rejected'), (503, 'provider_rejected')):
            client = self.fixture()
            self.down_status = status
            with self.assertRaises(p.QvCloudError) as raised:
                await client.async_login('PRIVATE_EMAIL', 'PRIVATE_PASSWORD')
            self.assertEqual(raised.exception.code, code)
            self.assertIsNone(client._account)
            self.assertFalse(any(call[1] == 'POST' for call in self.calls))
            await client.async_close()

    async def test_cancelled_close_clears_secrets_and_finishes_the_same_cleanup(self):
        client = self.fixture()
        await client.async_login('PRIVATE_EMAIL', 'PRIVATE_PASSWORD')
        await client.async_list_devices()
        await client.async_register('PRIVATE_FCM')
        auth = next(session for session in self.sessions if session.kind == 'auth')
        closing, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def delayed_close():
            nonlocal calls
            calls += 1
            closing.set()
            await release.wait()
            auth.closed = True

        auth.close = delayed_close
        waiter = asyncio.create_task(client.async_close())
        await closing.wait()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertTrue(client._closed)
        self.assertTrue(all(getattr(client, field) is None for field in
                            ('_account', '_email', '_digest', '_oauth')))
        self.assertFalse(client._devices)
        self.assertFalse(client._registered)
        self.assertFalse(client._close_task.done())
        release.set()
        async with asyncio.timeout(1):
            await client.async_close()
        self.assertTrue(all(session.closed for session in self.sessions))
        self.assertFalse(client._sessions)
        self.assertEqual(calls, 1)
        self.assertTrue(client._close_task.done())

    async def test_invalid_account_never_registers_or_retries_credentials(self):
        client = self.fixture()
        self.login_result = 100101006
        with self.assertRaises(p.QvCloudError) as raised:
            await client.async_login('PRIVATE_EMAIL', 'PRIVATE_PASSWORD')
        self.assertEqual(str(raised.exception), 'authentication_failed')
        self.assertEqual(sum(call[1] == 'POST' for call in self.calls), 1)
        with self.assertRaises(p.QvCloudError):
            await client.async_register('PRIVATE_FCM')
        self.assertFalse(any('openapi' in call[2] for call in self.calls))

    async def test_unlisted_device_and_channel_never_reach_provider(self):
        client = self.fixture()
        await client.async_login('a@example.invalid', 'p')
        device = (await client.async_list_devices())[0]
        await client.async_register('PRIVATE_FCM')
        count = len(self.calls)
        with self.assertRaises(p.QvCloudError):
            await client.async_subscribe(replace(device, uid='UNLISTED'), 0)
        with self.assertRaises(p.QvCloudError):
            await client.async_subscribe(device, 999)
        self.assertEqual(len(self.calls), count)

    async def test_device_scope_readback_preserves_session_profile_and_token(self):
        client = self.fixture(profile=p.R002_PROFILE)
        self.device_body = envelope('<device><id>PRIVATE_UID</id><type>IDS</type>'
                                    '<model>IDS94E6SW</model><channel-num>2</channel-num></device>')
        await client.async_login('PRIVATE_EMAIL', 'PRIVATE_PASSWORD')
        device, = await client.async_list_devices()
        await client.async_register('PRIVATE_FCM')
        await client.async_subscribe(device, None)
        state = await client.async_query_subscription(device, None)
        self.assertEqual(state.effective_call_events, p.CALL_EVENTS)
        self.assertEqual(state.effective_call_mask, p.CALL_DETAIL)
        self.assertTrue(state.enabled)
        await client.async_subscribe(device, None, False)
        self.query_body = envelope('<ifpush>0</ifpush><detail>0</detail>')
        self.assertFalse((await client.async_query_subscription(device, None)).enabled)
        info = next(call for call in self.calls if call[2].endswith(p.PUSH_INFO_PATH))
        token = next(call for call in self.calls if call[2].endswith(p.PUSH_TOKEN_PATH))
        self.assertEqual(info[3]['json']['appId'], '4058')
        self.assertEqual(info[3]['json']['oemId'], p.R002_PROFILE.oem_id)
        self.assertEqual(info[3]['json']['clientId'], '003-4058-' + INSTALLATION)
        self.assertEqual(token[3]['json']['serverTypeToken'],
                         [{'serverType': 'fcm', 'pushToken': 'PRIVATE_FCM'}])
        self.assertEqual(token[3]['headers'], {'token': 'PRIVATE_ACCESS'})
        alarms = [call for call in self.calls if call[2].endswith(p.ALARM_PATH)]
        login = ET.fromstring(alarms[0][3]['data'])
        self.assertEqual(login.findtext('content/client/id'), '003-4058-' + INSTALLATION)
        self.assertEqual(login.findtext('content/client/token'), 'PRIVATE_FCM')
        for call in alarms:
            body = ET.fromstring(call[3]['data'])
            self.assertEqual(body.findtext('header/session'), 'PRIVATE_SESSION')
            self.assertEqual(body.findtext('header/seq'), '0')
            self.assertIsNone(body.find('content/chno'))
            self.assertEqual(call[3]['headers'], api.XML_HEADERS)
        query = ET.fromstring(alarms[2][3]['data'])
        self.assertEqual(query.findtext('header/command'), 'client-query-dev')
        self.assertEqual(query.findtext('content/ifdetail'), '1')

    async def test_query_is_registered_authorized_and_rejects_unverified_ack(self):
        client = self.fixture()
        await client.async_login('a@example.invalid', 'p')
        owned, shared = await client.async_list_devices()
        count = len(self.calls)
        with self.assertRaises(p.QvCloudError) as raised:
            await client.async_query_subscription(owned, 0)
        self.assertEqual(raised.exception.code, 'not_registered')
        self.assertEqual(len(self.calls), count)
        await client.async_register('PRIVATE_FCM')
        count = len(self.calls)
        for device, channel in ((replace(owned, uid='UNLISTED'), 0),
                                (replace(owned, device_type='IDS'), None),
                                (shared, None), (shared, 0), (owned, 99)):
            with self.subTest(device=device, channel=channel):
                with self.assertRaises(p.QvCloudError):
                    await client.async_query_subscription(device, channel)
        self.assertEqual(len(self.calls), count)
        # An owned unknown model can be inspected at device scope, but cannot
        # acquire that broader subscription from a successful query alone.
        self.assertTrue((await client.async_query_subscription(owned, None)).enabled)
        with self.assertRaises(p.QvCloudError):
            await client.async_subscribe(owned, None)
        await client.async_query_subscription(shared, 2)
        self.query_body = envelope()
        with self.assertRaises(p.QvCloudError) as raised:
            await client.async_query_subscription(owned, 0)
        self.assertEqual(raised.exception.code, 'invalid_response')
