"""Synthetic XML and mocked HTTPS only. No real auth code or certificate."""
import asyncio
from hashlib import sha256
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from xml.etree import ElementTree as ET

from load_integration import load

cgi = load('connect3.cgi')
hub_module = load('connect3.hub')
SECRET = 'SYNTHETIC_LOCAL_AUTH'


def reply(content, error='0'):
    return f'<envelope><body><error>{error}</error><content>{content}</content></body></envelope>'.encode()


def page(more=0):
    return reply(f'<record><page>{more}</page><data><channel>1</channel><filesize>123</filesize>'
                 '<idf>0</idf><ids>0</ids><idx>42</idx><filetype>picture</filetype>'
                 '<occurtype>event</occurtype><starttime>2026-10-01 10:00:00</starttime>'
                 '<endtime>2026-10-01 10:00:00</endtime>'
                 '<filename>SYNTHETIC_PRIVATE_FILENAME</filename></data></record>')


class XMLTests(unittest.TestCase):
    def test_exact_lan_header_and_encoding(self):
        xml = ET.fromstring(cgi.envelope('get.device.streamkey', SECRET))
        self.assertEqual(xml.tag, 'envelope')
        self.assertEqual({n.tag: n.text for n in xml.find('header')}, dict(
            security='username', username='adminapp2', password=sha256(SECRET.encode()).hexdigest(), passwordencode='1'))
        self.assertEqual(xml.findtext('body/command'), 'get.device.streamkey')
        self.assertEqual(cgi.encode_auth_code('a' * 64), 'a' * 64)
        self.assertEqual(cgi.encode_auth_code('\U0001f603' * 32), '\U0001f603' * 32)
        escaped = ET.fromstring(cgi.envelope('get.record.message', SECRET, {'id': '<synthetic>&'}))
        self.assertEqual(escaped.findtext('body/content/record/id'), '<synthetic>&')

    def test_no_other_command_even_direct_api(self):
        for command in ('set.device.opendoor', 'get.device.streamkey\0', 'POST', ''):
            with self.assertRaises(cgi.CGIError):
                cgi.envelope(command, SECRET)
        with self.assertRaises(cgi.CGIError):
            cgi.envelope('get.device.streamkey', SECRET, {'extra': 'x'})

    def test_secrets_never_returned_by_key_summary(self):
        result = cgi.streamkey_summary(reply('<key>PRIVATE_KEY</key><tdc>PRIVATE_PASSWORD</tdc><synctime>PRIVATE_EXPIRY</synctime>'))
        self.assertTrue(result['streamkey_received'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertFalse(result['media_available'])

    def test_xml_limits_entities_duplicate_fields_and_errors(self):
        invalid = [b'', b'x' * (cgi.MAX_XML + 1), reply('', '401'), reply('<key/>'),
                   reply('<key>x</key><key>y</key>'), b'\xff',
                   reply('<key>x</key>')[:-1], b'<envelope><body/></envelope>',
                   b'<!DOCTYPE envelope [<!ENTITY x "secret">]>' + reply('<key>&x;</key>'),
                   reply('<a>' * 14 + '<key>x</key>' + '</a>' * 14),
                   reply('<a/>' * (cgi.MAX_NODES + 1)),
                   reply('<key>x</key>').decode().encode('utf-16')]
        for data in invalid:
            with self.subTest(size=len(data)):
                with self.assertRaises(cgi.CGIError) as error:
                    cgi.streamkey_summary(data)
                self.assertNotIn('secret', str(error.exception))

    def test_history_range_page_and_no_ring_claim(self):
        fields = cgi.history_fields('2026-10-01 00:00:00', '2026-10-01 23:59:59', 1)
        self.assertEqual(fields['filetype'], 'picture')
        self.assertEqual(fields['starttime'], '2026-10-01t00:00:00z')
        self.assertEqual(fields['endtime'], '2026-10-01t23:59:59z')
        for start, end, channel in [('bad', 'bad', 1), ('2026-10-01 00:00:00', '2026-10-03 00:00:00', 1),
                                    ('2026-10-01 00:00:00', '2026-10-01 01:00:00', True)]:
            with self.assertRaises(cgi.CGIError):
                cgi.history_fields(start, end, channel)
        records, more = cgi.record_page(page())
        self.assertEqual(len(records), 1)
        self.assertFalse(more)
        for data in (reply('<record/>'), reply('<page>1</page><page>2</page>'), reply('<page>-1</page>')):
            with self.assertRaises(cgi.CGIError):
                cgi.record_page(data)


class ReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_access_https_only_no_retry_secret_retention(self):
        with patch.object(cgi, '_post', AsyncMock(return_value=reply('<key>PRIVATE</key>'))) as post:
            result = await cgi.read_device('192.0.2.1', SECRET, certificate_sha256='a' * 64)
        post.assert_awaited_once()
        self.assertEqual(post.await_args.args[1], 'https://192.0.2.1:443/tdkcgi')
        self.assertIsInstance(post.await_args.args[2], cgi.aiohttp.Fingerprint)
        self.assertEqual(result['authentication'], 'cgi_accepted')
        self.assertTrue(post.await_args.args[0].closed)

    async def test_history_bound_and_duplicates_with_private_details_opt_in(self):
        for detailed in (False, True):
            with patch.object(cgi, '_post', AsyncMock(side_effect=[reply('<record><id>PRIVATE_SESSION</id></record>')] + [page(1)] * 4)) as post:
                result = await cgi.read_device('192.0.2.1', SECRET, operation='history',
                    start='2026-10-01 00:00:00', end='2026-10-01 23:59:59', include_details=detailed)
            self.assertEqual(post.await_count, 5)
            self.assertEqual(result['record_count'], 1)
            self.assertFalse(result['history_complete'])
            self.assertFalse(result['photo_download_available'])
            self.assertFalse(result['ring_correlation_verified'])
            self.assertEqual('records' in result, detailed)
            self.assertNotIn('PRIVATE_SESSION', json.dumps(result))
            self.assertTrue(post.await_args.args[0].closed)

    async def test_complete_history_and_failure_never_retried(self):
        with patch.object(cgi, '_post', AsyncMock(side_effect=[reply('<record><id>x</id></record>'), page(0)])) as post:
            result = await cgi.read_device('192.0.2.1', SECRET, operation='history',
                start='2026-10-01 00:00:00', end='2026-10-01 01:00:00')
        self.assertEqual(post.await_count, 2)
        self.assertTrue(result['history_complete'])
        with patch.object(cgi, '_post', AsyncMock(side_effect=TimeoutError('PRIVATE'))) as post:
            with self.assertRaises(TimeoutError):
                await cgi.read_device('192.0.2.1', SECRET)
        post.assert_awaited_once()
        self.assertTrue(post.await_args.args[0].closed)

    async def test_cancellation_and_deadline_close_session(self):
        entered = asyncio.Event()
        sessions = []
        async def block(session, *args):
            sessions.append(session)
            entered.set()
            await asyncio.Future()
        with patch.object(cgi, '_post', side_effect=block), patch.object(cgi, 'TIMEOUT', .01):
            with self.assertRaises(TimeoutError):
                await cgi.read_device('192.0.2.1', SECRET)
        self.assertTrue(sessions[-1].closed)
        entered.clear()
        with patch.object(cgi, '_post', side_effect=block):
            task = asyncio.create_task(cgi.read_device('192.0.2.1', SECRET))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(sessions[-1].closed)

    async def test_post_redirect_body_limit_and_no_follow(self):
        class Content:
            async def iter_chunked(self, size):
                yield b'x' * (cgi.MAX_XML + 1)
        response = SimpleNamespace(status=302, content_length=None, content=Content())
        context = AsyncMock()
        context.__aenter__.return_value = response
        session = SimpleNamespace(post=Mock(return_value=context))
        for status in (302, 401, 200):
            response.status = status
            with self.assertRaises(cgi.CGIError):
                await cgi._post(session, 'https://192.0.2.1/tdkcgi', True, b'SYNTHETIC')
        self.assertFalse(session.post.call_args.kwargs['allow_redirects'])
        self.assertEqual(context.__aexit__.await_count, 3)

    async def test_invalid_inputs_never_open_connection(self):
        for kwargs in ({'operation': 'open_door'}, {'port': 0}, {'certificate_sha256': 'bad'},
                       {'operation': 'history', 'start': 'bad', 'end': 'bad'}):
            with patch.object(cgi.aiohttp, 'ClientSession') as session:
                with self.assertRaises(cgi.CGIError):
                    await cgi.read_device('192.0.2.1', SECRET, **kwargs)
            session.assert_not_called()


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = hub_module.Connect3Hub(None, SimpleNamespace(data={'host': '192.0.2.1'}))
        await self.hub.start()

    async def test_no_credentials_and_no_network_at_start(self):
        with patch.object(hub_module, 'read_device', AsyncMock()) as read:
            result = await self.hub.execute('access')
        self.assertEqual(result['reason'], 'local_auth_code_required')
        read.assert_not_called()

    async def test_detailed_result_not_persisted_and_errors_sanitized(self):
        with patch.object(hub_module, 'discover', AsyncMock(return_value={
            'decoded_records': 1, 'records': [{'firmware': 'PRIVATE'}], 'status': 'observed'})):
            result = await self.hub.execute('discovery', include_details=True)
        self.assertIn('PRIVATE', str(result))
        self.assertNotIn('PRIVATE', json.dumps(self.hub.diagnostics()))
        self.hub.entry.data['auth_code'] = SECRET
        with patch.object(hub_module, 'read_device', AsyncMock(side_effect=OSError('PRIVATE secret URL'))):
            result = await self.hub.execute('access')
        self.assertEqual(result['last_error_type'], 'NetworkError')
        for private in ('PRIVATE', SECRET, '192.0.2.1'):
            self.assertNotIn(private, json.dumps(self.hub.diagnostics()))

    async def test_single_operation_unload_cancels_no_callback_or_orphan(self):
        entered = asyncio.Event()
        async def block(*args, **kwargs):
            entered.set()
            await asyncio.Future()
        callback = Mock()
        self.hub.subscribe(callback)
        with patch.object(hub_module, 'discover', side_effect=block):
            task = asyncio.create_task(self.hub.execute('discovery'))
            await entered.wait()
            with self.assertRaises(RuntimeError):
                await self.hub.execute('discovery')
            before = callback.call_count
            await self.hub.stop(reason='home_assistant_stop')
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(callback.call_count, before)
            self.assertIsNone(self.hub._task)
            self.assertFalse(self.hub.listeners)
        with self.assertRaises(RuntimeError):
            await self.hub.execute('discovery')

    async def test_physical_and_media_operations_fail_closed(self):
        for operation in ('strike', 'gate', 'login', 'media', 'microphone', 'r002_probe'):
            with self.assertRaises(ValueError):
                await self.hub.execute(operation)
        self.assertEqual(self.hub.runs, 0)
