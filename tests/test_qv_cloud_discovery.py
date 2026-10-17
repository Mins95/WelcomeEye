"""Synthetic native-directory fixtures; never contacts vendor services."""
import asyncio
import ssl
import unittest
from xml.etree import ElementTree as ET

from load_integration import load

discovery = load('qv_cloud_discovery')
protocol = load('qv_cloud_protocol')
INSTALLATION = '2a7e0401-7baf-46a9-8710-33585d463358'


def response():
    root = ET.Element('envelope')
    header = ET.SubElement(root, 'header')
    ET.SubElement(header, 'result').text = '0'
    ET.SubElement(header, 'seq').text = '1'
    content = ET.SubElement(root, 'content')
    ET.SubElement(content, 'client-regionid').text = '2'
    for name, host in (('userapp', 'http://legacy.qvcloud.net:8080'),
                       ('sec-userapp', 'https://auth.qvcloud.net'),
                       ('sec-alarmapp', 'https://alarm.qvcloud.net:4443'),
                       ('oauth2', 'https://oauth.qvcloud.net'),
                       ('openapi', 'https://api.qvcloud.net')):
        server = ET.SubElement(content, 'server')
        for key, value in (('server-type', name), ('query-result', '1'),
                           ('regionid', '2'), ('url', host), ('uri', ''), ('param', '')):
            ET.SubElement(server, key).text = value
    return ET.tostring(root)


class FormatTests(unittest.TestCase):
    def test_native_request_profile_and_own_identity(self):
        for profile in (protocol.CONNECT3_PROFILE, protocol.R002_PROFILE):
            client_id = protocol.make_client_id(INSTALLATION, profile)
            payload = discovery.build_discovery_request(profile, client_id, 7)
            root = ET.fromstring(payload)
            self.assertEqual(root.findtext('header/command'), 'query-hlrv2')
            self.assertEqual(root.findtext('content/server-type'), discovery.SERVICE_TYPES)
            self.assertEqual(root.findtext('content/oem'), profile.oem_id)
            self.assertEqual(root.findtext('content/client-id'), client_id)
            self.assertEqual(root.findtext('content/version'), '4456')
            self.assertEqual(root.findtext('content/regionid'), '7')
            self.assertEqual(root.findtext('content/devid'), '')
            self.assertEqual(root.findtext('content/public-ip'), '')
            self.assertNotIn(b'password', payload)
            self.assertNotIn(b'username', payload)

    def test_secure_services_selected_without_legacy_fallback(self):
        services = discovery.parse_discovery_response(response())
        self.assertEqual(services.auth_url, 'https://auth.qvcloud.net/')
        self.assertEqual(services.alarm_url, 'https://alarm.qvcloud.net:4443/')
        self.assertEqual(services.region_id, 2)
        self.assertNotIn('qvcloud', repr(services))

    def test_discovery_rejects_unsafe_ambiguous_and_incomplete_results(self):
        duplicate = ET.fromstring(response())
        duplicate.find('content').append(ET.fromstring(ET.tostring(duplicate.findall('content/server')[1])))
        cases = [
            response().replace(b'https://auth.qvcloud.net', b'http://auth.qvcloud.net'),
            response().replace(b'https://auth.qvcloud.net', b'https://evil.example'),
            response().replace(b'https://auth.qvcloud.net', b'https://127.0.0.1'),
            response().replace(b'https://auth.qvcloud.net', b'https://auth.qvcloud.net:8443'),
            response().replace(b'<seq>1</seq>', b'<seq>2</seq>'),
            response().replace(b'<seq>1</seq>', b'<seq>1</seq><seq>1</seq>'),
            response().replace(b'<result>0</result>', b'<result>9</result>'),
            response().replace(b'<query-result>1</query-result>', b'<query-result>0</query-result>'),
            response().replace(b'<server-type>oauth2</server-type>', b'<server-type>unknown</server-type>'),
            response().replace(b'<client-regionid>2</client-regionid>', b''),
            ET.tostring(duplicate),
            b'<!DOCTYPE envelope [<!ENTITY x "test">]><envelope>&x;</envelope>',
            b'not xml', b'x' * (discovery.MAX_DISCOVERY_BYTES + 1),
        ]
        for payload in cases:
            with self.subTest(payload=payload[:35]):
                with self.assertRaises(protocol.QvCloudError):
                    discovery.parse_discovery_response(payload)

    def test_invalid_profile_identity_or_region_never_builds_request(self):
        client_id = protocol.make_client_id(INSTALLATION)
        for profile, cid, region in ((protocol.R002_PROFILE, client_id, 0),
                (protocol.CONNECT3_PROFILE, 'phone-identity', 0),
                (protocol.CONNECT3_PROFILE, client_id, -1),
                (protocol.CONNECT3_PROFILE, client_id, True)):
            with self.assertRaises(protocol.QvCloudError):
                discovery.build_discovery_request(profile, cid, region)


class Reply:
    def __init__(self, body=None, status=200, error=None):
        self.body = response() if body is None else body
        self.status, self.error, self.closed = status, error, False
        self.content = self

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def iter_chunked(self, size):
        for pos in range(0, len(self.body), size):
            yield self.body[pos:pos + size]


class Session:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.reply


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def run_lookup(self, session):
        return await discovery.async_resolve_services(protocol.CONNECT3_PROFILE,
            client_id=protocol.make_client_id(INSTALLATION), session=session)

    async def test_exact_get_body_tls_and_redirect_policy(self):
        reply = Reply()
        session = Session(reply)
        await self.run_lookup(session)
        args, kwargs = session.calls[0]
        self.assertEqual(args, ('GET', 'https://sh2.qvcloud.net/mst/query'))
        self.assertIsInstance(kwargs['ssl'], ssl.SSLContext)
        self.assertEqual(kwargs['ssl'].verify_mode, ssl.CERT_REQUIRED)
        self.assertFalse(kwargs['ssl'].check_hostname)
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['headers'], {'Content-Type': discovery.CONTENT_TYPE})
        self.assertEqual(ET.fromstring(kwargs['data']).findtext('header/command'), 'query-hlrv2')
        self.assertTrue(reply.closed)

    async def test_failures_are_bounded_sanitized_and_never_retried(self):
        for reply, code in ((Reply(status=302), 'connection_failed'),
                (Reply(body=b'x' * 65537), 'response_too_large'),
                (Reply(error=OSError('private-host')), 'connection_failed'),
                (Reply(error=TimeoutError('private-host')), 'request_timeout')):
            session = Session(reply)
            with self.subTest(code=code):
                with self.assertRaises(protocol.QvCloudError) as raised:
                    await self.run_lookup(session)
                self.assertEqual(str(raised.exception), code)
                self.assertEqual(len(session.calls), 1)

    async def test_cancellation_is_not_retried_or_wrapped(self):
        session = Session(Reply(error=asyncio.CancelledError()))
        with self.assertRaises(asyncio.CancelledError):
            await self.run_lookup(session)
        self.assertEqual(len(session.calls), 1)


if __name__ == '__main__':
    unittest.main()
