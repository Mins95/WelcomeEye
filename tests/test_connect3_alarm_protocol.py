"""Independent synthetic native-control fixtures; no device or network calls."""
from dataclasses import FrozenInstanceError, asdict
from hashlib import sha256
import struct
import unittest

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from load_integration import load

p = load("connect3.alarm_protocol")
KEY = "SYNTHETIC_CONTROL_KEY_32_CHARACTERS_ONLY"


def aes(data, *, decrypt=False, key=KEY):
    context = Cipher(algorithms.AES(key.encode("ascii")[:32]), modes.CBC(b"0" * 16))
    operation = context.decryptor() if decrypt else context.encryptor()
    return operation.update(data) + operation.finalize()


def protected_packet(payload, *, timestamp=123, digest_valid=True):
    # Independent CTcpSocket 0x7f5498 fixture, never the production encoder.
    body_length = ((len(payload) + 32 + 15) // 16) * 16
    header = bytearray(32)
    struct.pack_into("<QII", header, 0, timestamp, body_length, len(payload))
    digest = sha256(bytes(header) + payload).digest() if digest_valid else bytes(32)
    body = payload + digest + bytes(body_length - len(payload) - 32)
    return aes(bytes(header)) + aes(body)


def setup_response(command=0xA9, result=0, encryption=2, sha=1):
    # Independent native offsets from WQ_LIB::setup 0x7c1074.
    data = bytearray(32)
    data[0], data[9], data[10], data[11] = command, result, encryption, sha
    return bytes(data)


class ControlSetupTests(unittest.TestCase):
    def test_exact_native_control_request_differs_from_live_selector(self):
        expected = bytes.fromhex(
            "a9 00 00 00 00 00 00 00 00 64 00 00 00 00 00 00 "
            "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00"
        )
        self.assertEqual(p.build_control_setup_request(), expected)
        self.assertNotEqual(p.build_control_setup_request(), b"\xa9" + bytes(31))

    def test_modern_supported_crypto_combinations(self):
        for encryption in (0, 1, 2):
            for sha in (0, 1):
                with self.subTest(encryption=encryption, sha=sha):
                    response = p.parse_control_setup_response(
                        setup_response(encryption=encryption, sha=sha))
                    self.assertEqual(response.variant, "modern_v3")
                    self.assertEqual(response.protocol_version, 3)
                    self.assertEqual(response.result, 0)
                    self.assertEqual(response.encryption_mode, encryption)
                    self.assertEqual(response.sha_mode, sha)
                    self.assertEqual(response.crypto_mode_source, "modern_advertised")
                    self.assertEqual(response.response_selector, 0)
                    self.assertEqual(response.supports_protected_login,
                                     encryption == 2 and sha == 1)

    def test_legacy_is_identified_without_crypto_or_login_acceptance(self):
        for command, result, variant, version in (
            (0xBE, 0, "legacy_v1", 1), (0xBE, 255, "legacy_v1", 1),
            (0xA9, 1, "legacy_v2", 2), (0xA9, 50, "legacy_v2", 2),
        ):
            with self.subTest(command=command, result=result):
                response = p.parse_control_setup_response(
                    setup_response(command, result, 255, 255))
                self.assertEqual(response.variant, variant)
                self.assertEqual(response.protocol_version, version)
                self.assertEqual((response.encryption_mode, response.sha_mode), (0, 0))
                self.assertEqual(response.crypto_mode_source, "legacy_sdk_defaults")
                self.assertEqual(response.response_selector, result)
                self.assertFalse(response.supports_protected_login)

    def test_legacy_v2_does_not_treat_other_bytes_as_advertised_crypto(self):
        for selector in (1, 50):
            for encryption, sha in ((2, 1), (255, 255)):
                response = p.parse_control_setup_response(
                    setup_response(result=selector, encryption=encryption, sha=sha))
                self.assertEqual(response.variant, "legacy_v2")
                self.assertEqual(response.protocol_version, 2)
                self.assertEqual((response.encryption_mode, response.sha_mode), (0, 0))
                self.assertEqual(response.crypto_mode_source, "legacy_sdk_defaults")
                self.assertFalse(response.supports_protected_login)

    def test_result_codes_not_misclassified_as_legacy(self):
        for result in (51, 100, 255):
            response = p.parse_control_setup_response(setup_response(result=result))
            self.assertEqual(response.variant, "rejected")
            self.assertEqual(response.result, result)
            self.assertIsNone(response.protocol_version)
            self.assertEqual(response.crypto_mode_source, "not_available")
            self.assertFalse(response.supports_protected_login)

    def test_strict_size_and_input_type(self):
        for data in (None, "private", bytearray(32), b"", bytes(31), bytes(33), bytes(4096)):
            with self.subTest(type=type(data), size=len(data) if data is not None else 0):
                with self.assertRaisesRegex(p.AlarmProtocolError, "^control_setup_size$"):
                    p.parse_control_setup_response(data)

    def test_unexpected_commands_never_follow_native_fallthrough(self):
        for command in (0, 0x0A, 0x68, 0x69, 0xFE, 0xFF):
            with self.subTest(command=command):
                with self.assertRaisesRegex(p.AlarmProtocolError, "^control_setup_command$"):
                    p.parse_control_setup_response(setup_response(command=command))

    def test_invalid_modern_crypto_fields(self):
        for encryption, sha in ((3, 0), (255, 1), (2, 2), (0, 255)):
            with self.subTest(encryption=encryption, sha=sha):
                with self.assertRaisesRegex(p.AlarmProtocolError, "^control_setup_crypto_mode$"):
                    p.parse_control_setup_response(setup_response(encryption=encryption, sha=sha))

    def test_metadata_never_retains_unknown_response_bytes(self):
        data = bytearray(setup_response())
        data[12:] = b"PRIVATE_RESPONSE_KEY"
        response = p.parse_control_setup_response(bytes(data))
        self.assertNotIn("PRIVATE", repr(response))
        self.assertEqual(set(asdict(response)), {
            "variant", "result", "encryption_mode", "sha_mode", "response_selector"})
        with self.assertRaises(FrozenInstanceError):
            response.result = 1


class ControlEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.material = p.ControlCipherMaterial(KEY)

    def test_exact_container_and_separate_iv_resets(self):
        payload = b"\x69" + bytes(31) + b"SYNTHETIC_METADATA"
        expected = protected_packet(payload, timestamp=987654321)
        encoded = p.encode_control_packet(payload, self.material, timestamp_seconds=987654321)
        self.assertEqual(encoded, expected)
        raw_header = aes(encoded[:32], decrypt=True)
        raw_body = aes(encoded[32:], decrypt=True)
        self.assertEqual(struct.unpack_from("<Q", raw_header)[0], 987654321)
        self.assertEqual(struct.unpack_from("<I", raw_header, 12)[0], len(payload))
        self.assertEqual(raw_body[:len(payload)], payload)
        self.assertNotEqual(aes(encoded, decrypt=True)[32:], raw_body)

    def test_independent_fixture_decodes_and_omits_private_header_from_repr(self):
        payload = b"\x69" + bytes(31) + b"SYNTHETIC_METADATA"
        packet = protected_packet(payload)
        header = p.decode_control_header(packet[:32], self.material)
        self.assertEqual(header.body_length, len(packet) - 32)
        self.assertEqual(header.plaintext_length, len(payload))
        self.assertNotIn("plaintext=", repr(header))
        self.assertEqual(p.decode_control_body(header, packet[32:], self.material), payload)

    def test_digest_required_before_payload_is_returned(self):
        packet = protected_packet(bytes(32), digest_valid=False)
        header = p.decode_control_header(packet[:32], self.material)
        with self.assertRaisesRegex(p.AlarmProtocolError, "^control_digest_mismatch$"):
            p.decode_control_body(header, packet[32:], self.material)

    def test_digest_covers_outer_header(self):
        packet = protected_packet(bytes(32))
        raw = bytearray(aes(packet[:32], decrypt=True))
        raw[0] ^= 1
        header = p.decode_control_header(aes(bytes(raw)), self.material)
        with self.assertRaisesRegex(p.AlarmProtocolError, "^control_digest_mismatch$"):
            p.decode_control_body(header, packet[32:], self.material)

    def test_length_checked_before_allocating_or_reading_body(self):
        for body_length, original_length in ((0xFFFFFFFF, 32), (64, 0xFFFFFFFF),
                                             (16, 32), (65, 32), (64, 0)):
            raw = struct.pack("<QII", 0, body_length, original_length) + bytes(16)
            with self.subTest(body=body_length, original=original_length):
                with self.assertRaises(p.AlarmProtocolError):
                    p.decode_control_header(aes(raw), self.material)

    def test_truncation_and_nonzero_padding_fail_closed(self):
        packet = protected_packet(bytes(33))
        header = p.decode_control_header(packet[:32], self.material)
        with self.assertRaisesRegex(p.AlarmProtocolError, "^control_body_size$"):
            p.decode_control_body(header, packet[32:-1], self.material)
        body = bytearray(aes(packet[32:], decrypt=True))
        body[-1] = 1
        with self.assertRaisesRegex(p.AlarmProtocolError, "^control_padding_invalid$"):
            p.decode_control_body(header, aes(bytes(body)), self.material)

    def test_key_validation_and_repr_redaction(self):
        self.assertNotIn(KEY, repr(self.material))
        for key in (None, b"A" * 32, "A" * 31, "A" * 257, "A" * 31 + "\0", "A" * 31 + "é"):
            with self.assertRaisesRegex(p.AlarmProtocolError, "^control_key_invalid$"):
                p.ControlCipherMaterial(key)

    def test_encoder_rejects_oversize_payload_and_timestamp(self):
        for payload in (b"", bytes(p.MAX_CONTROL_BODY), "private"):
            with self.assertRaisesRegex(p.AlarmProtocolError, "^control_payload_size$"):
                p.encode_control_packet(payload, self.material, timestamp_seconds=1)
        for timestamp in (-1, True, 1.1, 1 << 64):
            with self.assertRaisesRegex(p.AlarmProtocolError, "^control_timestamp_invalid$"):
                p.encode_control_packet(bytes(32), self.material, timestamp_seconds=timestamp)

    def test_record_may_contain_only_a_native_packet_body_fragment(self):
        # CTcpSocket WriteData records and native packets are distinct layers.
        for payload in (b"x", b"body_fragment"):
            packet = protected_packet(payload)
            header = p.decode_control_header(packet[:32], self.material)
            self.assertEqual(p.decode_control_body(header, packet[32:], self.material), payload)


class NativeAlarmTests(unittest.TestCase):
    @staticmethod
    def notification(subtype, *, special=0, body=b"PRIVATE_ALARM_BODY"):
        header = bytearray(32)
        header[0], header[12], header[13] = 0x69, subtype, special
        struct.pack_into("<I", header, 4, len(body))
        return bytes(header) + body

    def test_69_callback_namespace_and_payload_redaction(self):
        response = p.parse_alarm_notification(self.notification(7))
        self.assertEqual(asdict(response), {"subtype": 7, "callback_type": 107, "body_length": 18})
        self.assertNotIn("PRIVATE", repr(response))

    def test_all_native_accepted_and_rejected_subtypes(self):
        accepted = set(range(1, 9)) | set(range(10, 14)) | set(range(157, 191)) | {192, 251, 252, 253, 254, 255}
        for subtype in range(256):
            with self.subTest(subtype=subtype):
                if subtype in accepted:
                    self.assertEqual(p.parse_alarm_notification(self.notification(subtype)).subtype, subtype)
                else:
                    with self.assertRaisesRegex(p.AlarmProtocolError, "^alarm_subtype_unsupported$"):
                        p.parse_alarm_notification(self.notification(subtype))

    def test_subtype_five_special_case_does_not_apply_to_other_types(self):
        self.assertEqual(p.parse_alarm_notification(self.notification(5, special=1)).callback_type, 354)
        self.assertEqual(p.parse_alarm_notification(self.notification(5, special=0)).callback_type, 105)
        self.assertEqual(p.parse_alarm_notification(self.notification(6, special=1)).callback_type, 106)

    def test_native_length_and_command_are_verified(self):
        packet = self.notification(7)
        for data in (packet[:-1], packet + b"x", b"", bytes(31)):
            with self.assertRaisesRegex(p.AlarmProtocolError, "^native_packet_size$"):
                p.parse_alarm_notification(data)
        packet = bytes((0xFE,)) + packet[1:]
        with self.assertRaisesRegex(p.AlarmProtocolError, "^native_packet_command$"):
            p.parse_alarm_notification(packet)
        oversized = bytearray(32)
        struct.pack_into("<I", oversized, 4, 0xFFFFFFFF)
        with self.assertRaisesRegex(p.AlarmProtocolError, "^native_body_size$"):
            p.parse_native_packet_header(bytes(oversized))

    def test_exact_native_subscription_sequence_and_opaque_word(self):
        packets = p.build_alarm_subscription_requests(device_information_word=0x10203040)
        expected_subtypes = list(range(1, 14)) + [157, 159, 160, 161] + list(range(174, 191)) + [192]
        self.assertEqual(len(packets), 35)
        self.assertEqual([packet[12] for packet in packets], expected_subtypes)
        for packet, subtype in zip(packets, expected_subtypes):
            expected = bytearray(32)
            expected[0], expected[8], expected[12] = 0x68, 2, subtype
            expected[28:32] = b"\x40\x30\x20\x10"
            self.assertEqual(packet, bytes(expected))

    def test_information_word_requires_bounded_integer(self):
        for value in (-1, True, 1 << 32, "private"):
            with self.assertRaisesRegex(p.AlarmProtocolError, "^alarm_information_invalid$"):
                p.build_alarm_subscription_requests(device_information_word=value)

    def test_native_keepalive_is_only_a1_in_zeroed_header(self):
        self.assertEqual(p.build_control_keepalive_request(), b"\xa1" + bytes(31))


class ControlLoginResponseTests(unittest.TestCase):
    def test_b0_accepted_and_rejected_metadata(self):
        packet = bytearray(32)
        packet[0], packet[9] = 0xB0, 255
        self.assertEqual(asdict(p.parse_control_login_response(bytes(packet))), {"accepted": True, "result": 0})
        packet[8], packet[9] = 1, 9
        self.assertEqual(asdict(p.parse_control_login_response(bytes(packet))), {"accepted": False, "result": 9})
        packet[8] = 255
        self.assertFalse(p.parse_control_login_response(bytes(packet)).accepted)

    def test_alarm_and_setup_never_count_as_login_acceptance(self):
        for command in (0x0A, 0xA0, 0x69, 0xA9, 0xBE, 0):
            with self.subTest(command=command):
                with self.assertRaisesRegex(p.AlarmProtocolError, "^native_packet_command$"):
                    p.parse_control_login_response(bytes((command,)) + bytes(31))

    def test_b0_bounds_and_private_body_not_retained(self):
        header = bytearray(32)
        header[0] = 0xB0
        body = b"PRIVATE_ACCOUNT_MODEL_DATA"
        struct.pack_into("<I", header, 4, len(body))
        response = p.parse_control_login_response(bytes(header) + body)
        self.assertNotIn("PRIVATE", repr(response))
        with self.assertRaisesRegex(p.AlarmProtocolError, "^native_packet_size$"):
            p.parse_control_login_response(bytes(header) + body[:-1])


class ControlLoginRequestTests(unittest.TestCase):
    def setUp(self):
        self.material = p.ControlCipherMaterial(KEY)
        self.setup = p.parse_control_setup_response(setup_response())

    def request(self, **overrides):
        kwargs = dict(username="SYNTHETIC_USER", password="SYNTHETIC_PASSWORD",
                      client_type=0, customer="C0000", timestamp_seconds=456)
        kwargs.update(overrides)
        return p.build_control_login_requests(self.material, self.setup, **kwargs)

    def test_exact_native_0a_then_a0_each_protected(self):
        parameters = b"SYNTHETIC_USER&&SYNTHETIC_PASSWORD"
        expected = bytearray(32)
        expected[0:4] = bytes((0x0A, 0, 1, 0x50))
        expected[4:8] = struct.pack("<I", len(parameters))
        expected[8], expected[24], expected[25] = 255, 4, 1
        expected[9:14] = b"C0000"
        expected[30:32] = b"\xa1\xaa"
        packets = self.request()
        self.assertEqual(len(packets), 2)
        self.assertEqual(packets[0], protected_packet(bytes(expected) + parameters, timestamp=456))
        expected[0] = 0xA0
        self.assertEqual(packets[1], protected_packet(bytes(expected) + parameters, timestamp=456))
        for packet in packets:
            self.assertNotIn(parameters, packet)
            self.assertNotIn(b"SYNTHETIC_PASSWORD", packet)
            outer = aes(packet[:32], decrypt=True)
            original_length = struct.unpack_from("<I", outer, 12)[0]
            plaintext = aes(packet[32:], decrypt=True)[:original_length]
            self.assertEqual(plaintext[32:], parameters)
            self.assertFalse(plaintext.endswith(b"\0"))

    def test_client_type_is_explicit_and_not_negotiated_protocol_version(self):
        first, _ = self.request(client_type=7)
        self.assertEqual(aes(first[32:], decrypt=True)[1], 7)
        self.assertEqual(self.setup.protocol_version, 3)
        for client_type in (-1, 256, True, "7"):
            with self.assertRaisesRegex(p.AlarmProtocolError, "^control_client_type_invalid$"):
                self.request(client_type=client_type)

    def test_selected_apk_client_and_customer_defaults_exact_fixture(self):
        # CBBConfig.xml ClientType=3, decoded normal-app OEM customer string.
        parameters = b"SYNTHETIC_USER&&SYNTHETIC_PASSWORD"
        expected = bytearray(32)
        expected[:4] = b"\x0a\x03\x01\x50"
        struct.pack_into("<I", expected, 4, len(parameters))
        expected[8] = 255
        expected[9:24] = b"A0000G0000C0000"
        expected[24:26] = b"\x04\x01"
        expected[30:32] = b"\xa1\xaa"
        first, second = self.request(client_type=3, customer="A0000G0000C0000")
        self.assertEqual(first, protected_packet(bytes(expected) + parameters, timestamp=456))
        expected[0] = 0xA0
        self.assertEqual(second, protected_packet(bytes(expected) + parameters, timestamp=456))

    def test_no_secret_construction_for_downgrade_or_rejection(self):
        for setup in (setup_response(command=0xBE), setup_response(result=1),
                      setup_response(result=51), setup_response(encryption=1),
                      setup_response(sha=0)):
            response = p.parse_control_setup_response(setup)
            with self.assertRaisesRegex(p.AlarmProtocolError, "^control_login_protection_required$"):
                p.build_control_login_requests(self.material, response, username=object(),
                                              password=object(), client_type=0, customer="C0000",
                                              timestamp_seconds=0)

    def test_invalid_credentials_have_fixed_error_without_private_value(self):
        for password in (None, "", "PRIVATE\0DATA", "PRIVATE&&DATA", "A" * 257, "\ud800"):
            with self.assertRaisesRegex(p.AlarmProtocolError, "^control_login_parameter_invalid$"):
                self.request(password=password)

    def test_only_bounded_native_customer_groups_are_supported(self):
        for customer in ("", "C0000", "C0000" * 4):
            first, _ = self.request(customer=customer)
            inner = aes(first[32:], decrypt=True)
            self.assertEqual(inner[9:9 + len(customer)], customer.encode("ascii"))
        for customer in (None, "X", "C0000" * 5, "1234\0", "1234é"):
            with self.assertRaisesRegex(p.AlarmProtocolError, "^control_customer_invalid$"):
                self.request(customer=customer)


if __name__ == "__main__":
    unittest.main()
