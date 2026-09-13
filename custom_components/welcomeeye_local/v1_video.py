"""Conservative V1 complete-video path derived from libglnkio onParse.

No network commands, header stripping, format conversion or fragment guessing.
Connect 2 never instantiates this receiver or uses this TLV parser.
"""
import re
import struct
import time

from .media import inspect_h264_packet
from .protected import ProtocolError

MAX_VIDEO_SIZE = 1048576
METADATA_LIFETIME = 20.0
_START_CODE = re.compile(b'\x00\x00(?:\x00)?\x01')
_VIDEO_METADATA_KINDS = (99, 102, 109)


def _is_annex_b_video(body):
    return body.startswith((b'\x00\x00\x00\x01', b'\x00\x00\x01'))


def parse_video_tlvs(payload):
    """Parse an already complete OWSP payload (sequence excluded).

    Hardware traces show V1 complete-image TLVs 100/101 may declare a short
    value (observed: 1) while the native parser consumes the OWSP remainder as
    the complete Annex-B image. Accept that terminal form only when this same
    packet first carries native-sized video metadata and the remainder itself
    begins with Annex-B. Otherwise retain strict TLV bounds.
    """
    offset, parts, metadata_length = 0, [], None
    while offset < len(payload):
        if len(parts) >= 128 or len(payload) - offset < 4:
            raise ProtocolError('Invalid V1 TLV header or count')
        kind, length = struct.unpack_from('<HH', payload, offset)
        offset += 4
        available = len(payload) - offset
        if not kind or not length or length > available:
            raise ProtocolError('Invalid V1 TLV length')

        if kind in (100, 101) and length != available:
            remainder = payload[offset:]
            if (metadata_length not in (12, 16)
                    or not 0 < available <= MAX_VIDEO_SIZE
                    or not _is_annex_b_video(remainder)):
                raise ProtocolError('Uncorroborated V1 terminal video length')
            length = available

        body = payload[offset:offset + length]
        parts.append((kind, body))
        if kind in _VIDEO_METADATA_KINDS:
            metadata_length = length if length in (12, 16) else None
        offset += length
    return parts


class V1VideoReceiver:
    """One media session; retain only bounded metadata, never video payloads."""

    def __init__(self, observer):
        self.observer = observer
        self.metadata = None
        self.last_sequence = None
        self.awaiting_keyframe = True

    def _count(self, key):
        self.observer.video_event(key)

    def _reject(self, reason):
        self._count(reason)
        self.metadata = None
        self.awaiting_keyframe = True
        return None

    def receive(self, kind, body):
        """Return (unchanged complete Annex-B bytes, native I/P flag) or None."""
        if kind in _VIDEO_METADATA_KINDS:
            self._count('video_metadata_count')
            if kind == 99:
                self._count('tlv99_count')
            if len(body) not in (12, 16):
                return self._reject('invalid_video_metadata')

            # Existing synthetic/native evidence used a 16-byte metadata form
            # containing sequence and exact image size. The real V1 hardware
            # trace uses a 12-byte form. Do not invent fields for that shorter
            # structure: freshness + terminal OWSP boundary + Annex-B framing
            # are sufficient, while sequence/size checks remain available for
            # the known 16-byte form.
            if len(body) == 16:
                sequence = struct.unpack_from('<I', body, 4)[0]
                size = struct.unpack_from('<I', body, 12)[0]
                if not 0 < size <= MAX_VIDEO_SIZE:
                    return self._reject('invalid_video_size')
            else:
                sequence = None
                size = None
                self._count('video_metadata_12_count')

            self.metadata = (sequence, size, time.monotonic())
            return None

        if kind in (103, 106, 107, 108):
            self._count('fragment_tlv_count')
            return self._reject('unsupported_fragment_tlv_count')

        # 97 is audio metadata. Unknown/control types are never promoted.
        if kind not in (100, 101):
            return None

        self._count('tlv100_count' if kind == 100 else 'tlv101_count')
        metadata, self.metadata = self.metadata, None
        if metadata is None:
            return self._reject('missing_video_metadata')
        sequence, size, received = metadata
        if time.monotonic() - received >= METADATA_LIFETIME:
            return self._reject('expired_video_metadata')
        if size is not None and len(body) != size:
            return self._reject('video_size_mismatch')
        if not 0 < len(body) <= MAX_VIDEO_SIZE:
            return self._reject('invalid_video_size')

        # libglnkio's callback copies Annex-B unchanged. A start code later
        # in a random body does not authorize deleting its prefix.
        if not _is_annex_b_video(body):
            return self._reject('unsupported_video_framing')
        for match in _START_CODE.finditer(body):
            if (match.end() >= len(body) or body[match.end()] & 0x80
                    or not 1 <= body[match.end()] & 31 <= 23):
                return self._reject('invalid_video_nal')
        info = inspect_h264_packet(body)
        if not info.detected or not ({1, 5} & set(info.nal_types)):
            return self._reject('missing_video_slice')

        keyframe = kind == 100
        # The native callback flag comes from 100/101, not NAL inspection.
        if not keyframe:
            if self.awaiting_keyframe:
                return self._reject('waiting_for_keyframe')
            if (sequence is not None and self.last_sequence is not None
                    and self.last_sequence != 0
                    and sequence != (self.last_sequence + 1) & 0xffffffff):
                return self._reject('video_sequence_gap')

        if sequence is not None:
            self.last_sequence = sequence
        self.awaiting_keyframe = False
        self._count('complete_video_count')
        return body, keyframe
