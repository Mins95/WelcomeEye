"""Production lifecycle methods with synthetic HA, peer and media boundaries.

No device, ICE negotiation, socket or relay command is used. These tests cover
ownership across callbacks; the separate local-stream suite covers real loopback
sockets and the codec suites cover media. They do not validate an HA runtime.
"""
import ast
import asyncio
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import struct
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / 'custom_components/welcomeeye_local'


def definitions(name, **namespace):
    """Load unmodified definitions without requiring Home Assistant/aiortc."""
    path = SOURCE / f'{name}.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body
                 if not isinstance(node, (ast.Import, ast.ImportFrom))]
    module = ModuleType(f'_media_lifecycle_fixture_{name}')
    sys.modules[module.__name__] = module
    module.__dict__.update(namespace)
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    return module


class TrackBase:
    readyState = 'live'

    def stop(self):
        self.readyState = 'ended'


class Peer:
    """Controllable signaling boundary; never creates a network transport."""
    connectionState = 'new'
    iceConnectionState = 'new'
    iceGatheringState = 'new'
    signalingState = 'stable'

    def __init__(self, configuration):
        self.events = {}
        self.remote_entered = asyncio.Event()
        self.remote_gate = None
        self.close_count = 0

    def on(self, event):
        def register(callback):
            self.events[event] = callback
            return callback
        return register

    async def setRemoteDescription(self, description):
        self.remote_entered.set()
        if self.remote_gate is not None:
            await self.remote_gate.wait()

    def getTransceivers(self):
        return [SimpleNamespace(kind='video', direction='recvonly')]

    def addTrack(self, track):
        pass

    async def createAnswer(self):
        return SimpleNamespace(sdp='synthetic answer')

    async def setLocalDescription(self, answer):
        self.localDescription = answer

    async def close(self):
        self.close_count += 1
        self.connectionState = self.iceConnectionState = 'closed'


class Hub:
    def __init__(self):
        self.stopped = False
        self.connected = True
        self.capabilities = SimpleNamespace(talkback=True, downstream_audio=False)
        self.hass = SimpleNamespace()
        self.entry = SimpleNamespace(unique_id='synthetic', title='Synthetic', data={})
        self.device_model = 'Synthetic'
        self.frame_listeners = set()
        self.close_listeners = set()
        self.webrtc_diagnostics = {}
        self.consumers = set()
        self.acquire_entered = asyncio.Event()
        self.acquire_gate = None
        self.release_entered = asyncio.Event()
        self.release_gate = None
        self.released = []
        self.session = SimpleNamespace(closed=threading.Event(), talk_enabled=False,
                                       stop_talk=Mock())

    async def acquire(self, lease):
        self.consumers.add(lease)
        self.acquire_entered.set()
        if self.acquire_gate is not None:
            await self.acquire_gate.wait()

    async def release(self, lease, *, reason=None):
        self.release_entered.set()
        if self.release_gate is not None:
            await self.release_gate.wait()
        self.consumers.discard(lease)
        self.released.append(lease)


class Entity:
    async def async_will_remove_from_hass(self):
        pass


class Source:
    """Camera source ownership only; actual socket lifetime has its own suite."""
    def __init__(self, hub):
        self.closed = False
        self.url = f'synthetic-source-{id(self)}'

    async def source(self):
        return self.url

    async def close(self):
        self.closed = True


class MediaLifecycleRegressions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.talk = definitions('talkback', asyncio=asyncio, dataclass=dataclass,
                                struct=struct, time=time, ProtocolError=ValueError)
        self.rtc = definitions('rtc', asyncio=asyncio, dataclass=dataclass, field=field,
            logging=logging, json=json, time=time, MediaStreamTrack=TrackBase,
            RTCPeerConnection=Peer, RTCConfiguration=SimpleNamespace,
            RTCIceServer=SimpleNamespace, RTCSessionDescription=SimpleNamespace,
            WebRTCAnswer=SimpleNamespace, WebRTCError=SimpleNamespace,
            TalkRefused=self.talk.TalkRefused,
            UnsupportedTalkFormat=self.talk.UnsupportedTalkFormat,
            async_get_ice_servers=lambda hass: [], protect_peer_ice=lambda *args: None)
        self.hub = Hub()
        self.hub.talkback = self.talk.Talkback(self.hub)
        self.manager = self.rtc.WebRTCManager(self.hub)
        self.addAsyncCleanup(self.manager.close_all)

    async def test_reused_session_id_closes_new_viewer_after_previous_cleanup(self):
        await self.manager.offer('synthetic', 'same-id', Mock())
        old = self.manager.viewers['same-id']
        await self.manager.close('same-id')
        self.assertFalse(self.hub.consumers)
        await self.manager.offer('synthetic', 'same-id', Mock())
        replacement = self.manager.viewers['same-id']
        self.assertIsNot(replacement, old)
        self.assertEqual(self.hub.consumers, {replacement.lease})
        # A delayed old ICE callback must not close the replacement.
        await self.manager.close('same-id', expected=old)
        self.assertIs(self.manager.viewers['same-id'], replacement)
        await self.manager.close('same-id')
        self.assertNotIn('same-id', self.manager.viewers)
        self.assertFalse(self.hub.consumers)
        self.assertEqual((old.pc.close_count, replacement.pc.close_count), (1, 1))

    async def test_session_id_cannot_reopen_while_old_cleanup_is_pending(self):
        await self.manager.offer('synthetic', 'same-id', Mock())
        old = self.manager.viewers['same-id']
        self.hub.release_gate = asyncio.Event()
        closing = asyncio.create_task(self.manager.close('same-id'))
        try:
            await asyncio.wait_for(self.hub.release_entered.wait(), 1)
            messages = Mock()
            await self.manager.offer('synthetic', 'same-id', messages)
            self.assertEqual(getattr(messages.call_args.args[0], 'code', None), 'busy')
            self.assertNotIn('same-id', self.manager.viewers)
            self.assertEqual(self.hub.consumers, {old.lease})
        finally:
            self.hub.release_gate.set()
            await closing
        self.assertFalse(self.hub.consumers)

    async def test_cancelled_offer_releases_only_its_lease_preserving_second_viewer_microphone(self):
        await self.manager.offer('synthetic', 'microphone-owner', Mock())
        owner = self.manager.viewers['microphone-owner']
        talk = self.hub.talkback
        talk.owner, talk.session, talk.active = owner, self.hub.session, True
        self.hub.session.talk_enabled = True
        self.hub.acquire_gate = asyncio.Event()
        self.hub.acquire_entered.clear()
        pending = asyncio.create_task(self.manager.offer('synthetic', 'interrupted', Mock()))
        await asyncio.wait_for(self.hub.acquire_entered.wait(), 1)
        interrupted = self.manager.viewers['interrupted']
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 1)
        self.assertEqual(self.hub.consumers, {owner.lease})
        self.assertIs(talk.owner, owner)
        self.assertTrue(talk.active)
        self.assertTrue(self.hub.session.talk_enabled)
        self.hub.session.stop_talk.assert_not_called()
        self.assertEqual(interrupted.pc.close_count, 1)
        self.assertTrue(interrupted.timeout.done())
        self.assertFalse(self.manager.offers)
        await self.manager.close('microphone-owner')
        self.assertFalse(talk.active)
        self.assertFalse(self.hub.consumers)
        self.hub.session.stop_talk.assert_called_once()

    async def test_camera_unload_cancels_negotiation_and_reload_has_fresh_owners(self):
        entity = definitions('entity', Entity=Entity, DeviceInfo=lambda **kw: kw,
                             DOMAIN='welcomeeye_local', callback=lambda fn: fn)
        camera = definitions('camera', Camera=type('Camera', (), {}),
            CameraEntityFeature=SimpleNamespace(STREAM=1), UNDEFINED=object(),
            WelcomeEyeEntity=entity.WelcomeEyeEntity, WebRTCManager=self.rtc.WebRTCManager,
            LocalVideoStream=Source, callback=lambda fn: fn)
        # The setup manager is unrelated to this camera's owned callbacks.
        await self.manager.close_all()
        old = camera.WelcomeEyeConnect3Camera(self.hub)
        await old.stream_source()
        stream = self.hub._local_video_stream
        peer = Peer(None)
        peer.remote_gate = asyncio.Event()
        self.rtc.RTCPeerConnection = lambda configuration: peer
        negotiation = asyncio.create_task(old.rtc.offer('synthetic', 'pending', Mock()))
        await asyncio.wait_for(peer.remote_entered.wait(), 1)
        await asyncio.wait_for(old.async_will_remove_from_hass(), 1)
        with self.assertRaises(asyncio.CancelledError):
            await negotiation
        self.assertTrue(stream.closed)
        self.assertIsNone(self.hub._local_video_stream)
        self.assertFalse(self.hub.frame_listeners)
        self.assertFalse(self.hub.close_listeners)
        self.assertFalse(self.hub.consumers)
        self.assertEqual(peer.close_count, 1)
        self.assertIsNone(await old.stream_source())
        self.rtc.RTCPeerConnection = Peer
        new = camera.WelcomeEyeConnect3Camera(self.hub)
        self.addAsyncCleanup(new.async_will_remove_from_hass)
        self.assertEqual(new._attr_unique_id, old._attr_unique_id)
        self.assertNotEqual(await new.stream_source(), stream.url)
        await new.rtc.offer('synthetic', 'after-reload', Mock())
        self.assertEqual(len(self.hub.consumers), 1)
        await new.async_will_remove_from_hass()
        self.assertFalse(self.hub.consumers)
        self.assertFalse(self.hub.frame_listeners)
        self.assertFalse(self.hub.close_listeners)


if __name__ == '__main__':
    unittest.main()
