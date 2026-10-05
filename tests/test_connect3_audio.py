"""Synthetic QV audio metadata and real PyAV decode; no device or microphone."""
from fractions import Fraction
import json
import struct
import unittest
from unittest.mock import Mock

import av
try:
    from aiortc.codecs.opus import OpusEncoder
except ImportError:
    OpusEncoder = None

from load_integration import load

p = load('connect3.protocol')
audio = load('connect3.audio')


def audio_packet(payload, *, codec=4, rate=8000, channels=1, frame_type=3):
    # Independently construct native QV20 metadata then use the actual assembler.
    header = bytearray(20)
    header[:4] = b'\0\0\1' + bytes((0xE0 + frame_type,))
    struct.pack_into('<I', header, 4, len(payload))
    header[14], header[15] = codec, channels
    struct.pack_into('<H', header, 16, rate)
    return p.FrameAssembler().feed(bytes(header) + payload)[0]


def synthetic_aac(*, rate=8000, channels=1):
    encoder = av.CodecContext.create('aac', 'w')
    encoder.sample_rate = rate
    encoder.layout = 'mono' if channels == 1 else 'stereo'
    encoder.format = 'fltp'
    encoder.bit_rate = 24000 * channels
    source = av.AudioFrame(format='fltp', layout=encoder.layout.name, samples=1024)
    source.sample_rate, source.time_base, source.pts = rate, Fraction(1, rate), 0
    for plane in source.planes:
        plane.update(struct.pack('<1024f', *([0.1, -0.1] * 512)))
    packets = encoder.encode(source) + encoder.encode(None)
    # A standard self-describing test fixture, never added by the decoder.
    index = {8000: 11, 16000: 8}[rate]
    result = []
    for packet in packets:
        raw = bytes(packet)
        length = len(raw) + 7
        header = bytes((0xff, 0xf1, (1 << 6) | (index << 2) | (channels >> 2),
                        ((channels & 3) << 6) | (length >> 11), (length >> 3) & 0xff,
                        ((length & 7) << 5) | 0x1f, 0xfc))
        result.append(header + raw)
    return result


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.decoder = audio.AudioDecoder()

    def tearDown(self):
        self.decoder.close()
        self.assertIsNone(self.decoder._decoder)

    def samples(self, frame):
        length = frame.samples * len(frame.layout.channels)
        return struct.unpack('<' + str(length) + 'h', bytes(frame.planes[0])[:length * 2])

    def test_native_header_audio_predicate_frequency_and_channels(self):
        for frame_type in range(12):
            for codec in (4, 5, 6, 7, 8, 9, 12, 13, 1):
                with self.subTest(frame_type=frame_type, codec=codec):
                    packet = audio_packet(b'SYNTHETIC', codec=codec, rate=16000,
                                          channels=2, frame_type=frame_type)
                    expected = frame_type not in (7, 8) and codec != 1
                    self.assertEqual(packet.is_audio, expected)
                    self.assertEqual(packet.sample_rate, 16000 if expected else None)
                    self.assertEqual(packet.channels, 2 if expected else None)

    def test_g711_known_independent_alaw_and_mulaw_vectors(self):
        for codec, raw, expected in (
            (4, b'\xd5\x55\x00\x80\xaa\x2a', (8, -8, -5504, 5504, 32256, -32256)),
            (5, b'\xff\x7f\x00\x80', (0, 0, -32124, 32124)),
        ):
            with self.subTest(codec=codec):
                padded = raw + raw[:1] * (160 - len(raw))
                frame, = self.decoder.feed(audio_packet(padded, codec=codec))
                self.assertEqual(self.samples(frame), expected + (expected[0],) * (160 - len(raw)))
                self.assertEqual((frame.sample_rate, frame.layout.name, frame.format.name),
                                 (8000, 'mono', 's16'))
        self.assertEqual(self.decoder.diagnostics['input_packets'], 2)
        self.assertEqual(self.decoder.diagnostics['input_bytes'], 320)
        self.assertEqual(self.decoder.diagnostics['decoded_frames'], 2)
        self.assertEqual(self.decoder.diagnostics['decoded_samples'], 320)
        self.assertEqual(self.decoder.errors, 0)

    def test_pcm_stereo_exact_bytes_and_monotone_time_across_format_change(self):
        first, = self.decoder.feed(audio_packet(b'\xd5' * 160))
        pcm = struct.pack('<8h', -32768, 32767, -1, 1, 0, 0, 222, -222)
        second, = self.decoder.feed(audio_packet(pcm * 80, codec=9, rate=16000, channels=2))
        self.assertEqual(self.samples(second), (-32768, 32767, -1, 1, 0, 0, 222, -222) * 80)
        self.assertEqual(second.samples, 320)
        self.assertEqual(second.time_base, Fraction(1, 16000))
        self.assertEqual(second.pts * second.time_base,
                         first.pts * first.time_base + Fraction(first.samples, first.sample_rate))
        self.assertEqual(self.decoder.diagnostics['codec'], 'pcm_s16le')
        self.assertEqual(self.decoder.diagnostics['sample_rate'], 16000)
        self.assertEqual(self.decoder.diagnostics['channels'], 2)

    def test_direct_self_describing_aac_real_decoder_without_invented_extradata(self):
        for rate, channels in ((8000, 1), (16000, 2)):
            with self.subTest(rate=rate, channels=channels):
                before = self.decoder.frames
                for payload in synthetic_aac(rate=rate, channels=channels):
                    frames = self.decoder.feed(audio_packet(payload, codec=8,
                                                            rate=rate, channels=channels))
                    self.assertTrue(frames)
                    for frame in frames:
                        self.assertEqual((frame.sample_rate, len(frame.layout.channels)),
                                         (rate, channels))
                        self.assertEqual(frame.samples, rate // 50)
                        self.assertEqual(frame.format.name, 's16')
                self.assertGreater(self.decoder.frames, before)
                self.assertFalse(self.decoder._decoder.extradata)
        self.assertEqual(self.decoder.diagnostics['status'], 'decoded')

    @unittest.skipIf(OpusEncoder is None, 'aiortc is exercised in the HA runtime suite')
    def test_aac_decoded_frames_enter_real_webrtc_opus_encoder(self):
        for rate, channels in ((8000, 1), (16000, 2)):
            with self.subTest(rate=rate, channels=channels):
                opus = OpusEncoder()
                encoded = []
                timestamps = []
                for payload in synthetic_aac(rate=rate, channels=channels):
                    for frame in self.decoder.feed(audio_packet(payload, codec=8,
                                                               rate=rate, channels=channels)):
                        packets, timestamp = opus.encode(frame)
                        self.assertLessEqual(len(packets), 1)
                        if packets:
                            timestamps.append(timestamp)
                        encoded.extend(packets)
                self.assertTrue(encoded)
                self.assertGreaterEqual(timestamp, 0)
                self.assertTrue(all(b > a for a, b in zip(timestamps, timestamps[1:])))

    def test_short_audio_tail_is_retained_without_loss_or_oversized_frame(self):
        self.assertEqual(self.decoder.feed(audio_packet(b'\xd5' * 64)), [])
        self.assertEqual(self.decoder.diagnostics['buffered_samples'], 64)
        first, = self.decoder.feed(audio_packet(b'\x55' * 96))
        self.assertEqual(self.samples(first), (8,) * 64 + (-8,) * 96)
        self.assertEqual(self.decoder.diagnostics['buffered_samples'], 0)
        frames = self.decoder.feed(audio_packet(b'\xd5' * 1600))
        self.assertEqual(len(frames), 10)
        self.assertTrue(all(frame.samples == 160 for frame in frames))
        self.assertEqual([f.pts for f in frames], list(range(160, 1760, 160)))

    def test_nonintegral_20ms_rate_remains_bounded_and_preserves_samples(self):
        total = []
        for _ in range(5):
            total.extend(self.decoder.feed(audio_packet(b'\xd5' * 441, rate=11025)))
        self.assertTrue(all(frame.samples == 220 for frame in total))
        self.assertEqual(sum(frame.samples for frame in total) +
                         self.decoder.diagnostics['buffered_samples'], 2205)

    def test_unknown_codec_metadata_and_missing_format_fail_before_decoder(self):
        for packet, reason in (
            (audio_packet(b'SYNTHETIC', codec=12), 'unsupported_audio_codec'),
            (audio_packet(b'SYNTHETIC', codec=13), 'unsupported_audio_codec'),
            (audio_packet(b'SYNTHETIC', frame_type=7), 'unsupported_audio_frame'),
            (audio_packet(b'SYNTHETIC', rate=0), 'unsupported_audio_format'),
            (audio_packet(b'SYNTHETIC', rate=65535), 'unsupported_audio_format'),
            (audio_packet(b'SYNTHETIC', channels=0), 'unsupported_audio_format'),
            (audio_packet(b'SYNTHETIC', channels=3), 'unsupported_audio_format'),
        ):
            with self.subTest(reason=reason), self.assertRaisesRegex(
                    audio.UnsupportedAudioFormat, '^' + reason + '$'):
                self.decoder.feed(packet)
        self.assertEqual(self.decoder.diagnostics['unsupported_packets'], 7)
        self.assertEqual(self.decoder.diagnostics['decode_errors'], 0)
        self.assertIsNone(self.decoder._decoder)

    def test_byte_duration_and_sample_alignment_bounds(self):
        for packet, reason in (
            (audio_packet(b'', codec=4), 'audio_packet_size'),
            (audio_packet(bytes(audio.MAX_AUDIO_PAYLOAD + 1)), 'audio_packet_size'),
            (audio_packet(bytes(1601)), 'audio_packet_duration'),
            (audio_packet(bytes(3), codec=9), 'audio_sample_alignment'),
            (audio_packet(bytes(3), channels=2), 'audio_sample_alignment'),
        ):
            with self.subTest(reason=reason), self.assertRaisesRegex(
                    audio.AudioDecodeError, '^' + reason + '$'):
                self.decoder.feed(packet)
        self.assertEqual(self.decoder.diagnostics['decode_errors'], 5)
        self.assertEqual(self.decoder.diagnostics['unsupported_packets'], 0)
        self.assertIsNone(self.decoder._decoder)

    def test_malformed_and_truncated_aac_fail_fixed_then_recover(self):
        valid = synthetic_aac()[0]
        for bad in (b'PRIVATE_MALFORMED_AAC', valid[:9]):
            with self.subTest(length=len(bad)), self.assertRaisesRegex(
                    audio.AudioDecodeError, '^audio_decode_error$'):
                self.decoder.feed(audio_packet(bad, codec=8))
        self.assertEqual(self.decoder.diagnostics['decode_errors'], 2)
        self.assertEqual(self.decoder.frames, 0)
        self.assertIsNone(self.decoder._decoder)
        self.assertTrue(self.decoder.feed(audio_packet(valid, codec=8)))
        self.assertIsNone(self.decoder.diagnostics['last_error_reason'])
        self.assertNotIn('PRIVATE', json.dumps(self.decoder.diagnostics))

    def test_aac_output_must_match_declared_format(self):
        payload = synthetic_aac(rate=16000)[0]
        with self.assertRaisesRegex(audio.AudioDecodeError, '^decoded_audio_format$'):
            self.decoder.feed(audio_packet(payload, codec=8, rate=8000))
        self.assertEqual(self.decoder.frames, 0)
        self.assertEqual(self.decoder.diagnostics['decoded_samples'], 0)

    def test_aac_multiple_frames_over_duration_are_not_delivered(self):
        payload = synthetic_aac()[0] * 3
        with self.assertRaisesRegex(audio.AudioDecodeError, '^decoded_audio_limit$'):
            self.decoder.feed(audio_packet(payload, codec=8))
        self.assertEqual(self.decoder.frames, 0)
        self.assertEqual(self.decoder._clock, 0)

    def test_decoded_sample_and_frame_limits_before_any_delivery(self):
        packet = audio_packet(b'SYNTHETIC_AAC', codec=8)
        for count, samples in ((17, 1), (1, 1601)):
            with self.subTest(count=count, samples=samples):
                frame = av.AudioFrame(format='s16', layout='mono', samples=samples)
                frame.sample_rate = 8000
                fake = Mock(decode=Mock(return_value=[frame] * count))
                self.decoder._decoder, self.decoder._format = fake, (8, 8000, 1)
                with self.assertRaisesRegex(audio.AudioDecodeError, '^decoded_audio_limit$'):
                    self.decoder.feed(packet)
                self.assertEqual(self.decoder.frames, 0)
                self.assertEqual(self.decoder._clock, 0)

    def test_diagnostics_privacy_fixed_errors_and_idempotent_close(self):
        self.decoder.feed(audio_packet(b'PRIVATE_PCM_BYTES!' + b'\xd5' * 143, codec=4))
        diagnostic = json.dumps(self.decoder.diagnostics)
        self.assertNotIn('PRIVATE', diagnostic)
        self.assertNotIn(b'PRIVATE_PCM_BYTES!'.hex(), diagnostic)
        self.assertNotIn('payload', diagnostic)
        self.decoder.close()
        self.decoder.close()
        self.assertTrue(self.decoder.diagnostics['closed'])
        self.assertEqual(self.decoder.diagnostics['decoded_frames'], 1)
        with self.assertRaisesRegex(audio.AudioDecodeError, '^audio_decoder_closed$'):
            self.decoder.feed(audio_packet(b'\xd5'))


if __name__ == '__main__':
    unittest.main()
