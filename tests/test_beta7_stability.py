"""Beta 7 DNS/mDNS and H.264 stability regressions; fully offline."""
from pathlib import Path
import unittest
from unittest.mock import patch

import dns.rdata
import dns.rdataclass
import dns.rdatatype

from test_v1_video_receive import media, synthetic_h264

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "custom_components/welcomeeye_local/__init__.py"


class DNSPreloadTests(unittest.TestCase):
    def test_exact_mdns_class_is_cached_without_later_dynamic_import(self):
        mdns_class = int(dns.rdataclass.IN) | 0x8000
        dns.rdata.load_all_types(disable_dynamic_load=False)
        for rdtype in dns.rdatatype.RdataType:
            dns.rdata.get_rdata_class(mdns_class, rdtype, True)

        with patch.object(
            dns.rdata,
            "import_module",
            side_effect=AssertionError("dynamic DNS import after preload"),
        ):
            for rdtype in (
                dns.rdatatype.A,
                dns.rdatatype.AAAA,
                dns.rdatatype.PTR,
                dns.rdatatype.SRV,
                dns.rdatatype.TXT,
                dns.rdatatype.NSEC,
            ):
                self.assertIsNotNone(dns.rdata.get_rdata_class(mdns_class, rdtype, True))

    def test_production_source_warms_all_exact_mdns_types_in_executor(self):
        source = INIT.read_text()
        self.assertIn("mdns_rdclass = 1 | 0x8000", source)
        self.assertIn("for rdtype in dns.rdatatype.RdataType", source)
        self.assertIn("dns.rdata.get_rdata_class(mdns_rdclass, rdtype, True)", source)
        preload = "await hass.async_add_executor_job(_preload_dns_types)"
        hub = "hub = WelcomeEyeHub(hass, entry)"
        platforms = "await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)"
        self.assertLess(source.index(preload), source.index(hub))
        self.assertLess(source.index(preload), source.index(platforms))

    def test_preload_strategy_does_not_disable_dynamic_loading_globally(self):
        dns.rdata.load_all_types(disable_dynamic_load=False)
        self.assertTrue(dns.rdata._dynamic_load_allowed)


class FailingDecoder:
    def parse(self, _body):
        return [object()]

    def decode(self, _part):
        raise media.av.InvalidDataError(1094995529, "Invalid data")


class UnexpectedDecoderFailure:
    def parse(self, _body):
        raise RuntimeError("unexpected decoder failure")


class H264RecoveryTests(unittest.TestCase):
    def new_pipeline(self):
        self.ts = []
        self.images = []
        self.frames = []
        return media.MediaPipeline(
            media.StreamFormat(352, 288, 20, 8000, 0x7A19, 1),
            self.ts.append,
            self.images.append,
            lambda kind, frame: self.frames.append((kind, frame)),
        )

    def test_invalid_keyframe_is_dropped_then_fresh_keyframe_recovers(self):
        first = synthetic_h264()
        fresh = synthetic_h264()
        pipeline = self.new_pipeline()
        try:
            pipeline.decoder = FailingDecoder()
            self.assertFalse(pipeline.feed_video(first[0][1], keyframe=True))
            self.assertFalse(pipeline.started)
            self.assertTrue(pipeline.video_waiting_for_keyframe)
            self.assertEqual(pipeline.video_decode_errors, 1)
            self.assertEqual(pipeline.video_decoder_resets, 1)
            self.assertEqual(pipeline.video_pts, 0)
            self.assertFalse(pipeline.feed_video(first[1][1], keyframe=False))
            self.assertEqual(pipeline.video_dropped_until_keyframe, 1)
            self.assertTrue(pipeline.feed_video(fresh[0][1], keyframe=True))
            for _kind, body in fresh[1:]:
                pipeline.feed_video(body, keyframe=False)
            self.assertTrue(pipeline.started)
            self.assertFalse(pipeline.video_waiting_for_keyframe)
            self.assertTrue(self.frames)
            self.assertTrue(self.images and self.images[0].startswith(b"\xff\xd8"))
        finally:
            pipeline.close()

    def test_invalid_p_frame_does_not_end_started_pipeline(self):
        packets = synthetic_h264()
        fresh = synthetic_h264()
        pipeline = self.new_pipeline()
        try:
            self.assertTrue(pipeline.feed_video(packets[0][1], keyframe=True))
            pts_before = pipeline.video_pts
            pipeline.decoder = FailingDecoder()
            self.assertFalse(pipeline.feed_video(packets[1][1], keyframe=False))
            self.assertTrue(pipeline.started)
            self.assertTrue(pipeline.video_waiting_for_keyframe)
            self.assertEqual(pipeline.video_pts, pts_before)
            self.assertFalse(pipeline.feed_video(packets[2][1], keyframe=False))
            self.assertTrue(pipeline.feed_video(fresh[0][1], keyframe=True))
            self.assertFalse(pipeline.video_waiting_for_keyframe)
        finally:
            pipeline.close()

    def test_only_invalid_data_is_recovered(self):
        packets = synthetic_h264()
        pipeline = self.new_pipeline()
        try:
            pipeline.decoder = UnexpectedDecoderFailure()
            with self.assertRaisesRegex(RuntimeError, "unexpected decoder failure"):
                pipeline.feed_video(packets[0][1], keyframe=True)
            self.assertEqual(pipeline.video_decode_errors, 0)
            self.assertEqual(pipeline.video_decoder_resets, 0)
        finally:
            pipeline.close()


if __name__ == "__main__":
    unittest.main()
