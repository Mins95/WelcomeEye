"""Synthetic QV account XML fixtures and scope boundaries, no live credentials."""
import hashlib
from dataclasses import replace
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

    def test_only_authorized_channel_scope_and_apk_call_family(self):
        owned, shared = p.parse_device_list(DEVICES)
        self.assertEqual(owned.channels, (0, 1))
        self.assertEqual(shared.channels, (2,))
        self.assertTrue(shared.shared)
        root = ET.fromstring(p.build_subscription_request('PRIVATE_SESSION', shared, 2, True))
        self.assertEqual(root.findtext('content/devid'), shared.uid)
        self.assertEqual(root.findtext('content/chno'), '2')
        self.assertEqual(root.findtext('content/ifshare'), '1')
        self.assertEqual(root.findtext('content/detail'), '53188875780096')
        self.assertEqual(p.CALL_EVENTS, (19, 20, 38, 39, 45, 46))
        disabled = ET.fromstring(p.build_subscription_request('PRIVATE_SESSION', owned, 0, False))
        self.assertEqual(disabled.findtext('content/ifpush'), '0')
        self.assertEqual(disabled.findtext('content/detail'), '0')
        with self.assertRaises(p.QvCloudError):
            p.build_subscription_request('s', shared, 0, True)

    def test_cloud_metadata_selects_sdk_scope_without_guessing_from_brand(self):
        def device_xml(kind='', model=''):
            return envelope('<device><id>PRIVATE_UID</id>'
                            f'<type>{kind}</type><model>{model}</model>'
                            '<channel-num>2</channel-num></device>')

        for kind, model, scope, reason in (
                ('IDS', 'UNKNOWN', 'device', 'sdk_vdp_type'),
                ('ODS', '', 'device', 'sdk_vdp_type'),
                ('BOX', '', 'device', 'sdk_vdp_type'),
                ('', 'IDS94E6SW', 'device', 'sdk_vdp_model'),
                ('UNDEF', 'ods94e6', 'device', 'sdk_vdp_model'),
                ('IPC', 'IDS94E6SW', 'channel', 'other_type'),
                ('UNKNOWN', 'IDS94E6SW', 'channel', 'other_type'),
                ('', 'IDS-IPC', 'channel', 'sdk_vsu_model'),
                ('', 'WelcomeEye Connect3', 'channel', 'unknown_model'),
                ('', 'V401.R001.A331.00.G0123.B015', 'channel', 'unknown_model'),
        ):
            with self.subTest(kind=kind, model=model):
                device, = p.parse_device_list(device_xml(kind, model))
                self.assertEqual(device.device_type, kind)
                self.assertEqual(p.subscription_scope(device), scope)
                self.assertEqual(p.subscription_scope_reason(device), reason)
                if kind:
                    self.assertNotIn(kind, repr(device))
        # New metadata is trailing and optional for existing positional callers.
        legacy = p.QvCloudDevice('PRIVATE_UID', 'Front', '', (0,), False, 0)
        self.assertEqual(legacy.device_type, '')
        self.assertEqual(p.subscription_scope(legacy), 'channel')
        for extra in ('<type>IDS</type><type>IPC</type>',
                      '<model>IDS</model><model>IPC</model>',
                      '<type><value>IDS</value></type>'):
            with self.assertRaises(p.QvCloudError):
                p.parse_device_list(envelope(f'<device><id>x</id>{extra}</device>'))

    def test_device_scope_omits_channel_and_never_widens_shared_grants(self):
        device = p.QvCloudDevice('PRIVATE_UID', 'Front', 'IDS94E6SW', (0, 1))
        expected = (
            b"<?xml version='1.0' encoding='utf-8'?>\n"
            b'<envelope><header><flag>tdkcloud</flag><command>client-update-dev</command>'
            b'<seq>0</seq><session>PRIVATE_SESSION</session></header><content>'
            b'<devid>PRIVATE_UID</devid><ifshare>0</ifshare><ifpush>1</ifpush>'
            b'<detail>53188875780096</detail></content></envelope>')
        self.assertEqual(p.build_subscription_request('PRIVATE_SESSION', device, None, True),
                         expected)
        for candidate in (replace(device, shared=True),
                          replace(device, model='UNKNOWN'),
                          replace(device, cloud_type=1), replace(device, channels=())):
            with self.subTest(candidate=candidate):
                with self.assertRaises(p.QvCloudError):
                    p.build_subscription_request('s', candidate, None, True)
        unknown = replace(device, model='UNKNOWN')
        disabled = ET.fromstring(p.build_subscription_request('s', unknown, None, False))
        self.assertIsNone(disabled.find('content/chno'))
        self.assertEqual(disabled.findtext('content/ifpush'), '0')
        self.assertEqual(disabled.findtext('content/detail'), '0')
        with self.assertRaises(p.QvCloudError):
            p.build_subscription_request('s', replace(unknown, shared=True), None, False)
        shared = replace(device, shared=True, channels=(1,))
        self.assertEqual(p.subscription_scope(shared), 'channel')
        self.assertEqual(p.subscription_scope_reason(shared), 'shared_channel_grant')
        self.assertEqual(ET.fromstring(p.build_subscription_request('s', shared, 1, True))
                         .findtext('content/chno'), '1')
        for channel in (0, None, True):
            with self.assertRaises(p.QvCloudError):
                p.build_subscription_request('s', shared, channel, True)

    def test_query_request_reads_only_authorized_scope_with_apk_fields(self):
        device = p.QvCloudDevice('PRIVATE_UID', 'Front', '', (0, 1))
        expected = (
            b"<?xml version='1.0' encoding='utf-8'?>\n"
            b'<envelope><header><flag>tdkcloud</flag><command>client-query-dev</command>'
            b'<seq>0</seq><session>PRIVATE_SESSION</session></header><content>'
            b'<devid>PRIVATE_UID</devid><ifshare>0</ifshare><ifdetail>1</ifdetail>'
            b'</content></envelope>')
        self.assertEqual(p.build_subscription_query('PRIVATE_SESSION', device, None), expected)
        selected = ET.fromstring(p.build_subscription_query('PRIVATE_SESSION', device, 1))
        self.assertEqual(selected.findtext('content/chno'), '1')
        self.assertIsNone(selected.find('content/ifpush'))
        self.assertIsNone(selected.find('content/detail'))
        for candidate, channel in ((replace(device, shared=True), None),
                                   (replace(device, cloud_type=2), 0),
                                   (replace(device, channels=()), None),
                                   (device, 99), (device, True)):
            with self.assertRaises(p.QvCloudError):
                p.build_subscription_query('s', candidate, channel)

    def test_subscription_readback_projects_effective_call_state(self):
        for content, enabled, events, present in (
                ('<ifpush>1</ifpush><detail>53188875780096</detail>',
                 True, p.CALL_EVENTS, True),
                ('<ifpush>1</ifpush><detail>786432</detail>', True, (19, 20), True),
                ('<ifpush>1</ifpush><detail>262144</detail>', True, (19,), True),
                ('<ifpush>1</ifpush><detail>262145</detail>', True, (19,), True),
                ('<ifpush>1</ifpush><detail>0</detail>', True, (), True),
                ('<ifpush>0</ifpush><detail>53188875780096</detail>', False, (), True),
                ('<ifpush>1</ifpush>', True, p.CALL_EVENTS, False),
                ('<ifpush>0</ifpush>', False, (), False),
                ('<ifpush>2</ifpush><detail>786432</detail>', True, (19, 20), True),
                ('<ifpush>-1</ifpush><detail>786432</detail>', True, (19, 20), True),
                ('<ifpush>2147483647</ifpush>', True, p.CALL_EVENTS, False),
                ('<ifpush>-2147483648</ifpush>', True, p.CALL_EVENTS, False),
                ('<ifpush> \n+1\t</ifpush><detail> 786432\n</detail>',
                 True, (19, 20), True),
                ('<ifpush>1</ifpush><detail>+786432</detail>', True, (19, 20), True),
                ('<ifpush>1</ifpush><detail>9223372036854775807</detail>',
                 True, p.CALL_EVENTS, True)):
            with self.subTest(content=content):
                state = p.parse_subscription_response(envelope(content))
                self.assertEqual(state.enabled, enabled)
                self.assertEqual(state.effective_call_events, events)
                self.assertEqual(state.effective_call_mask,
                                 sum(1 << (event - 1) for event in events))
                self.assertEqual(state.detail_present, present)
                self.assertNotIn('PRIVATE', repr(state))

    def test_readback_rejects_ack_duplicates_nested_state_and_invalid_numbers(self):
        invalid_content = (
            '', '<ifpush/>', '<ifpush>true</ifpush>', '<ifpush>1.0</ifpush>',
            '<ifpush>2147483648</ifpush>', '<ifpush>-2147483649</ifpush>',
            '<ifpush>1</ifpush><ifpush>0</ifpush>',
            '<ifpush><value>1</value></ifpush>',
            '<ifpush>1</ifpush><detail>1</detail><detail>2</detail>',
            '<ifpush>1</ifpush><device><ifpush>0</ifpush></device>',
            '<ifpush>1</ifpush><device><detail>0</detail></device>',
        )
        invalid_content += tuple('<ifpush>1</ifpush><detail>' + value + '</detail>'
                                 for value in ('', '-1', '1.0', '1e6', 'PRIVATE',
                                               '9223372036854775808', '1' * 20))
        for content in invalid_content:
            with self.subTest(content=content):
                with self.assertRaises(p.QvCloudError) as raised:
                    p.parse_subscription_response(envelope(content))
                self.assertEqual(raised.exception.code, 'invalid_response')
        valid = envelope('<ifpush>1</ifpush>')
        for payload in (
                valid.replace(b'<result>0</result>', b'<result>0</result><result>1</result>'),
                valid.replace(b'</envelope>', b'<content><ifpush>0</ifpush></content></envelope>'),
                valid.replace(b'</header>', b'<command>x</command><command>y</command></header>')):
            with self.assertRaises(p.QvCloudError):
                p.parse_subscription_response(payload)
        # The APK does not require the response command to echo the request.
        state = p.parse_subscription_response(valid.replace(
            b'</header>', b'<command>client-query-dev-result</command></header>'))
        self.assertTrue(state.enabled)
        with self.assertRaises(p.QvCloudError) as raised:
            p.parse_subscription_response(envelope('<ifpush>1</ifpush>', result=5))
        self.assertEqual(raised.exception.code, 'provider_rejected')

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
