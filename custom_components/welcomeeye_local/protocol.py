"""WelcomeEye protocol primitives, reconstructed from APK 6.1.58.24.

Offline building blocks only: no transport, no network calls, no HA integration.
Evidence: TCRequestBean, LtRequestBean, LtResponseBean, QvEncrypt,
and QvLtPlayerCore in the APK's com.quvii packages.
"""

import hashlib
import struct

UNLOCK_REQUEST = 425
UNLOCK_RESPONSE = 426


def encode_password(password: str) -> str:
    """Implement QvEncrypt.encodeWithHashAndMd5 for the encrypted variant."""
    if not password:
        return ""
    sha_hex = hashlib.sha256(password.encode("utf-8")).hexdigest()
    digest = hashlib.md5(sha_hex.encode("ascii"), usedforsecurity=False).digest()
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    return "".join(alphabet[(digest[i] + digest[i + 1]) % 62]
                   for i in range(0, 16, 2))


def build_legacy_unlock(password: str, lock_number: int) -> bytes:
    """Build the 44-byte payload for an authenticated legacy Goolink session.

    This is NOT a complete UDP/TCP packet. Number 1/2 maps to channel 0/1.
    Physical gate versus pedestrian-gate assignment requires device testing.
    Reject overlong input instead of silently truncating a credential.
    """
    if type(lock_number) is not int or lock_number not in (1, 2):
        raise ValueError("lock_number must be 1 or 2")
    encoded = password.encode("utf-8")
    if not encoded or len(encoded) > 32 or b"\x00" in encoded:
        raise ValueError("password must contain 1–32 UTF-8 bytes without NUL")
    return struct.pack("<I32sIBB2s", 0, encoded, 0, lock_number - 1, 1, b"\0\0")


def parse_legacy_unlock_reply(payload: bytes) -> int:
    """Return the signed result from reply type 426; 1 means SDK success.

    Success acknowledges the command; it does not prove the gate is open.
    """
    if len(payload) != 4:
        raise ValueError("unlock response must contain exactly 4 bytes")
    result, _reserved = struct.unpack("<hh", payload)
    return result


def build_stream_mode(mode: int) -> bytes:
    """Build manufacturer payload: app mode 0,1,2 maps to wire 0,2,1."""
    if type(mode) is not int or mode not in (0, 1, 2):
        raise ValueError("mode must be 0, 1 or 2")
    return bytes((1, 5, 4, (0, 2, 1)[mode], 0))


QUERY_STREAM_MODE = bytes((1, 4, 3, 0))
REQUEST_I_FRAME = bytes((1, 4, 11, 0))
