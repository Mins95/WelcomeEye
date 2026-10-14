"""Explicit legacy TCP consent guards; independent synthetic native fixtures."""
import unittest
from unittest.mock import Mock

from load_integration import load
from test_connect3_alarm_legacy_tls import legacy_setup, native_login, USERNAME, PASSWORD, CUSTOMER

wire = load('connect3.alarm_protocol')
stream = load('connect3.alarm_stream')


class ExplicitLegacyTCPTests(unittest.TestCase):
    def request(self, setup=None, **overrides):
        options = dict(tcp_approved=True, legacy_login_approved=True,
            username=USERNAME, password=PASSWORD, client_type=3, customer=CUSTOMER)
        options.update(overrides)
        return wire.build_explicit_tcp_legacy_login_requests(
            legacy_setup() if setup is None else setup, **options)

    def test_native_pair_matches_independent_sdk_fixture_only_after_both_approvals(self):
        self.assertEqual(self.request(), (native_login(), native_login(0xA0)))

    def test_media_consent_alone_cannot_construct_login(self):
        for key in ('tcp_approved', 'legacy_login_approved'):
            for value in (False, None, 0, 1, 'approved'):
                with self.subTest(key=key, value=value):
                    with self.assertRaisesRegex(wire.AlarmProtocolError,
                            '^control_legacy_tcp_approval_required$'):
                        self.request(**{key: value}, username=object(), password=object())

    def test_consent_does_not_authorize_other_protocol_variants(self):
        for variant, result, encryption, sha in (
                (wire.SetupVariant.MODERN_V3, 0, 2, 1),
                (wire.SetupVariant.MODERN_V3, 0, 0, 0),
                (wire.SetupVariant.LEGACY_V1, 0, 0, 0),
                (wire.SetupVariant.LEGACY_V2, 7, 0, 0),
                (wire.SetupVariant.REJECTED, 51, 0, 0)):
            with self.subTest(variant=variant, result=result):
                with self.assertRaisesRegex(wire.AlarmProtocolError,
                        '^control_legacy_setup_required$'):
                    self.request(wire.ControlSetupResponse(variant, result, encryption, sha))

    def test_stream_requires_two_strict_approvals_before_any_io(self):
        for options in ({}, {'tcp_approved': True}, {'legacy_login_approved': True},
                {'tcp_approved': True, 'legacy_login_approved': 1},
                {'tcp_approved': 'approved', 'legacy_login_approved': True}):
            reader, writer = Mock(), Mock()
            with self.subTest(options=options):
                with self.assertRaisesRegex(wire.AlarmProtocolError,
                        '^control_legacy_tcp_approval_required$'):
                    stream.ExplicitTCPControlStream(reader, writer, {}, **options)
                reader.readexactly.assert_not_called()
                writer.write.assert_not_called()
