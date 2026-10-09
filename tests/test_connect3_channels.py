"""Synthetic channel observations are private and bound to one media profile."""
from copy import deepcopy
import unittest

from load_integration import load

channels = load('connect3.channels')


class ChannelPolicyTests(unittest.TestCase):
    def setUp(self):
        self.data = {'host': '192.0.2.1', 'cgi_port': 443, 'media_port': 8443,
            'media_transport': 'tls', 'certificate_sha256': 'a' * 64,
            'media_certificate_sha256': 'b' * 64}
        self.data['observed_media_channels'] = {
            'binding': channels.media_profile_binding(self.data), 'channels': [1, 2]}

    def test_only_explicit_option_or_matching_observed_channel_enables_second_camera(self):
        self.assertTrue(channels.channel2_enabled(self.data))
        for value in (False, None, 1, 'true'):
            self.assertFalse(channels.channel2_enabled({**self.data, 'second_channel_enabled': value}))
        self.assertTrue(channels.channel2_enabled({'second_channel_enabled': True}))
        self.assertFalse(channels.channel2_enabled({'channels': 2, 'decoded_records': 1,
            'records': [{'channels': 2}]}))
        data = deepcopy(self.data)
        data['observed_media_channels']['channels'] = [1]
        self.assertFalse(channels.channel2_enabled(data))

    def test_endpoint_or_certificate_change_invalidates_old_observation(self):
        for field, value in (('host', '192.0.2.2'), ('cgi_port', 9443), ('media_port', 8444),
                ('media_transport', 'connect3_tcp'), ('certificate_sha256', 'c' * 64),
                ('media_certificate_sha256', 'd' * 64)):
            with self.subTest(field=field):
                self.assertFalse(channels.valid_observed_channels({**self.data, field: value}))

    def test_binding_excludes_credentials_identity_and_nonconnection_options(self):
        changed = {**self.data, 'auth_code': 'NEVER_STORED', 'opening_code': 'NEVER_STORED',
            'credential_device_uid': 'NEVER_STORED', 'experimental_outputs': True,
            'second_channel_enabled': True}
        self.assertEqual(channels.media_profile_binding(changed), channels.media_profile_binding(self.data))
        tcp = {**self.data, 'media_transport': 'connect3_tcp'}
        self.assertEqual(channels.media_profile_binding(tcp),
            channels.media_profile_binding({**tcp, 'media_port': 12345}))

    def test_malformed_records_never_enable_channel(self):
        records = [None, [], {}, {'binding': None, 'channels': [2]},
            {'binding': 'é' * 64, 'channels': [2]},
            {**self.data['observed_media_channels'], 'unexpected': True}]
        records.extend({'binding': self.data['observed_media_channels']['binding'], 'channels': values}
            for values in ([], [True], [0], [3], [1, 2, 3], '2'))
        for record in records:
            self.assertFalse(channels.valid_observed_channels({**self.data, 'observed_media_channels': record}))

    def test_invalid_endpoint_profile_cannot_be_observed(self):
        for change in ({'host': 'not-an-ip'}, {'cgi_port': True}, {'media_port': 0},
                {'media_transport': 'udp'}, {'certificate_sha256': b'bad'}):
            self.assertIsNone(channels.media_profile_binding({**self.data, **change}))


if __name__ == '__main__':
    unittest.main()
