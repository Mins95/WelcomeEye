"""Bounded structural observations only; never route, decode or retain video.

The examined libglnkio onParse maps 97 to an eight-byte audio metadata
structure, 99/102/109 to video metadata, and 100/101 to complete video.
103/106/107/108 are fragment types. These observations are not a V1 parser.
"""
from collections import Counter, deque
from copy import deepcopy
import re
import struct
import threading

from .media import inspect_h264_packet

_MEDIA = frozenset(range(97, 110))
_KNOWN = _MEDIA | {40, 49, 57, 70, 203, 502, 510, 5008, 5010}
_TERMINAL = {98, 100, 101, 103, 106, 107, 108}
_MAX_FRAMES = 128
_MAX_TLVS = 32
_MAX_SCAN = 65536
_START_CODE = re.compile(b'\x00\x00(?:\x00)?\x01')


def _shape(body):
    """Offsets and NAL types are hints, never a claimed proprietary header."""
    scan = body[:_MAX_SCAN]
    positions, types = [], set()
    capped = False
    for match in _START_CODE.finditer(scan):
        if len(positions) == 8:
            capped = True
            break
        if match.end() < len(scan):
            positions.append(match.start())
            nal = scan[match.end()]
            if not nal & 0x80 and 1 <= nal & 31 <= 23:
                types.add(nal & 31)
    info = inspect_h264_packet(scan)
    # AVCC requires a complete body, not an accidentally aligned scan prefix.
    framing = info.framing if info.detected else None
    if len(scan) != len(body) and framing == 'avcc':
        framing = None
    return {
        'body_size': len(body),
        'scanned_bytes': len(scan),
        'scan_truncated': len(scan) != len(body),
        'start_code_offsets': positions,
        'start_code_list_capped': capped,
        'nal_types_at_sampled_start_codes': sorted(types),
        'framing_candidate': framing,
        'nal_types_candidate': list(info.nal_types) if framing else [],
    }


def _structure(payload):
    """Inspect the strict TLV chain and, separately, native terminal lengths."""
    offset, rows = 0, []
    result = {'payload_size': len(payload), 'tlvs': rows, 'strict_tlv_status': 'ok'}
    while offset < len(payload):
        if len(rows) == _MAX_TLVS:
            result['strict_tlv_status'] = 'scan_limit'
            break
        if len(payload) - offset < 4:
            result['strict_tlv_status'] = 'truncated_header'
            result['error_offset'] = offset
            break
        kind, length = struct.unpack_from('<HH', payload, offset)
        start = offset + 4
        available = len(payload) - start
        row = {'kind': kind if kind in _KNOWN else 'other',
               'header_offset': offset, 'declared_length': length,
               'remaining_after_header': available}
        rows.append(row)
        if kind in _TERMINAL:
            row['native_terminal_length_relation'] = (
                'equal' if length == available else 'shorter' if length < available else 'longer'
            )
            # This is a diagnostic candidate only. No bytes go to the decoder.
            row['native_terminal_candidate'] = _shape(payload[start:])
        if length > available:
            result['strict_tlv_status'] = 'truncated_payload'
            result['error_offset'] = offset
            break
        if kind in _MEDIA:
            body = payload[start:start + length]
            row['declared_body'] = _shape(body)
            if kind == 97:
                row['matches_native_audio_metadata_size'] = length == 8
            elif kind in (99, 102, 109):
                row['matches_native_video_metadata_size'] = length == 16
        offset = start + length
        # Native consumes the remainder at these types. If lengths disagree,
        # don't interpret encoded media bytes as more headers in diagnostics.
        if kind in _TERMINAL and length != available:
            result['strict_tlv_status'] = 'native_terminal_boundary_differs'
            break
    return result


class V1VideoDiagnostics:
    """Per-media-session observer, with bounded samples and no network I/O."""

    def __init__(self):
        self.lock = threading.Lock()
        self.phase = 'idle'
        self.length_words = self.complete_frames = self.parsed_frames = 0
        self.parse_failures = self.read_failures = self.sampled_frames = 0
        self.last_announced_size = self.last_complete_size = None
        self.rows = deque(maxlen=8)
        self.errors = deque(maxlen=8)
        self.media_counts = Counter()
        self.media_lengths = {}
        self.metadata_sizes = Counter()
        self.terminal_mismatches = 0

    def begin_header(self):
        with self.lock:
            self.phase = 'length_word'

    def begin_payload(self, size):
        with self.lock:
            self.phase = 'owsp_payload'
            self.length_words += 1
            self.last_announced_size = size

    def read_failed(self, requested, received, error_type):
        with self.lock:
            self.read_failures += 1
            self.errors.append({'phase': self.phase, 'requested_bytes': requested,
                                'received_bytes': received, 'error_type': error_type})

    def complete(self, frame):
        with self.lock:
            self.complete_frames += 1
            self.last_complete_size = len(frame)
            self.phase = 'tlv_parse'
            if self.sampled_frames >= _MAX_FRAMES:
                return
            self.sampled_frames += 1
        # frame includes the four-byte OWSP sequence, excluded from diagnostics.
        summary = _structure(frame[4:])
        with self.lock:
            self.rows.append(summary)
            for row in summary['tlvs']:
                kind = row['kind']
                if kind in _MEDIA:
                    self.media_counts[kind] += 1
                    length = row['declared_length']
                    bounds = self.media_lengths.setdefault(str(kind), {'min': length, 'max': length})
                    bounds['min'] = min(bounds['min'], length)
                    bounds['max'] = max(bounds['max'], length)
                if kind in (97, 99, 102, 109):
                    key = f'{kind}_expected_size' if row.get(
                        'matches_native_audio_metadata_size', row.get('matches_native_video_metadata_size', False)
                    ) else f'{kind}_unexpected_size'
                    self.metadata_sizes[key] += 1
                relation = row.get('native_terminal_length_relation')
                if relation and relation != 'equal':
                    self.terminal_mismatches += 1

    def parsed(self, success):
        with self.lock:
            if success:
                self.parsed_frames += 1
            else:
                self.parse_failures += 1
            self.phase = 'idle'

    def snapshot(self):
        with self.lock:
            return {
                'scope': 'last_v1_media_session',
                'diagnostic_only': True,
                'valid_length_words': self.length_words,
                'complete_owsp_frames': self.complete_frames,
                'successfully_parsed_frames': self.parsed_frames,
                'tlv_parse_failures': self.parse_failures,
                'read_failures': self.read_failures,
                'last_announced_size': self.last_announced_size,
                'last_complete_size': self.last_complete_size,
                'sampled_frames': self.sampled_frames,
                'sampling_limit': _MAX_FRAMES,
                'sampling_capped': self.complete_frames > self.sampled_frames,
                'sampled_media_tlv_counts': {str(k): v for k, v in sorted(self.media_counts.items())},
                'sampled_media_tlv_length_bounds': deepcopy(self.media_lengths),
                'sampled_metadata_size_counts': dict(self.metadata_sizes),
                'sampled_native_terminal_length_mismatches': self.terminal_mismatches,
                'recent_frame_structures': deepcopy(list(self.rows)),
                'recent_read_errors': deepcopy(list(self.errors)),
            }
