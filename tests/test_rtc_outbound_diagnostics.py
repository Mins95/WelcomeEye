"""On-demand aiortc sender statistics in the protected diagnostics download."""
import asyncio
from dataclasses import dataclass, field
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from test_media_lifecycle_regressions import Hub, Peer, TrackBase, definitions


rtc = definitions('rtc', asyncio=asyncio, dataclass=dataclass, field=field,
    logging=logging, RTCPeerConnection=Peer, MediaStreamTrack=TrackBase,
    RTCSessionDescription=SimpleNamespace, RTCConfiguration=SimpleNamespace,
    RTCIceServer=SimpleNamespace, WebRTCAnswer=SimpleNamespace,
    protect_peer_ice=lambda *args: None, async_get_ice_servers=lambda hass: [])
diagnostics = definitions('diagnostics', VERSION='test',
    cloud_enrollment_snapshot=lambda *args: {})


def video(**fields):
    return SimpleNamespace(type='outbound-rtp', kind='video', ssrc=12345,
                           trackId='PRIVATE_TRACK_ID', **fields)


def viewer(*rows, error=None):
    report = {f'PRIVATE_REPORT_{n}': row for n, row in enumerate(rows)}
    sender = SimpleNamespace(getStats=AsyncMock(return_value=report, side_effect=error))
    return rtc.Viewer(pc=Mock(), video_sender=sender)


class OutboundVideoDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.hub = Hub()
        self.manager = rtc.WebRTCManager(self.hub)

    async def test_download_samples_only_existing_attached_video_senders_by_channel(self):
        peer = Peer(None)
        outbound = viewer(video(framesEncoded=5, framesSent=3)).video_sender
        peer.addTrack = Mock(return_value=outbound)
        self.hub.capabilities = SimpleNamespace(connect3_read=True, r002_probe=False,
            as_dict=lambda: {}, talkback=False, downstream_audio=False)
        self.hub.protocol_family = self.hub.variant = SimpleNamespace(value='test')
        self.hub.diagnostics = lambda: {'existing': True}
        self.hub.channel2 = Hub()
        second = rtc.WebRTCManager(self.hub.channel2)
        second.viewers['PRIVATE_OTHER'] = viewer(video(framesEncoded=0, framesSent=0))
        entry = SimpleNamespace(runtime_data=self.hub, domain='welcomeeye_local', entry_id='test')
        with unittest.mock.patch.object(rtc, 'RTCPeerConnection', return_value=peer):
            await self.manager.offer('synthetic SDP', 'PRIVATE_SESSION', Mock())
        try:
            outbound.getStats.assert_not_awaited()
            leases = set(self.hub.consumers)
            result = await diagnostics.async_get_config_entry_diagnostics(self.hub.hass, entry)
            self.assertEqual(result['webrtc_outbound_video'], {
                '1': {'frames_encoded': 5, 'frames_sent': 3},
                '2': {'frames_encoded': 0, 'frames_sent': 0}})
            self.assertEqual(result['connect3'], {'existing': True})
            self.assertNotIn('PRIVATE', repr(result))
            self.assertEqual(self.hub.consumers, leases)
            self.assertEqual(peer.close_count, 0)
            outbound.getStats.assert_awaited_once_with()
        finally:
            await self.manager.close_all()
        self.assertIsNone(self.hub._webrtc_manager)
        self.assertEqual(self.hub.consumers, set())

    async def test_aggregate_filters_streams_saturates_and_preserves_unknown(self):
        self.manager.viewers = {
            'one': viewer(SimpleNamespace(type='local-candidate', address='PRIVATE_ADDRESS'),
                          SimpleNamespace(type='inbound-rtp', kind='video', framesEncoded=800),
                          SimpleNamespace(type='outbound-rtp', kind='audio', framesSent=900),
                          video(framesEncoded=8, framesSent=5)),
            'two': viewer(video(framesEncoded=7, framesSent=6)),
            'audio-only': rtc.Viewer(pc=Mock())}
        expected = {'frames_encoded': 15, 'frames_sent': 11}
        self.assertEqual(await self.manager.outbound_video_stats(), expected)
        self.assertEqual(await self.manager.outbound_video_stats(), expected)
        for invalid in (None, -1, True, 2.5, 'PRIVATE_VALUE'):
            with self.subTest(invalid=invalid):
                self.manager.viewers['two'] = viewer(video(framesEncoded=2**40, framesSent=invalid))
                self.assertEqual(await self.manager.outbound_video_stats(), {
                    'frames_encoded': 2**31 - 1, 'frames_sent': None})
        self.manager.viewers['two'] = viewer(error=RuntimeError('PRIVATE_NETWORK_DETAIL'))
        self.assertEqual(await self.manager.outbound_video_stats(), {
            'frames_encoded': None, 'frames_sent': None})

    async def test_empty_audio_only_and_replaced_viewers_produce_no_stale_stats(self):
        self.assertEqual(await diagnostics._webrtc_outbound_video(self.hub), {})
        self.manager.viewers['same-id'] = rtc.Viewer(pc=Mock())
        self.assertIsNone(await self.manager.outbound_video_stats())

        async def replace():
            self.manager.viewers['same-id'] = viewer(video(framesEncoded=4, framesSent=3))
            return {'PRIVATE_OLD_ID': video(framesEncoded=400, framesSent=300)}

        self.manager.viewers['same-id'] = viewer(error=replace)
        self.assertIsNone(await self.manager.outbound_video_stats())
        self.assertEqual(await diagnostics._webrtc_outbound_video(self.hub), {
            '1': {'frames_encoded': 4, 'frames_sent': 3}})
        self.assertEqual(self.hub.consumers, set())

    async def test_slow_sender_times_out_without_mutating_the_viewer(self):
        waiting = asyncio.Event()
        self.manager.viewers['one'] = slow = viewer(error=waiting.wait)
        self.assertEqual(await asyncio.wait_for(self.manager.outbound_video_stats(), 3), {
            'frames_encoded': None, 'frames_sent': None})
        self.assertIs(self.manager.viewers['one'], slow)
        slow.pc.close.assert_not_called()


if __name__ == '__main__':
    unittest.main()
