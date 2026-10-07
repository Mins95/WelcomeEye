"""Offline protocol vectors and hostile-input checks; all identities synthetic."""
import base64
from datetime import datetime
import unittest

from load_integration import load

protocol = load('v1_cloud_protocol')
UID = 'SYNTHETIC_V1_UID'
TOKEN = 'SYNTHETIC_FCM_TOKEN'
CLIENT = '003-4000-SYNTHETIC_HA'


def wire_json(raw):
    return base64.b64encode(protocol._rc4(raw, protocol._RC4_KEY))


def notification(*, uid=UID, channel='0', event='14', stamp='20261007124530', name='[PRIVATE_NAME]'):
    return {'message_content': f'0|{uid}|{channel}|{event}|{stamp}|{name}'}


class V1CloudEncodingTests(unittest.TestCase):
    def test_rc4_known_vectors(self):
        self.assertEqual(protocol._rc4(b'Plaintext', b'Key').hex(), 'bbf316e8d940af0ad3')
        self.assertEqual(protocol._rc4(bytes(16), bytes([1, 2, 3, 4, 5])).hex(),
                         'b2396305f03dc027ccc3524a0a1118a8')

    def test_actual_protocol_roundtrip_and_padding(self):
        for size in range(8):
            payload = {'re': '1', 'text': 'Été <&=\'>\u2028\u2029' + 'x' * size}
            wire = protocol.encode_payload(payload)
            self.assertEqual(len(wire) % 4, 0)
            self.assertNotIn('\n', wire)
            self.assertEqual(protocol.decode_payload(wire), payload)
            self.assertEqual(protocol.decode_payload(wire.encode('ascii')), payload)
            self.assertEqual(protocol.decode_payload(' \r\n' + wire + '\t'), payload)

    def test_encoding_matches_gson_html_escaping(self):
        wire = protocol.encode_payload({'text': "<&='>"})
        plain = protocol._rc4(base64.b64decode(wire), protocol._RC4_KEY)
        self.assertEqual(plain, b'{"text":"\\u003c\\u0026\\u003d\\u0027\\u003e"}')

    def test_invalid_base64_and_oversized_body(self):
        for body in [None, 5, [], b'\xff', 'é', 'PRIVATE_TOKEN!', '', '===', 'YQ']:
            with self.subTest(body_type=type(body).__name__):
                with self.assertRaises(protocol.CloudProtocolError) as raised:
                    protocol.decode_payload(body)
                self.assertEqual(raised.exception.code, 'invalid_encoding')
        with self.assertRaisesRegex(protocol.CloudProtocolError, '^payload_too_large$'):
            protocol.decode_payload('A' * (protocol.MAX_ENCODED_BYTES + 1))

    def test_invalid_json_never_leaks_plaintext(self):
        for raw in [b'PRIVATE_TOKEN', b'\xff', b'{"re":"1","re":"0"}', b'{"x":NaN}', b'{"x":Infinity}']:
            with self.subTest(raw_length=len(raw)):
                with self.assertRaises(protocol.CloudProtocolError) as raised:
                    protocol.decode_payload(wire_json(raw))
                self.assertEqual(str(raised.exception), 'invalid_json')
                self.assertNotIn('PRIVATE', repr(raised.exception))
        with self.assertRaisesRegex(protocol.CloudProtocolError, '^invalid_response$'):
            protocol.decode_payload(wire_json(b'[]'))

    def test_structure_bounds_apply_to_encode_and_decode(self):
        inputs = [
            {'value': 'x' * (protocol.MAX_TOKEN_LENGTH + 1)},
            {'rows': [{}] * (protocol.MAX_STATUS_ROWS + 1)},
            {str(i): i for i in range(65)},
        ]
        deep = {}
        for _ in range(10):
            deep = {'x': deep}
        inputs.append(deep)
        for value in inputs:
            with self.assertRaisesRegex(protocol.CloudProtocolError, '^payload_too_large$'):
                protocol.encode_payload(value)
        with self.assertRaisesRegex(protocol.CloudProtocolError, '^payload_too_large$'):
            protocol.decode_payload(wire_json(b'{"x":' * 12 + b'0' + b'}' * 12))

    def test_invalid_outbound_types_and_cycles(self):
        circular = {}
        circular['self'] = circular
        for value in [None, [], {'x': b'PRIVATE'}, {'x': float('nan')}, {'x': 2**63},
                      {'x': '\ud800'}, {1: 'x'}, circular]:
            with self.assertRaises(protocol.CloudProtocolError):
                protocol.encode_payload(value)

    def test_error_constructor_cannot_forward_private_text(self):
        error = protocol.CloudProtocolError('PRIVATE_TOKEN_AND_PROVIDER_BODY')
        self.assertEqual(error.code, 'invalid_response')
        self.assertEqual(error.args, ('invalid_response',))


class V1CloudSubscriptionTests(unittest.TestCase):
    def test_registration_values_and_types_match_apk(self):
        value = protocol.build_subscription_request(CLIENT, TOKEN, UID, True)
        self.assertEqual(value['account'], 'N_philips/' + CLIENT)
        self.assertEqual(value['phone_imei'], CLIENT)
        self.assertEqual(value['act_type'], '3')
        self.assertEqual((value['phone_lang'], value['phone_type']), ('2', '2'))
        self.assertEqual(value['app_token_list'], {'fcm': {
            'app_key': 'tiandi.json', 'app_token': TOKEN, 'push_platform': '4'}})
        self.assertEqual(value['dev_list'], [{
            'alarm_type': '14,26', 'channel_no': -1, 'channel_vir_no': -1,
            'company_id': 'LT4a7a46c756098', 'gid': UID, 'gid_name': 'Home Assistant',
            'push_mode': 0, 'switch_state': '1'}])
        self.assertIs(type(value['push_mode']), int)
        self.assertEqual(value['push_mode'], 1)
        self.assertNotIn('password', str(value).lower())
        self.assertEqual(protocol.decode_payload(protocol.encode_payload(value)), value)

    def test_disable_only_changes_mask_and_switch_on_own_registration(self):
        enabled = protocol.build_subscription_request(CLIENT, TOKEN, UID, True)
        disabled = protocol.build_subscription_request(CLIENT, TOKEN, UID, False)
        enabled['dev_list'][0].update(alarm_type='', switch_state='0')
        self.assertEqual(enabled, disabled)

    def test_token_rotation_and_other_installation_do_not_share_identity(self):
        first = protocol.build_subscription_request(CLIENT, TOKEN, UID, True)
        rotated = protocol.build_subscription_request(CLIENT, 'SYNTHETIC_ROTATED', UID, True)
        phone = protocol.build_subscription_request('003-4000-SYNTHETIC_PHONE', 'PHONE_TOKEN', UID, True)
        self.assertEqual(first['account'], rotated['account'])
        self.assertNotEqual(first['account'], phone['account'])
        self.assertEqual(protocol.make_client_id('SYNTHETIC_HA'), CLIENT)

    def test_invalid_registration_fields_are_rejected_without_values(self):
        invalid = [(None, TOKEN, UID, True), (CLIENT, '', UID, True),
                   (CLIENT, 'PRIVATE\nTOKEN', UID, True), (CLIENT, 'é', UID, True),
                   (CLIENT, 'x' * (protocol.MAX_TOKEN_LENGTH + 1), UID, True),
                   (CLIENT, TOKEN, 'WRONG|UID', True), (CLIENT, TOKEN, UID, 1),
                   ('x' * 161, TOKEN, UID, True), (CLIENT, TOKEN, '', True)]
        for args in invalid:
            with self.assertRaisesRegex(protocol.CloudProtocolError, '^invalid_input$'):
                protocol.build_subscription_request(*args)

    def test_explicit_provider_selection_has_no_fallback(self):
        self.assertEqual(protocol.provider_urls(), (
            'https://tdpush.push2u.com/pda_more_api.php', 'https://tdpush.push2u.com/pushCheck.php'))
        self.assertEqual(protocol.provider_urls(False), (
            'https://lbs.push2u.com/pda_more_api.php', 'https://lbs.push2u.com/pushCheck.php'))
        with self.assertRaises(protocol.CloudProtocolError):
            protocol.provider_urls('false')

    def test_check_request_and_own_token_criterion(self):
        self.assertEqual(protocol.build_check_request(CLIENT, UID), {
            'account': 'N_philips/' + CLIENT, 'action_flag': 2, 'gid': UID})
        data = {'re': '1', 'data': [{'account': 'SYNTHETIC_PHONE', 'app_token': 'PHONE_TOKEN'},
                                  {'account': 'SYNTHETIC_HA', 'app_token': TOKEN}]}
        self.assertIs(protocol.check_subscription_response(data, TOKEN), True)
        self.assertIs(protocol.check_subscription_response(data, 'UNREGISTERED_TOKEN'), False)
        for data in [{'re': '1'}, {'re': '1', 'data': None}, {'re': '1', 'data': []},
                     {'re': '1', 'data': [{'app_token': None}]}]:
            self.assertIs(protocol.check_subscription_response(data, TOKEN), False)

    def test_rejection_is_distinct_from_disabled_and_no_code_guessing(self):
        for result in ['0', '401', '1024', '', 'PRIVATE_PROVIDER_BODY', 2]:
            with self.assertRaisesRegex(protocol.CloudProtocolError, '^provider_rejected$'):
                protocol.check_subscription_response({'re': result}, TOKEN)
        for result in ['1', 1]:
            self.assertIsNone(protocol.validate_subscription_response({'re': result}))

    def test_malformed_success_response_is_not_reported_disabled(self):
        for value in [[], {}, {'re': True}, {'re': None}, {'re': '1', 'data': {}},
                      {'re': '1', 'data': [None]}, {'re': '1', 'data': [{'app_token': 12}]}]:
            with self.assertRaises(protocol.CloudProtocolError):
                protocol.check_subscription_response(value, TOKEN)


class V1CloudNotificationTests(unittest.TestCase):
    def test_own_call_uses_apk_channel_and_naive_wall_time(self):
        event = protocol.parse_ring_notification(notification(), UID)
        self.assertEqual(event.channel, 1)
        self.assertEqual(event.occurred_at, datetime(2026, 10, 7, 12, 45, 30))
        self.assertIsNone(event.occurred_at.tzinfo)
        self.assertEqual(event.timestamp, '20261007124530')
        self.assertEqual(len(event.dedup_key), 64)
        self.assertNotIn(UID, repr(event))
        self.assertNotIn('PRIVATE_NAME', repr(event))
        self.assertNotIn('2026', repr(event))

    def test_non_ring_events_uid_case_and_other_families_are_ignored(self):
        values = [notification(event=value) for value in ['26', '2', '19', '7', '014', 'bogus']]
        values += [notification(uid='OTHER_UID'), notification(uid=UID.lower()), {},
                   dict(notification(), CameraId='SYNTHETIC_OTHER_FAMILY'),
                   dict(notification(), SrcAccountId='SYNTHETIC_SHARE')]
        for value in values:
            self.assertIsNone(protocol.parse_ring_notification(value, UID))

    def test_invalid_own_call_metadata_is_rejected(self):
        values = [notification(channel=value) for value in ['-1', '+1', '256', '1.0', '', '１', '10000']]
        values += [notification(stamp=value) for value in [
            '20260230124530', '20261007244530', '20261301124530',
            '20261007124560', '2026100712453', '２０２６1007124530', '00001007124530']]
        values += [{'message_content': value} for value in [None, b'PRIVATE', '', 'a|b',
                   'x' * (protocol.MAX_NOTIFICATION_LENGTH + 1),
                   '0|' + UID + '|0|14|20261007124530|PRIVATE\nNAME']]
        for value in values:
            with self.assertRaisesRegex(protocol.CloudProtocolError, '^invalid_notification$'):
                protocol.parse_ring_notification(value, UID)

    def test_unknown_uid_rejected_before_parsing_untrusted_date(self):
        self.assertIsNone(protocol.parse_ring_notification(notification(uid='OTHER_UID', stamp='bad'), UID))

    def test_display_text_optional_and_not_part_of_dedup(self):
        first = protocol.parse_ring_notification(notification(), UID)
        renamed = protocol.parse_ring_notification(notification(name='[OTHER_PRIVATE_NAME]|unused'), UID)
        absent = protocol.parse_ring_notification({'message_content': f'0|{UID}|0|14|20261007124530'}, UID)
        self.assertEqual(first, renamed)
        self.assertEqual(first, absent)

    def test_distinct_identity_channel_and_time_have_distinct_dedup_keys(self):
        events = [protocol.parse_ring_notification(notification(), UID),
                  protocol.parse_ring_notification(notification(channel='1'), UID),
                  protocol.parse_ring_notification(notification(stamp='20261007124531'), UID),
                  protocol.parse_ring_notification(notification(uid='OTHER_UID'), 'OTHER_UID')]
        self.assertEqual(len({event.dedup_key for event in events}), 4)

    def test_channel_bounds_and_leap_day(self):
        event = protocol.parse_ring_notification(notification(channel='255', stamp='20240229123456'), UID)
        self.assertEqual(event.channel, 256)
        self.assertEqual(event.timestamp, '20240229123456')

    def test_invalid_container_and_expected_uid(self):
        for value in [None, [], 'PRIVATE', {str(i): i for i in range(65)}]:
            with self.assertRaisesRegex(protocol.CloudProtocolError, '^invalid_notification$'):
                protocol.parse_ring_notification(value, UID)
        with self.assertRaisesRegex(protocol.CloudProtocolError, '^invalid_input$'):
            protocol.parse_ring_notification(notification(), '')


if __name__ == '__main__':
    unittest.main()
