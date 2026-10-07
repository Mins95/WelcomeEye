"""Exercise the HA runtime's synthetic peer against real QV, decoders and talk."""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from load_integration import load

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from qv_runtime_support import OPENING_CODE, PASSWORD, STREAM_KEY, SyntheticQVPeer
from test_connect3_talk import pcm

session = load('connect3.session')
live = load('connect3.live')
talk = load('connect3.talk')
cgi = load('connect3.cgi')
video = load('connect3.video')
audio = load('connect3.audio')


class RuntimePeerTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_qv_decoders_outputs_and_dedicated_talk_peer(self):
        for codec in (4, 8):
            with self.subTest(codec=codec):
                peer = SyntheticQVPeer(audio_codec=codec)
                observation = {}
                current = session.QVSession('192.0.2.1', 34567, '', STREAM_KEY,
                    cgi.encode_auth_code(PASSWORD), observation, transport='r002_tcp')
                decoders = {'video': video.VideoDecoder(), 'audio': audio.AudioDecoder()}
                counts = {'video': 0, 'audio': 0}
                got_media = asyncio.Event()

                async def on_frame(packet):
                    kind = 'audio' if packet.is_audio else 'video'
                    decoded = decoders[kind].feed(packet)
                    counts[kind] += len(decoded if kind == 'audio' else decoded[0])
                    if all(counts.values()):
                        got_media.set()

                with patch.object(session, 'open_r002_media_tcp', return_value=(peer.reader, peer)):
                    running = asyncio.create_task(current.run(on_frame))
                    microphone = None
                    try:
                        await asyncio.wait_for(got_media.wait(), 2)
                        self.assertTrue(observation['play_accepted'])
                        self.assertEqual(decoders['audio'].diagnostics['decode_errors'], 0)
                        self.assertTrue((await current.execute_output(1, OPENING_CODE)).accepted)
                        peer.reject_output = True
                        self.assertFalse((await current.execute_output(2, OPENING_CODE)).accepted)
                        self.assertEqual(peer.outputs, [1, 2])
                        self.assertEqual(current.output_diagnostics()['request_send_attempt_count'], 2)
                        hub = SimpleNamespace(stopped=False)
                        hub.live = live.LiveMedia(hub)
                        hub.live.session, hub.live.connected = current, True
                        hub.live.observation['play_accepted'] = True
                        microphone = talk.Talkback(hub)
                        talk_peer = SyntheticQVPeer()
                        with patch.object(talk, 'open_r002_media_tcp', return_value=(talk_peer.reader, talk_peer)):
                            await microphone.start('viewer')
                            for _ in range(3):
                                await microphone.feed('viewer', pcm())
                            self.assertGreater(talk_peer.audio_packets, 0)
                            await microphone.stop('viewer')
                        self.assertEqual(talk_peer.commands, [0xA9, 11, 12, 13, 13, 7])
                        self.assertEqual(talk_peer.teardowns, 1)
                        self.assertTrue(talk_peer.closed)
                    finally:
                        if microphone is not None:
                            await microphone.close()
                        running.cancel()
                        await asyncio.gather(running, return_exceptions=True)
                        await current.close()
                        for decoder in decoders.values():
                            decoder.close()
                self.assertEqual(peer.commands.count(1), 1)
                self.assertEqual(peer.commands.count(7), 1)
                self.assertEqual(peer.close_count, 1)
                self.assertTrue(peer.producer.done())
                self.assertIsNone(current._read_task)


if __name__ == '__main__':
    unittest.main()
