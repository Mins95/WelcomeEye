"""Synthetic QV account XML fixtures and scope boundaries, no live credentials."""
import hashlib
import unittest
from xml.etree import ElementTree as ET

from load_integration import load

p = load('qv_cloud_protocol')
INSTALLATION = '77777777-1111-2222-3333-444444444444'
CLIENT = p.make_client_id(INSTALLATION)


def envelope(content='', *, result=0, seq=1, session='PRIVATE_SESSION'):
    return (f'<envelope><header><seq>{seq}</seq><result>{result}</result>'
            f'<session><id>{session}</id></session></header>'
            f'<content>{content}</content></envelope>').encode()


DEVICES = envelope('<count>2</count><device><id>PRIVATE_UID</id><name>Front</name>'
                   '<channel-num>2</channel-num><is-hs-device>0</is-hs-device></device>'
                   '<device><id>PRIVATE_SHARED</id><from-share>1</from-share>'
                   '<channel-num>4</channel-num><auth-chans><count>1</count>'
                   '<item><channel>2</channel></item></auth-chans></device>')


class QvProtocolTests(unittest.TestCase):
    def test_profiles_are_separate_and_client_is_own_uuid(self):
        self.assertEqual(CLIENT, '003-4123-' + INSTALLATION)
        self.assertEqual(p.make_client_id(INSTALLATION, p.R002_PROFILE), '003-4058-' + INSTALLATION)
        self.assertEqual(p.profile_for('connect2_r002').oem_id, 'A0058,G0058,G0079')
        with self.assertRaises(p.QvCloudError):
            p.make_client_id('borrowed-phone-token')
        with self.assertRaises(p.QvCloudError):
            p.profile_for('connect_v1')

    def test_login_xml_escapes_email_and_hashes_normal_password(self):
        digest = p.password_hash('Été<&>')
        self.assertEqual(digest, hashlib.sha256('Été<&>'.encode()).hexdigest())
        root = ET.fromstring(p.build_login_request(CLIENT, 'a<&>@example.invalid', digest, 7))
        self.assertEqual(root.findtext('header/version'), 'v1.13')
        self.assertEqual(root.findtext('header/client/oem'), p.CONNECT3_PROFILE.oem_id)
        self.assertEqual(root.findtext('content/account'), 'a<&>@example.invalid')
        self.assertEqual(root.findtext('content/auth-type'), '0')
        self.assertEqual(root.findtext('content/password'), digest)
        self.assertEqual(p.build_oauth_params(CLIENT, 'a@example.invalid', digest, 7)['pwd'], digest)

    def test_login_response_requires_server_account_and_session(self):
        account = p.parse_login_response(envelope('<account-id>PRIVATE_ACCOUNT</account-id>'))
        self.assertEqual(account.account_id, 'PRIVATE_ACCOUNT')
        self.assertNotIn('PRIVATE', repr(account))
        for payload in (envelope(), envelope('<account-id>x</account-id>', session=''),
                        b'<envelope><header/></envelope>'):
            with self.assertRaises(p.QvCloudError):
                p.parse_login_response(payload)
        with self.assertRaises(p.QvRegionRedirect) as raised:
            p.parse_login_response(envelope('<redirect-region-id>3</redirect-region-id>', result=100101000))
        self.assertEqual(raised.exception.region_id, 3)

    def test_only_authorized_channel_scope_and_ring_bit(self):
        owned, shared = p.parse_device_list(DEVICES)
        self.assertEqual(owned.channels, (0, 1))
        self.assertEqual(shared.channels, (2,))
        self.assertTrue(shared.shared)
        root = ET.fromstring(p.build_subscription_request('PRIVATE_SESSION', shared, 2, True))
        self.assertEqual(root.findtext('content/devid'), shared.uid)
        self.assertEqual(root.findtext('content/chno'), '2')
        self.assertEqual(root.findtext('content/ifshare'), '1')
        self.assertEqual(root.findtext('content/detail'), '262144')
        disabled = ET.fromstring(p.build_subscription_request('PRIVATE_SESSION', owned, 0, False))
        self.assertEqual(disabled.findtext('content/ifpush'), '0')
        self.assertEqual(disabled.findtext('content/detail'), '0')
        with self.assertRaises(p.QvCloudError):
            p.build_subscription_request('s', shared, 0, True)

    def test_distinct_alarm_client_payload_has_no_device_password(self):
        account = p.QvCloudAccount('PRIVATE_SESSION', 'PRIVATE_ACCOUNT')
        root = ET.fromstring(p.build_alarm_login(CLIENT, 'PRIVATE_FCM', account,
                                               'https://auth.qvcloud.net/'))
        self.assertEqual(root.findtext('content/client/id'), CLIENT)
        self.assertEqual(root.findtext('content/client/tokentype'), '1')
        self.assertEqual(root.findtext('content/account/authserver'), 'auth.qvcloud.net')
        self.assertIsNone(root.find('.//password'))
        self.assertIsNone(root.find('.//lt'))

    def test_malformed_and_oversized_provider_values_fail_closed(self):
        for payload in (b'<!DOCTYPE envelope [<!ENTITY e "private">]><envelope/>',
                        b'<envelope><header/><header/></envelope>', b'<x/>', b'PRIVATE_BAD'):
            with self.assertRaises(p.QvCloudError) as raised:
                p.parse_envelope(payload)
            self.assertNotIn('PRIVATE', str(raised.exception))
        with self.assertRaises(p.QvCloudError) as raised:
            p.parse_envelope(b'x' * (p.MAX_RESPONSE_BYTES + 1))
        self.assertEqual(raised.exception.code, 'response_too_large')
        for body in ('{}', '{"code":false}', '{"code":5,"msg":"PRIVATE"}'):
            with self.assertRaises(p.QvCloudError) as raised:
                p.validate_openapi_response(body)
            self.assertNotIn('PRIVATE', str(raised.exception))

    def test_discovered_services_cannot_redirect_credentials(self):
        for url in ('http://auth.qvcloud.net/', 'https://qvcloud.net.evil.invalid/',
                    'https://auth.qvcloud.net/?token=x', 'https://u:p@auth.qvcloud.net/',
                    'https://127.0.0.1/', 'https://auth.qvcloud.net:80/'):
            with self.assertRaises(p.QvCloudError):
                p.validate_service_url(url)
        self.assertEqual(p.validate_service_url('https://auth.qvcloud.net:443/'),
                         'https://auth.qvcloud.net/')
        self.assertEqual(p.validate_service_url('https://alarm.qvcloud.net:4443/', alarm=True),
                         'https://alarm.qvcloud.net:4443/')
        with self.assertRaises(p.QvCloudError):
            p.validate_service_url('https://auth.qvcloud.net:4443/')
        services = p.QvCloudServices('https://auth.qvcloud.net/',
            'https://alarm.qvcloud.net:4443/', 'https://oauth.qvcloud.net/',
            'https://openapi.qvcloud.net/')
        self.assertEqual(services.alarm_url, 'https://alarm.qvcloud.net:4443/')
