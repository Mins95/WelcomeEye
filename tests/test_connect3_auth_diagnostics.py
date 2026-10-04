"""APK-backed auth distinctions using synthetic XML and mocked HTTPS only."""
import asyncio
from hashlib import sha256
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load

cgi = load('connect3.cgi')
hub_module = load('connect3.hub')
AUTH = 'SYNTHETIC_DIAGNOSTICS_AUTH'
KEY = 'SYNTHETIC_DIAGNOSTICS_KEY'
PIN = sha256(b'SYNTHETIC_DIAGNOSTICS_CERTIFICATE').hexdigest()


def response(error=0, content=''):
    return (f'<envelope><body><error>{error}</error><content>{content}'
            '</content></body></envelope>').encode()


class AuthDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = hub_module.Connect3Hub(None, SimpleNamespace(data={
            'host': '192.0.2.33', 'auth_code': AUTH, 'certificate_sha256': PIN}))
        await self.hub.start()

    async def asyncTearDown(self):
        await self.hub.stop()
        self.assertIsNone(self.hub._task)

    async def execute_response(self, body):
        with patch.object(cgi, '_post', AsyncMock(return_value=body)) as post:
            result = await self.hub.execute('access')
        post.assert_awaited_once()
        self.assertTrue(post.await_args.args[0].closed)
        return result

    async def test_xml_401_is_the_apk_auth_code_error_not_http_challenge(self):
        result = await self.execute_response(response(401, '<key>PRIVATE</key>'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'auth_code_rejected')
        self.assertEqual(result['device_error_code'], 401)
        self.assertEqual(result['error_source'], 'xml_device')
        self.assertEqual(result['last_stage'], 'xml_response')
        self.assertEqual(result['authentication_status'], 'rejected')
        self.assertFalse(result['device_authenticated'])
        self.assertNotIn('http_status', result)

    async def test_http_401_keeps_its_origin_without_claiming_wrong_authcode(self):
        class Content:
            async def iter_chunked(self, size):
                raise AssertionError('No rejected HTTP body may be consumed')
                yield b''

        context = AsyncMock()
        context.__aenter__.return_value = SimpleNamespace(status=401,
            content_length=None, content=Content())
        session = SimpleNamespace(post=Mock(return_value=context))
        observation = {}
        with self.assertRaises(cgi.CGIError) as caught:
            await cgi._post(session, 'https://192.0.2.33/tdkcgi', True,
                            b'SYNTHETIC_REQUEST', observation)
        error = caught.exception
        self.assertEqual(str(error), 'http_unauthorized')
        self.assertEqual(error.http_status, 401)
        self.assertIsNone(error.device_error_code)
        session.post.assert_called_once()
        self.assertFalse(session.post.call_args.kwargs['allow_redirects'])
        with patch.object(cgi, '_post', AsyncMock(side_effect=error)) as post:
            result = await self.hub.execute('access')
        post.assert_awaited_once()
        self.assertEqual(result['reason'], 'http_unauthorized')
        self.assertEqual(result['http_status'], 401)
        self.assertEqual(result['error_source'], 'http')
        self.assertEqual(result['authentication_status'], 'not_checked')
        self.assertFalse(result['device_authenticated'])
        self.assertNotIn('device_error_code', result)

    async def test_other_device_error_preserves_code_without_auth_guess(self):
        for code in (-1, 403, 500):
            with self.subTest(code=code):
                result = await self.execute_response(response(code))
                self.assertEqual(result['reason'], 'device_rejected')
                self.assertEqual(result['device_error_code'], code)
                self.assertEqual(result['authentication_status'], 'not_checked')
                self.assertFalse(self.hub.diagnostics()['device_authenticated'])

    async def test_only_valid_new_key_response_confirms_authentication(self):
        result = await self.execute_response(response(0, f'<key>{KEY}</key>'))
        self.assertTrue(result['device_authenticated'])
        self.assertEqual(result['authentication_status'], 'accepted')
        self.assertEqual(result['last_stage'], 'cgi_accepted')
        self.assertTrue(self.hub.diagnostics()['device_authenticated'])
        self.assertEqual(self.hub.diagnostics()['authentication'],
                         {'status': 'accepted', 'operation': 'access'})
        with patch.object(hub_module, 'discover', AsyncMock(return_value={
            'decoded_records': 1, 'status': 'observed'})):
            await self.hub.execute('discovery')
        self.assertTrue(self.hub.diagnostics()['device_authenticated'])
        for body in (response(0, '<key/>'), response(0), b'<PRIVATE MALFORMED'):
            with self.subTest(body=body):
                result = await self.execute_response(body)
                self.assertEqual(result['status'], 'failed')
                self.assertFalse(result['device_authenticated'])
                self.assertFalse(self.hub.diagnostics()['device_authenticated'])

    async def test_later_refusal_or_network_failure_clears_previous_acceptance(self):
        for failure in (response(401), TimeoutError('PRIVATE_URL_AUTH'),
                        OSError('PRIVATE_URL_AUTH')):
            await self.execute_response(response(0, f'<key>{KEY}</key>'))
            self.assertTrue(self.hub.diagnostics()['device_authenticated'])
            if isinstance(failure, bytes):
                result = await self.execute_response(failure)
            else:
                with patch.object(cgi, '_post', AsyncMock(side_effect=failure)) as post:
                    result = await self.hub.execute('access')
                post.assert_awaited_once()
            self.assertFalse(result['device_authenticated'])
            self.assertFalse(self.hub.diagnostics()['device_authenticated'])
            self.assertNotIn('PRIVATE', json.dumps([result, self.hub.diagnostics()]))

    async def test_credentials_required_and_identity_failure_do_not_keep_acceptance(self):
        await self.execute_response(response(0, f'<key>{KEY}</key>'))
        self.hub.entry.data.pop('auth_code')
        result = await self.hub.execute('access')
        self.assertEqual(result['reason'], 'local_auth_code_required')
        self.assertFalse(self.hub.diagnostics()['device_authenticated'])
        self.hub.entry.data.update(auth_code=AUTH, credential_source='apk_space',
                                   credential_device_uid='SYNTHETIC_PRIVATE_UID')
        with patch.object(hub_module, 'discover', AsyncMock(return_value={
            'credential_identity_status': 'mismatch'})), patch.object(
            hub_module, 'read_device', AsyncMock()) as read:
            result = await self.hub.execute('access')
        read.assert_not_called()
        self.assertEqual(result['reason'], 'credential_identity_not_matched')
        self.assertFalse(self.hub.diagnostics()['device_authenticated'])

    async def test_acceptance_diagnostics_exclude_all_private_material(self):
        body = response(0, f'<key>{KEY}</key><tdc>SYNTHETIC_PRIVATE_PASSWORD</tdc>'
                           '<synctime>SYNTHETIC_PRIVATE_EXPIRY</synctime>')
        result = await self.execute_response(body)
        serialized = json.dumps([result, self.hub.diagnostics()])
        for private in (AUTH, sha256(AUTH.encode()).hexdigest(), KEY, PIN,
                        '192.0.2.33', 'SYNTHETIC_PRIVATE_PASSWORD',
                        'SYNTHETIC_PRIVATE_EXPIRY', '/tdkcgi', '<envelope>'):
            self.assertNotIn(private, serialized)

    async def test_cancelled_new_authentication_clears_acceptance_and_task(self):
        await self.execute_response(response(0, f'<key>{KEY}</key>'))
        entered = asyncio.Event()
        sessions = []

        async def block(session, *args):
            sessions.append(session)
            entered.set()
            await asyncio.Future()

        with patch.object(cgi, '_post', side_effect=block) as post:
            task = asyncio.create_task(self.hub.execute('access'))
            await entered.wait()
            await self.hub.stop()
            with self.assertRaises(asyncio.CancelledError):
                await task
        post.assert_awaited_once()
        self.assertTrue(sessions[0].closed)
        self.assertFalse(self.hub.diagnostics()['device_authenticated'])
        self.assertEqual(self.hub.diagnostics()['last_operation']['status'], 'cancelled')
        self.assertIsNone(self.hub._task)

    async def test_trace_copies_only_phases_and_counts_no_remote_parameters(self):
        observation = {'request_sent_count': 0, 'authentication_status': 'not_checked'}
        trace = cgi._trace(observation)
        private = SimpleNamespace(url='PRIVATE_URL', headers={'secret': AUTH})
        await trace.on_connection_create_start[0](None, None, private)
        self.assertEqual(observation['last_stage'], 'tcp_tls_connect')
        await trace.on_connection_create_end[0](None, None, private)
        self.assertTrue(observation['tls_verified'])
        self.assertEqual(observation['authentication_status'], 'not_checked')
        await trace.on_request_headers_sent[0](None, None, private)
        self.assertEqual(observation['request_sent_count'], 1)
        self.assertEqual(observation['last_stage'], 'request_sent')
        self.assertNotIn('PRIVATE', json.dumps(observation))
        self.assertNotIn(AUTH, json.dumps(observation))


if __name__ == '__main__':
    unittest.main()
