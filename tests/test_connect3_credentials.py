"""Synthetic QR inputs only; no photographed QR or working authCode."""
import json
import unittest

from load_integration import load

module = load('connect3.credentials')


class CredentialTests(unittest.TestCase):
    def test_both_apk_formats_extract_authcode_not_ap_password(self):
        for qr, kind in (
            ('PRIVATE_AP SYNTHETIC_UID PRIVATE_CODE IDS94E6SW', 'apk_space'),
            (json.dumps(dict(v=2, a='PRIVATE_AP', u='SYNTHETIC_UID', c='PRIVATE_CODE',
                             m='IDS94E6SW', d='metadata')), 'apk_json')):
            result = module.parse_installation_qr(qr)
            self.assertEqual(result.uid, 'SYNTHETIC_UID')
            self.assertEqual(result.auth_code, 'PRIVATE_CODE')
            self.assertEqual(result.format, kind)
            self.assertNotIn('PRIVATE', repr(result))
            self.assertNotIn('SYNTHETIC_UID', repr(result))

    def test_json_minimal_fields_and_ascii_space_handling(self):
        result = module.parse_installation_qr('{"u":"UID","c":"CODE","m":"IDS94E6SW"}')
        self.assertEqual(result.auth_code, 'CODE')
        result = module.parse_installation_qr('  AP  UID  CODE  IDS94E6SW  ')
        self.assertEqual(result.uid, 'UID')

    def test_urls_share_codes_other_models_extra_or_missing_fields_rejected(self):
        invalid = ['https://example.invalid/?auth=PRIVATE', 'AP UID CODE IDS9417TW',
                   'AP UID CODE IDS94E6SW EXTRA', 'AP UID CODE', 'AP\tUID CODE IDS94E6SW',
                   '{"u":"PRIVATE","c":"CODE","m":"IDS94E6SW","url":"x"}',
                   '{"u":"PRIVATE","c":"CODE"}', '{"u":"PRIVATE","m":"IDS94E6SW"}',
                   '{"u":"PRIVATE","u":"x","c":"CODE","m":"IDS94E6SW"}']
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(module.CredentialImportError) as caught:
                module.parse_installation_qr(value)
            self.assertNotIn('PRIVATE', str(caught.exception))

    def test_size_types_malformed_unicode_nested_and_control_limits(self):
        invalid = [None, 7, '', 'x' * 2049, '{', '[' * 1500, '\ud800',
                   'AP UID\x00 CODE IDS94E6SW', 'AP UID CO\nDE IDS94E6SW']
        baseline = dict(u='UID', c='CODE', m='IDS94E6SW')
        for field, value in (('u', ''), ('u', '\ud800'), ('u', 'x' * 65), ('c', 'x' * 257),
                             ('c', None), ('a', []), ('d', {}), ('v', True), ('v', -1), ('v', 256)):
            invalid.append(json.dumps({**baseline, field: value}))
        for value in invalid:
            with self.subTest(value=repr(value)[:100]), self.assertRaises(module.CredentialImportError):
                module.parse_installation_qr(value)
