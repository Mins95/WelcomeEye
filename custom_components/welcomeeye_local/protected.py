"""Protected Goolink wire primitives derived from protocol interoperability analysis.

Discovery decryption and authenticated login have been validated with a
WelcomeEye Connect 2 test device. No network calls are performed in this module.
"""

from dataclasses import dataclass
import ipaddress
import json
import secrets
import string
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
try:
    from cryptography.hazmat.decrepit.ciphers.modes import CFB
except ImportError:
    from cryptography.hazmat.primitives.ciphers.modes import CFB


class ProtocolError(ValueError):
    """Malformed or unsupported device data."""


def aes_cfb(key, iv, data, *, decrypt=False):
    cipher = Cipher(algorithms.AES(key), CFB(iv))
    context = cipher.decryptor() if decrypt else cipher.encryptor()
    return context.update(data) + context.finalize()


def rc4(key, data):
    """Legacy protocol interoperability only; never use for new encryption."""
    if not key:
        raise ValueError("empty RC4 key")
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) & 255
        state[i], state[j] = state[j], state[i]
    i = j = 0
    out = bytearray()
    for byte in data:
        i = (i + 1) & 255
        j = (j + state[i]) & 255
        state[i], state[j] = state[j], state[i]
        out.append(byte ^ state[(state[i] + state[j]) & 255])
    return bytes(out)


def _alphabet_index(value):
    for first, last in ((65, 90), (97, 122), (48, 57)):
        if first <= value <= last:
            return value - first
    raise ProtocolError("Invalid nonce character")


def check_nonce(block, *, server=False):
    # checkRandDataForSvr uses index 8 twice and lower-case checks;
    # checkRandDataForLogin uses indexes 6/10 and upper-case checks.
    if len(block) != 16:
        raise ProtocolError("Nonce must be 16 bytes")
    first, second, base = (8, 8, 97) if server else (6, 10, 65)
    residue = _alphabet_index(block[first]) % 3
    if _alphabet_index(block[second]) % 3 != residue:
        raise ProtocolError("Nonce markers disagree")
    for i in range(15):
        if i in (first, second) or i % 3 != residue:
            continue
        if not base <= block[i] <= base + 25 or (block[i] - base) % 4:
            raise ProtocolError("Nonce check failed")


def login_nonce():
    result = bytearray(16)
    marker = secrets.choice(string.ascii_letters)
    result[6] = ord(marker)
    result[10] = ord(marker.swapcase())
    residue = _alphabet_index(result[6]) % 3
    for i in range(15):
        if i in (6, 10):
            continue
        if i % 3 == residue:
            result[i] = 65 + 4 * secrets.randbelow(4)
        else:
            alphabet = (string.ascii_uppercase, string.ascii_lowercase, string.digits)[i % 3]
            result[i] = ord(secrets.choice(alphabet))
    return bytes(result)


@dataclass(frozen=True, repr=False)
class DiscoveryInfo:
    uid: str
    udp_port: int
    tcp_port: int
    address: str
    protected: bool


def decode_discovery(packet):
    if len(packet) < 4:
        raise ProtocolError("Missing discovery header")
    command, length = struct.unpack_from("<HH", packet)
    if len(packet) != length + 4:
        raise ProtocolError("Discovery length mismatch")
    if command == 0xA036 and length == 100:
        first, last = packet[4:20], packet[88:104]
        check_nonce(first, server=True)
        check_nonce(last, server=True)
        key = first[5:14] + bytes(7)
        iv = last[3:9] + bytes(10)
        clear = aes_cfb(key, iv, packet[20:88], decrypt=True)
        protected = True
    elif command == 0xA008 and length == 68:
        clear = packet[4:]
        protected = False
    else:
        raise ProtocolError("Unsupported discovery reply")
    uid_bytes = clear[:32].split(b"\0", 1)[0]
    if not uid_bytes or not all(33 <= byte < 127 for byte in uid_bytes):
        raise ProtocolError("Invalid discovered identifier")
    udp_port, tcp_port = struct.unpack_from("<HH", clear, 32)
    if not udp_port or not tcp_port:
        raise ProtocolError("Invalid discovered ports")
    try:
        address = str(ipaddress.IPv4Address(clear[36:52].split(b"\0", 1)[0].decode("ascii")))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError("Invalid discovered IP") from exc
    return DiscoveryInfo(uid_bytes.decode("ascii"), udp_port, tcp_port, address, protected)


def tlv(kind, payload):
    if not 0 <= kind <= 65535 or len(payload) > 65535:
        raise ProtocolError("TLV exceeds protocol bounds")
    return struct.pack("<HH", kind, len(payload)) + payload


def owsp(payload, sequence=0):
    return struct.pack(">I", len(payload) + 4) + struct.pack("<I", sequence) + payload


def parse_tlvs(payload):
    result = []
    offset = 0
    while offset < len(payload):
        if len(payload) - offset < 4:
            raise ProtocolError("Truncated TLV header")
        kind, length = struct.unpack_from("<HH", payload, offset)
        offset += 4
        if offset + length > len(payload):
            raise ProtocolError("Truncated TLV payload")
        result.append((kind, payload[offset:offset + length]))
        offset += length
    return result


def build_protected_login(uid, username, encoded_password, *, channel=16,
                          stream=1, mode=2, nonces=None):
    """Reconstruct native packLoginPacketEnc/requestLoginEnc; experimental.

    encoded_password comes from protocol.encode_password. This never sends.
    Metadata uses the null login_id string generated by the native caller.
    """
    uid_bytes = uid.encode("ascii")
    user_bytes = username.encode("utf-8")
    password_bytes = encoded_password.encode("ascii")
    if not uid_bytes or len(uid_bytes) > 31 or b"\0" in uid_bytes:
        raise ValueError("Invalid UID")
    if not user_bytes or len(user_bytes) > 31 or b"\0" in user_bytes:
        raise ValueError("Invalid username")
    # QvEncrypt.sha256/getEncode preserve an empty password as an empty string.
    if len(password_bytes) > 15 or b"\0" in password_bytes:
        raise ValueError("Invalid encoded password")
    if not 0 <= channel < 64 or not 0 <= stream <= 255 or not 0 <= mode <= 255:
        raise ValueError("Invalid login parameters")
    first, second, third = nonces or tuple(login_nonce() for _ in range(3))
    for block in (first, second, third):
        check_nonce(block)
    key = first[:8] + second[4:11] + b"\0"
    iv = third[4:15] + bytes(5)
    clear = bytearray(68)
    clear[1:33] = user_bytes.ljust(32, b"\0")
    clear[33:49] = password_bytes.ljust(16, b"\0")
    mask = 1 << channel
    struct.pack_into("<I", clear, 56, mask >> 32)
    struct.pack_into("<I", clear, 60, mask & 0xFFFFFFFF)
    clear[64], clear[65] = stream, mode
    metadata = b'{"login_id":"(null)"}'
    padded_length = len(metadata) + 5 - ((len(metadata) + 5) % 16) + 16
    encrypted_metadata = aes_cfb(key, iv, metadata.ljust(padded_length, b"\0"))
    inner = first + aes_cfb(key, iv, bytes(clear)) + second + third
    inner += struct.pack("<I", padded_length) + encrypted_metadata
    return owsp(tlv(40, struct.pack("<I", 5)) + tlv(501, rc4(uid_bytes, inner)))


def decode_login_reply(uid, payload):
    """Decode TLV 502. Returns device status, reason, and ancillary JSON."""
    if len(payload) < 64 or len(payload) > 2048:
        raise ProtocolError("Invalid protected login reply size")
    inner = rc4(uid.encode("ascii"), payload)
    first, second, third = inner[:16], inner[28:44], inner[44:60]
    for block in (first, second, third):
        check_nonce(block)
    key = first[:11] + second[4:8] + b"\0"
    iv = third[4:12] + bytes(8)
    clear = aes_cfb(key, iv, inner[16:28], decrypt=True)
    size = struct.unpack_from("<I", inner, 60)[0]
    if size > len(inner) - 64:
        raise ProtocolError("Truncated login metadata")
    metadata = aes_cfb(key, iv, inner[64:64 + size], decrypt=True).split(b"\0", 1)[0]
    try:
        extra = json.loads(metadata) if metadata else {}
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError("Invalid login metadata") from exc
    if not isinstance(extra, dict):
        raise ProtocolError("Login metadata must be an object")
    status, reason = struct.unpack_from("<HH", clear, 8)
    return status, reason, extra


def decode_private_reply(uid, payload):
    """Native parsePriProRspData: TLV 510 => device time + manufacturer data."""
    if not 44 <= len(payload) <= 3072:
        raise ProtocolError('Invalid private response length')
    inner = rc4(uid.encode('ascii'), payload)
    if struct.unpack_from('<I', inner)[0] != 1:
        raise ProtocolError('Unknown private encryption type')
    first, last = inner[4:20], inner[-16:]
    key = first[4:13] + last[5:10] + bytes(2)
    iv = last[3:9] + bytes(10)
    clear = aes_cfb(key, iv, inner[20:-16], decrypt=True)
    return struct.unpack_from('<Q', clear)[0], clear[8:]


def profile_nonce(profile):
    """Generate the dynamic nonce described by the login AppId field."""
    first, second, choices, case = struct.pack('>I', profile)
    if first >= 15 or second >= 15 or first == second or not 2 <= choices <= 6 or case not in (1, 2):
        raise ProtocolError('Unsupported encryption profile')
    result = bytearray(16)
    marker = secrets.choice(string.ascii_letters)
    result[first], result[second] = ord(marker), ord(marker.swapcase())
    residue = _alphabet_index(result[first]) % 3
    for i in range(15):
        if i in (first, second):
            continue
        if i % 3 == residue:
            result[i] = (65 if case == 1 else 97) + 4 * secrets.randbelow(choices)
        else:
            result[i] = ord(secrets.choice((string.ascii_uppercase, string.ascii_lowercase, string.digits)[i % 3]))
    return bytes(result)


def build_private_query(uid, profile, device_time, data, *, kind=509):
    """Native packetPriProReqData, for authenticated manufacturer requests."""
    if len(data) > 2048 or kind not in (507, 509):
        raise ValueError('Invalid private request')
    a, b = profile_nonce(profile), profile_nonce(profile)
    key, iv = a[3:11] + b[6:11] + bytes(3), b[3:11] + bytes(8)
    clear = struct.pack('<Q', device_time) + data
    return owsp(tlv(kind, rc4(uid.encode('ascii'), a + aes_cfb(key, iv, clear) + b)))
