"""Executable regressions, written against beta.7 before production fixes."""
import ast
import asyncio
from pathlib import Path
import struct
import types
import unittest
from unittest.mock import patch

from beta8_helpers import entry, load_hub, load_rtc
from test_v1_video_receive import media


class CleanupReproductions(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_continues_after_one_listener_fails(self):
        hub = load_hub().WelcomeEyeHub(types.SimpleNamespace(), entry())
        calls = []
        async def broken():
            raise RuntimeError('listener failed')
        async def halt(): calls.append('halt')
        class Server:
            def close(self): calls.append('server')
            async def wait_closed(self): pass
        hub.close_listeners.add(broken)
        hub.server = Server()
        hub._halt_media = halt
        with self.assertRaises(Exception):
            await hub.stop()
        self.assertIn('server', calls)
        self.assertIn('halt', calls)

    async def test_http_writer_and_handler_close_even_when_release_fails(self):
        module = load_hub()
        hub = module.WelcomeEyeHub(types.SimpleNamespace(), entry())
        class Reader:
            async def readuntil(self, separator):
                return b'GET /wrong HTTP/1.1\r\n\r\n'
        class Writer:
            closed = False
            def write(self, data): pass
            async def drain(self): pass
            def close(self): self.closed = True
            async def wait_closed(self): pass
        async def broken_release(consumer, **kwargs):
            raise RuntimeError('release failed')
        hub.release = broken_release
        writer = Writer()
        with self.assertRaisesRegex(RuntimeError, 'release failed'):
            await hub._serve(Reader(), writer)
        self.assertTrue(writer.closed)
        self.assertEqual(hub.handlers, set())

    async def test_pipeline_container_closes_even_when_audio_flush_fails(self):
        pipeline = media.MediaPipeline.__new__(media.MediaPipeline)
        class Encoder:
            def encode(self, frame): raise RuntimeError('flush failed')
        class Output:
            closed = False
            def close(self): self.closed = True
        pipeline.audio, pipeline.output = Encoder(), Output()
        with self.assertRaisesRegex(RuntimeError, 'flush failed'):
            pipeline.close()
        self.assertTrue(pipeline.output.closed)


class DNSWireReproductions(unittest.IsolatedAsyncioTestCase):
    def preload_function(self):
        path = Path(__file__).parents[1] / 'custom_components/welcomeeye_local/__init__.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == '_preload_dns_types')
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
        return namespace['_preload_dns_types']

    async def test_actual_aioice_srv_datagram_after_production_preload_no_loop_import(self):
        import dns.rdata
        import dns.rdatatype
        from aioice.mdns import MDnsProtocol
        # A real cache-flush class SRV answer; no socket and no live addresses.
        name = b'\x04test\x05local\x00'
        data = struct.pack('!HHH', 0, 0, 80) + name
        wire = struct.pack('!6H', 0, 0x8400, 0, 1, 0, 0)
        wire += name + struct.pack('!HHIH', 33, 32769, 120, len(data)) + data
        key = (32769, dns.rdatatype.SRV)
        previous = dict(dns.rdata._rdata_classes)
        try:
            dns.rdata._rdata_classes.pop(key, None)
            dns.rdata._rdata_classes.pop((255, dns.rdatatype.SRV), None)
            await asyncio.to_thread(lambda: dns.rdata.load_all_types(disable_dynamic_load=False))
            with patch.object(dns.rdata, 'import_module', wraps=dns.rdata.import_module) as imports:
                dns.message.from_wire(wire)
            self.assertIn('dns.rdtypes.CLASS32769.SRV', [c.args[0] for c in imports.call_args_list])
            self.assertIn('dns.rdtypes.ANY.SRV', [c.args[0] for c in imports.call_args_list])
            dns.rdata._rdata_classes.pop(key, None)
            await asyncio.to_thread(self.preload_function())
            protocol = MDnsProtocol(types.SimpleNamespace(sendto=lambda *args: None))
            with patch.object(dns.rdata, 'import_module', side_effect=AssertionError('blocking import')):
                protocol.datagram_received(wire, ('unused', 5353))
            self.assertTrue(dns.rdata._dynamic_load_allowed)
            protocol.connection_lost(None)
        finally:
            dns.rdata._rdata_classes.clear()
            dns.rdata._rdata_classes.update(previous)


class ViewerRaceReproductions(unittest.IsolatedAsyncioTestCase):
    async def test_no_answer_after_viewer_closed_during_local_description(self):
        rtc = load_rtc()
        entered, proceed = asyncio.Event(), asyncio.Event()
        leases = set()
        class PC:
            connectionState = 'new'
            iceConnectionState = 'new'
            iceGatheringState = 'new'
            signalingState = 'stable'
            localDescription = types.SimpleNamespace(sdp='v=0\r\n')
            def __init__(self, configuration): self.events = {}
            def on(self, event):
                def register(fn): self.events[event] = fn; return fn
                return register
            async def setRemoteDescription(self, description): pass
            def getTransceivers(self):
                return [types.SimpleNamespace(kind='video', direction='recvonly')]
            def addTrack(self, track): pass
            async def createAnswer(self): return self.localDescription
            async def setLocalDescription(self, description):
                entered.set()
                await proceed.wait()
            async def close(self): self.connectionState = 'closed'
        async def acquire(lease): leases.add(lease)
        async def release(lease, **kwargs): leases.discard(lease)
        hub = types.SimpleNamespace(hass=types.SimpleNamespace(ice_servers=[]), stopped=False,
            frame_listeners=set(), close_listeners=set(), webrtc_diagnostics={},
            acquire=acquire, release=release)
        manager = rtc.WebRTCManager(hub)
        messages = []
        with patch.object(rtc, 'RTCPeerConnection', PC):
            task = asyncio.create_task(manager.offer('v=0', 'viewer', messages.append))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                await manager.close('viewer')
                proceed.set()
                await task
                self.assertEqual(messages, [])
                self.assertEqual(leases, set())
                self.assertEqual(manager.viewers, {})
            finally:
                proceed.set()
                await asyncio.gather(task, return_exceptions=True)
                await manager.close_all()
