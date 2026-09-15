"""RTC lifecycle stress and real aiortc SDP/PyAV frames; no device traffic."""
import asyncio
from fractions import Fraction
import types
from unittest.mock import patch

import av
import pytest

from beta8_helpers import load_rtc
from test_beta8_lifecycle import generated_video
from test_v1_video_receive import media


class PC:
    connectionState = iceConnectionState = iceGatheringState = 'new'
    signalingState = 'stable'
    localDescription = types.SimpleNamespace(sdp='v=0\r\n')

    def __init__(self, configuration):
        self.events = {}
        self.tracks = []
        self.candidates = []
        self.closed_count = 0

    def on(self, event):
        def register(fn): self.events[event] = fn; return fn
        return register

    async def setRemoteDescription(self, description): pass
    def getTransceivers(self):
        return [types.SimpleNamespace(kind=k, direction='recvonly') for k in ('video', 'audio')]
    def addTrack(self, track): self.tracks.append(track)
    async def createAnswer(self): return self.localDescription
    async def setLocalDescription(self, description): pass
    async def addIceCandidate(self, candidate): self.candidates.append(candidate)
    async def close(self):
        self.closed_count += 1
        self.connectionState = self.iceConnectionState = 'closed'
        await self.events['connectionstatechange']()


def setup():
    leases = set()
    async def acquire(lease): leases.add(lease)
    async def release(lease, **kwargs): leases.discard(lease)
    hub = types.SimpleNamespace(hass=types.SimpleNamespace(ice_servers=[]),
        stopped=False, frame_listeners=set(), close_listeners=set(),
        webrtc_diagnostics={}, acquire=acquire, release=release)
    rtc = load_rtc()
    return rtc, hub, leases, rtc.WebRTCManager(hub)


def test_fifty_viewers_and_late_callback_cannot_close_replacement():
    async def run():
        rtc, hub, leases, manager = setup()
        baseline = asyncio.all_tasks()
        old = []
        messages = []
        with patch.object(rtc, 'RTCPeerConnection', PC):
            for _ in range(50):
                await manager.offer('v=0', 'same-id', messages.append)
                current = manager.viewers['same-id']
                if old:
                    await old[-1].pc.events['connectionstatechange']()
                    assert manager.viewers['same-id'] is current
                old.append(current)
                await manager.close('same-id')
                assert not manager.viewers and not leases
                assert current.pc.closed_count == 1
                assert all(t.readyState == 'ended' for t in current.tracks.values())
            await manager.close_all()
        await asyncio.sleep(0)
        assert not manager.tasks and not manager.offers
        assert asyncio.all_tasks() == baseline
        assert len(messages) == 50
    asyncio.run(run())


def test_multiple_viewers_bounded_independent_frames_and_candidates():
    async def run():
        rtc, hub, leases, manager = setup()
        with patch.object(rtc, 'RTCPeerConnection', PC):
            await manager.offer('v=0', 'one', lambda m: None)
            await manager.offer('v=0', 'two', lambda m: None)
            first, second = manager.viewers.values()
            frame = av.VideoFrame(720, 576, 'yuv420p')
            frame.pts, frame.time_base = 1, Fraction(1, 20)
            for _ in range(100): manager._frame('video', frame)
            assert first.tracks['video'].queue.qsize() == 2
            assert second.tracks['video'].queue.qsize() == 2
            a, b = await asyncio.gather(first.tracks['video'].recv(), second.tracks['video'].recv())
            assert a is not b and a is not frame
            assert (a.width, a.height, a.pts, a.time_base) == (720, 576, 1, Fraction(1, 20))
            # No addresses enter diagnostics. End-of-candidates stays supported.
            await manager.candidate('one', types.SimpleNamespace(candidate=''))
            assert first.pc.candidates == [None]
            await manager.close('one')
            await manager.candidate('one', types.SimpleNamespace(candidate=''))
            assert len(leases) == 1
            assert second.tracks['video'].readyState == 'live'
            await manager.close_all()
            assert not leases and not manager.tasks and not manager.viewers
    asyncio.run(run())


def test_cancelled_close_still_finishes_release_and_pc_close():
    async def run():
        rtc, hub, leases, manager = setup()
        entered, finish = asyncio.Event(), asyncio.Event()
        async def release(lease, **kwargs):
            entered.set()
            await finish.wait()
            leases.discard(lease)
        hub.release = release
        with patch.object(rtc, 'RTCPeerConnection', PC):
            await manager.offer('v=0', 'one', lambda m: None)
            viewer = manager.viewers['one']
            task = asyncio.create_task(manager.close('one'))
            await entered.wait()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            finish.set()
            await manager.close_all()
            assert viewer.pc.closed_count == 1
            assert not leases and not manager.tasks
    asyncio.run(run())


def test_unload_cancels_negotiation_without_orphan_timeout():
    async def run():
        rtc, hub, leases, manager = setup()
        entered = asyncio.Event()
        class SlowPC(PC):
            async def setLocalDescription(self, description):
                entered.set()
                await asyncio.Event().wait()
        messages = []
        baseline = asyncio.all_tasks()
        with patch.object(rtc, 'RTCPeerConnection', SlowPC):
            task = asyncio.create_task(manager.offer('v=0', 'one', messages.append))
            await entered.wait()
            await manager.close_all()
            assert task.cancelled()
            assert not messages and not leases and not manager.tasks and not manager.offers
            await asyncio.sleep(0)
            assert asyncio.all_tasks() == baseline
    asyncio.run(run())


def test_real_aiortc_sdp_answer_and_close_without_stun_or_remote_candidates():
    async def run():
        rtc, hub, leases, manager = setup()
        remote = rtc.RTCPeerConnection(rtc.RTCConfiguration(iceServers=[]))
        remote.addTransceiver('video', direction='recvonly')
        remote.addTransceiver('audio', direction='recvonly')
        offer = await remote.createOffer()
        messages = []
        with patch.object(rtc, '_ice_configuration', return_value=(rtc.RTCConfiguration(iceServers=[]), {})):
            try:
                await manager.offer(offer.sdp, 'one', messages.append)
                assert len(messages) == 1 and hasattr(messages[0], 'answer')
                assert 'm=video' in messages[0].answer and 'm=audio' in messages[0].answer
                # No remote candidates are exchanged: validate real answer
                # generation without triggering connectivity to any endpoint.
                from aiortc.sdp import SessionDescription
                parsed = SessionDescription.parse(messages[0].answer)
                assert {m.kind for m in parsed.media} == {'video', 'audio'}
                viewer = manager.viewers['one']
                assert viewer.pc.signalingState == 'stable'
                assert set(viewer.tracks) == {'video', 'audio'}
            finally:
                await manager.close_all()
                await remote.close()
            assert viewer.pc.connectionState == 'closed'
            assert not leases and not manager.tasks and not manager.offers
    asyncio.run(run())


def test_turn_settings_preserved_but_secrets_absent_from_diagnostics():
    rtc = load_rtc()
    servers = [types.SimpleNamespace(urls=['stun:example.invalid', 'turn:example.invalid?transport=udp',
        'turns:example.invalid?transport=tcp'], username='secret-user', credential='secret-password')]
    config, diag = rtc._ice_configuration(types.SimpleNamespace(ice_servers=servers))
    assert config.iceServers[0].username == 'secret-user'
    assert config.iceServers[0].credential == 'secret-password'
    assert diag['stun_server_count'] == 1 and diag['turn_server_count'] == 2
    assert diag['turn_available']
    assert 'secret' not in str(diag) and 'example' not in str(diag)
    types_, protocols = rtc._candidate_metadata_from_sdp('a=candidate:x 1 SECRET 1 host.invalid 1 typ SECRET')
    assert types_ == protocols == []


@pytest.mark.parametrize('size', [(352, 288), (720, 576)])
def test_fifty_real_pipeline_cycles_audio_video_ts_jpeg(size):
    packets = generated_video(*size)
    for _ in range(50):
        chunks, images, frames = [], [], []
        pipeline = media.MediaPipeline(media.StreamFormat(*size, 20, 8000, 0x7a19, 1),
            chunks.append, images.append, lambda kind, frame: frames.append((kind, frame)))
        for kind, data in packets:
            assert pipeline.feed_video(data, keyframe=kind == 100)
            pipeline.feed_audio(b'\xd5' * 160)
        pipeline.close()
        pipeline.close()  # idempotence, no second encoder flush
        assert chunks and all(len(chunk) % 188 == 0 for chunk in chunks)
        assert images and images[0].startswith(b'\xff\xd8')
        assert any(kind == 'video' for kind, _ in frames)
        assert any(kind == 'audio' for kind, _ in frames)
        for kind, frame in frames:
            if kind == 'video': assert (frame.width, frame.height) == size
