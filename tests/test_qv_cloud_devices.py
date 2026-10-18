"""Account device rows from the APK schema; never merge authorization grants."""
from itertools import permutations
import unittest

from load_integration import load


p = load('qv_cloud_protocol')


def device(uid='SYNTHETIC_DEVICE', *, shared=0, channels=None, name='Front',
           cloud_type=0):
    authorization = ''
    if channels is not None:
        authorization = ('<auth-chans><count>' + str(len(channels)) + '</count>'
                         + ''.join(f'<item><channel>{ch}</channel></item>'
                                   for ch in channels) + '</auth-chans>')
    return (f'<device><id>{uid}</id><name>{name}</name><model>synthetic</model>'
            f'<channel-num>4</channel-num><from-share>{shared}</from-share>'
            f'<is-hs-device>{cloud_type}</is-hs-device>{authorization}</device>')


def parse(*rows):
    payload = ('<envelope><header><result>0</result></header><content>'
               f'<count>{len(rows)}</count>' + ''.join(rows)
               + '</content></envelope>').encode()
    return p.parse_device_list(payload)


class QvDeviceListTests(unittest.TestCase):
    def test_owned_row_takes_precedence_over_shared_copy_in_either_order(self):
        owner = device(name='Owned entry', channels=(0, 1))
        shared = device(shared=1, name='Shared entry', channels=(2,))
        for rows in ((owner, shared), (shared, owner)):
            with self.subTest(rows=rows):
                actual, = parse(*rows)
                self.assertFalse(actual.shared)
                self.assertEqual(actual.name, 'Owned entry')
                self.assertEqual(actual.channels, (0, 1))
                with self.assertRaises(p.QvCloudError):
                    p.build_subscription_request('SYNTHETIC_SESSION', actual, 2, True)

    def test_positive_from_share_values_keep_shared_authorization(self):
        for value in (1, 2, 3, 2147483647):
            with self.subTest(value=value):
                actual, = parse(device(shared=value, channels=(2,)))
                self.assertTrue(actual.shared)
                self.assertEqual(actual.channels, (2,))
                with self.assertRaises(p.QvCloudError):
                    p.build_subscription_request('SYNTHETIC_SESSION', actual, 0, True)
        for value in (-1, 2147483648):
            with self.subTest(value=value), self.assertRaises(p.QvCloudError):
                parse(device(shared=value))

    def test_owned_precedence_ignores_all_shared_copies_in_every_order(self):
        shared_first = device(shared=1, channels=(2,), name='First share')
        shared_second = device(shared=2, channels=(3,), name='Second share')
        for owner_channels in ((0, 1), ()):
            owner = device(channels=owner_channels, name='Owned entry')
            for rows in permutations((owner, shared_first, shared_second)):
                with self.subTest(owner_channels=owner_channels, rows=rows):
                    result = parse(*rows)
                    if not owner_channels:
                        self.assertEqual(result, ())
                        continue
                    actual, = result
                    self.assertFalse(actual.shared)
                    self.assertEqual(actual.name, 'Owned entry')
                    self.assertEqual(actual.channels, owner_channels)
                    for channel in (2, 3):
                        with self.assertRaises(p.QvCloudError):
                            p.build_subscription_request('SYNTHETIC_SESSION', actual,
                                                         channel, True)

    def test_empty_authorization_does_not_hide_other_devices_or_grant_channels(self):
        actual, = parse(device('SYNTHETIC_OWNER'),
                        device('SYNTHETIC_RESTRICTED', shared=1, channels=()))
        self.assertEqual(actual.uid, 'SYNTHETIC_OWNER')
        self.assertEqual(actual.channels, (0, 1, 2, 3))
        self.assertEqual(parse(device(channels=())), ())
        self.assertEqual(parse(device(shared=1, channels=())), ())

    def test_empty_owned_authorization_never_falls_back_to_shared_copy(self):
        owner = device(channels=())
        shared = device(shared=1, channels=(0, 1))
        self.assertEqual(parse(owner, shared), ())
        self.assertEqual(parse(shared, owner), ())

    def test_identical_rows_are_deduplicated(self):
        for shared in (0, 1):
            row = device(shared=shared, channels=(0,))
            self.assertEqual(len(parse(row, row)), 1)

    def test_conflicting_duplicates_are_not_merged_or_subscribed(self):
        cases = (
            (device(channels=(0,)), device(channels=(1,))),
            (device(shared=1, channels=(0,)), device(shared=2, channels=(1,))),
            (device(), device(shared=1, cloud_type=1)),
            (device(), device(name='Different owner record')),
        )
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(p.QvCloudError) as raised:
                parse(*rows)
            self.assertNotIn('SYNTHETIC', str(raised.exception))

    def test_malformed_channel_grants_still_fail_closed(self):
        for channels in ((0, 0), (-1,), (256,)):
            with self.subTest(channels=channels), self.assertRaises(p.QvCloudError):
                parse(device(shared=1, channels=channels))
