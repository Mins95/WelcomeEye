"""Pure framing for the SDK control channel, independent of QV live framing.

WQ_LIB::setup at ARM64 RVA 0x7c1074 in liblive_player.so SHA256
bb375c04df0f9b15407a8c158163ff121dc517be15ce55def0a4be2064019c68
sends A9 with selector 100. This is a capability exchange, not authentication.
No sockets or downgrade fallback are implemented here. Protected control
packets use the distinct CTcpSocket container, not QV live-media framing.
"""
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from hmac import compare_digest
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

HEADER_SIZE = 32
MAX_CONTROL_BODY = 1024 * 1024
CONTROL_IV = b"0" * 16
ALARM_RESPONSE_SUBTYPES = frozenset((*range(1, 9), *range(10, 14),
                                   *range(157, 191), 192, *range(251, 256)))
ALARM_SUBSCRIPTION_SUBTYPES = (*range(1, 14), 157, 159, 160, 161, *range(174, 191), 192)


class AlarmProtocolError(ValueError):
    """A fixed local reason without untrusted bytes or private material."""


class SetupVariant(StrEnum):
    LEGACY_V1 = "legacy_v1"
    LEGACY_V2 = "legacy_v2"
    MODERN_V3 = "modern_v3"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ControlSetupResponse:
    """Only the native setup result and its selected protocol capabilities."""

    variant: SetupVariant
    result: int
    encryption_mode: int
    sha_mode: int
    response_selector: int | None = None

    @property
    def protocol_version(self):
        return {
            SetupVariant.LEGACY_V1: 1,
            SetupVariant.LEGACY_V2: 2,
            SetupVariant.MODERN_V3: 3,
        }.get(self.variant)

    @property
    def supports_protected_login(self):
        """Advertised AES256/SHA capability; NOT authorization to send secrets.

        Endpoint identity, the control key binding, and credential protection
        must be established separately before a login could be constructed.
        """
        return (self.variant == SetupVariant.MODERN_V3 and self.result == 0
                and self.encryption_mode == 2 and self.sha_mode == 1)

    @property
    def crypto_mode_source(self):
        """Legacy modes are SDK defaults, not advertised response fields."""
        if self.variant in (SetupVariant.LEGACY_V1, SetupVariant.LEGACY_V2):
            return "legacy_sdk_defaults"
        if self.variant == SetupVariant.MODERN_V3:
            return "modern_advertised"
        return "not_available"


def build_control_setup_request():
    """Exact 32-byte native request: A9 at 0, selector 100 at 9, rest zero."""
    return bytes((0xA9,)) + bytes(8) + bytes((100,)) + bytes(22)


def parse_control_setup_response(data):
    """Parse only a complete unencrypted setup prefix, with no body reads.

    Native BE selects legacy v1; A9 with byte 9 in 1..50 selects legacy v2.
    Neither branch is a login acceptance. For modern A9, nonzero byte 9 is
    a result code; zero permits validated encryption/SHA capability fields.
    Unrelated commands are rejected even though native code can fall through.
    """
    if not isinstance(data, bytes) or len(data) != HEADER_SIZE:
        raise AlarmProtocolError("control_setup_size")
    command, result = data[0], data[9]
    if command == 0xBE:
        return ControlSetupResponse(SetupVariant.LEGACY_V1, 0, 0, 0, result)
    if command != 0xA9:
        raise AlarmProtocolError("control_setup_command")
    if 1 <= result <= 50:
        return ControlSetupResponse(SetupVariant.LEGACY_V2, 0, 0, 0, result)
    if result:
        return ControlSetupResponse(SetupVariant.REJECTED, result, 0, 0, result)
    encryption_mode, sha_mode = data[10], data[11]
    if encryption_mode not in (0, 1, 2) or sha_mode not in (0, 1):
        raise AlarmProtocolError("control_setup_crypto_mode")
    return ControlSetupResponse(SetupVariant.MODERN_V3, 0, encryption_mode, sha_mode, result)


def _number(value, maximum, reason):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise AlarmProtocolError(reason)
    return value


@dataclass(frozen=True)
class ControlCipherMaterial:
    """Direct ASCII control AES256 key; no hex decoding or credential logging.

    Callers obtain the key through the verified ap=1 CGI material route.
    This codec deliberately implements only modern AES256 with SHA256.
    """

    stream_key: str = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.stream_key, str) or not 32 <= len(self.stream_key) <= 256:
            raise AlarmProtocolError("control_key_invalid")
        try:
            raw = self.stream_key.encode("ascii")
        except UnicodeError:
            raise AlarmProtocolError("control_key_invalid") from None
        if any(byte < 32 or byte == 127 for byte in raw):
            raise AlarmProtocolError("control_key_invalid")

    @property
    def key(self):
        return self.stream_key.encode("ascii")[:32]


def _control_crypt(data, material, *, decrypt=False):
    if not isinstance(material, ControlCipherMaterial):
        raise AlarmProtocolError("control_key_invalid")
    if not isinstance(data, bytes) or len(data) % 16:
        raise AlarmProtocolError("control_cipher_size")
    cipher = Cipher(algorithms.AES(material.key), modes.CBC(CONTROL_IV))
    operation = cipher.decryptor() if decrypt else cipher.encryptor()
    return operation.update(data) + operation.finalize()


def encode_control_packet(payload, material, *, timestamp_seconds):
    """CTcpSocket::EncryptData 0x7f5498, AES256/SHA256 container.

    Native outer header: epoch seconds u64@0, padded body length u32@8,
    original packet length u32@12. SHA covers this outer header and original
    packet. Header and padded body are AES encrypted with separate IV resets.
    """
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_CONTROL_BODY - 32:
        raise AlarmProtocolError("control_payload_size")
    _number(timestamp_seconds, 0xFFFFFFFFFFFFFFFF, "control_timestamp_invalid")
    body_length = (len(payload) + 32 + 15) // 16 * 16
    header = struct.pack("<QII", timestamp_seconds, body_length, len(payload)) + bytes(16)
    digest = sha256(header + payload).digest()
    body = payload + digest + bytes(body_length - len(payload) - len(digest))
    return _control_crypt(header, material) + _control_crypt(body, material)


@dataclass(frozen=True)
class ControlPacketHeader:
    body_length: int
    plaintext_length: int
    plaintext: bytes = field(repr=False)


def decode_control_header(data, material):
    """Validate allocation bounds before the caller reads the encrypted body."""
    if not isinstance(data, bytes) or len(data) != HEADER_SIZE:
        raise AlarmProtocolError("control_header_size")
    plaintext = _control_crypt(data, material, decrypt=True)
    body_length, original_length = struct.unpack_from("<II", plaintext, 8)
    if not 1 <= original_length <= MAX_CONTROL_BODY - 32:
        raise AlarmProtocolError("control_payload_size")
    if body_length > MAX_CONTROL_BODY or body_length != (original_length + 32 + 15) // 16 * 16:
        raise AlarmProtocolError("control_body_size")
    return ControlPacketHeader(body_length, original_length, plaintext)


def decode_control_body(header, data, material):
    """Return integrity-checked native bytes only after SHA verification.

    The native digest is plain SHA256, not a keyed MAC or peer identity proof.
    """
    if (not isinstance(header, ControlPacketHeader) or
            not isinstance(data, bytes) or len(data) != header.body_length or
            len(data) > MAX_CONTROL_BODY):
        raise AlarmProtocolError("control_body_size")
    body = _control_crypt(data, material, decrypt=True)
    original = body[:header.plaintext_length]
    digest = body[header.plaintext_length:header.plaintext_length + 32]
    if not compare_digest(sha256(header.plaintext + original).digest(), digest):
        raise AlarmProtocolError("control_digest_mismatch")
    if any(body[header.plaintext_length + 32:]):
        raise AlarmProtocolError("control_padding_invalid")
    return original


@dataclass(frozen=True)
class NativePacketHeader:
    command: int
    body_length: int


def parse_native_packet_header(data):
    """CTcpSocket::OnProcessData 0x7f3648: body length is u32 LE at 4."""
    if not isinstance(data, bytes) or len(data) != HEADER_SIZE:
        raise AlarmProtocolError("native_header_size")
    body_length = struct.unpack_from("<I", data, 4)[0]
    if body_length > MAX_CONTROL_BODY - HEADER_SIZE:
        raise AlarmProtocolError("native_body_size")
    return NativePacketHeader(data[0], body_length)


def _native_packet(data, command):
    if not isinstance(data, bytes) or len(data) < HEADER_SIZE:
        raise AlarmProtocolError("native_packet_size")
    header = parse_native_packet_header(data[:HEADER_SIZE])
    if len(data) != HEADER_SIZE + header.body_length:
        raise AlarmProtocolError("native_packet_size")
    if header.command != command:
        raise AlarmProtocolError("native_packet_command")
    return header


@dataclass(frozen=True)
class AlarmNotification:
    """Native callback metadata, with no physical bell/panel inference."""

    subtype: int
    callback_type: int
    body_length: int


def parse_alarm_notification(data):
    """CDvrAlarmChannel::OnRespond 0x7db2ac, callback mode 1, command 69.

    Subtype 5 with header byte 13 equal to 1 is normalized to 254 by native
    code before adding 100 to the callback type. Payload bytes are not read,
    retained or returned. This is not the later application alarm enum.
    """
    header = _native_packet(data, 0x69)
    subtype = data[12]
    if subtype == 5 and data[13] == 1:
        subtype = 254
    if subtype not in ALARM_RESPONSE_SUBTYPES:
        raise AlarmProtocolError("alarm_subtype_unsupported")
    return AlarmNotification(subtype, subtype + 100, header.body_length)


def build_alarm_subscription_requests(*, device_information_word):
    """Exact mode-1 series from sendAlarmQuery_dvr2 0x7e5b58.

    The opaque word comes from native device information query 14; it must
    be supplied by the caller's proven login state, never a guessed channel.
    Returns native 32-byte headers which require control-container encryption.
    These requests have no firmware acceptance implied by construction.
    """
    _number(device_information_word, 0xFFFFFFFF, "alarm_information_invalid")
    packets = []
    for subtype in ALARM_SUBSCRIPTION_SUBTYPES:
        header = bytearray(HEADER_SIZE)
        header[0], header[8], header[12] = 0x68, 2, subtype
        struct.pack_into("<I", header, 28, device_information_word)
        packets.append(bytes(header))
    return tuple(packets)


def build_control_keepalive_request():
    """Native SDK heartbeat: A1 in a zeroed 32-byte native packet.

    The result still requires encode_control_packet; this is not the QV
    live-media keepalive, whose command and container are different.
    """
    return b"\xa1" + bytes(HEADER_SIZE - 1)


@dataclass(frozen=True)
class ControlLoginResponse:
    accepted: bool
    result: int


def parse_control_login_response(data):
    """B0 only: DealSpecialPacket 0x7f3430; parse_login_respond 0x7c0594.

    Byte 8 is the rejection flag; byte 9 is an error only on rejection.
    No private model strings, account-rule bytes or device fields are retained.
    """
    _native_packet(data, 0xB0)
    accepted = data[8] == 0
    return ControlLoginResponse(accepted, 0 if accepted else data[9])


def _login_parameter(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 256:
        raise AlarmProtocolError("control_login_parameter_invalid")
    try:
        raw = value.encode("utf-8")
    except UnicodeError:
        raise AlarmProtocolError("control_login_parameter_invalid") from None
    if len(raw) > 256 or b"&&" in raw or any(byte < 32 or byte == 127 for byte in raw):
        raise AlarmProtocolError("control_login_parameter_invalid")
    return raw


def _native_login_requests(*, username, password, client_type, customer):
    """Private serializer shared by two separately guarded transport paths."""
    _number(client_type, 255, "control_client_type_invalid")
    if not isinstance(customer, str) or len(customer) > 20 or len(customer) % 5:
        raise AlarmProtocolError("control_customer_invalid")
    try:
        customer_bytes = customer.encode("ascii")
    except UnicodeError:
        raise AlarmProtocolError("control_customer_invalid") from None
    if any(byte < 32 or byte == 127 for byte in customer_bytes):
        raise AlarmProtocolError("control_customer_invalid")
    parameters = _login_parameter(username) + b"&&" + _login_parameter(password)
    header = bytearray(HEADER_SIZE)
    header[0], header[1], header[2], header[3] = 0x0A, client_type, 1, 0x50
    struct.pack_into("<I", header, 4, len(parameters))
    header[8], header[24], header[25], header[30], header[31] = 255, 4, 1, 0xA1, 0xAA
    header[9:9 + len(customer_bytes)] = customer_bytes
    login = bytes(header) + parameters
    header[0] = 0xA0
    companion = bytes(header) + parameters
    return login, companion


def build_control_login_requests(material, setup, *, username, password,
                                 client_type, customer, timestamp_seconds):
    """Modern IPv4 SDK login pair, protected by native AES256/SHA256.

    build_login_packet 0x7c00dc and try_connect_dhdvr 0x7c2d24..0x7c2df4:
    byte 1 is SDK_SetClientType's value, NOT the negotiated protocol version.
    Byte 3 is 0x50 for local IPv4, body is username&&password without a NUL.
    Both 0A and A0 are the single native login sequence, never retries.
    Only the bounded header-only customer profile is supported.
    """
    if not isinstance(setup, ControlSetupResponse) or not setup.supports_protected_login:
        raise AlarmProtocolError("control_login_protection_required")
    if not isinstance(material, ControlCipherMaterial):
        raise AlarmProtocolError("control_key_invalid")
    return tuple(encode_control_packet(packet, material, timestamp_seconds=timestamp_seconds)
        for packet in _native_login_requests(username=username, password=password,
                                            client_type=client_type, customer=customer))


def build_tls_legacy_login_requests(setup, *, tls_verified, username, password,
                                    client_type, customer):
    """SDK V2 native login, allowed only inside an already verified TLS stream.

    The SDK reconnects once after V2 setup and sends these native packets
    directly. The password supplied by the app is an encoded authCode and is
    still a reusable credential. V2 has no independent native encryption;
    this builder must never be used on the plain TCP transport.
    """
    if tls_verified is not True:
        raise AlarmProtocolError("control_tls_required")
    if (not isinstance(setup, ControlSetupResponse)
            or setup.variant != SetupVariant.LEGACY_V2 or setup.result != 0):
        raise AlarmProtocolError("control_legacy_setup_required")
    return _native_login_requests(username=username, password=password,
                                 client_type=client_type, customer=customer)
