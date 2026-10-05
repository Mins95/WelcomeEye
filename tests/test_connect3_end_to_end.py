"""Real loopback HTTPS/TLS and H264 over synthetic QV packets, never hardware."""
import asyncio
from hashlib import sha256
import json
import struct
from types import SimpleNamespace
import unittest

from load_integration import load
import test_connect3_https as https_fixture
from test_connect3_media_protocol import KEY, aes, control_response, frame_bytes, media_response
from test_connect3_video import synthetic_video_packets

hub_module = load('connect3.hub')


class EndToEndTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # The helper creates a temporary, synthetic certificate and private key.
        https_fixture.HTTPSTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        https_fixture.HTTPSTests.tearDownClass()

    async def asyncSetUp(self):
        self.http = https_fixture.HTTPSTests()
        await self.http.asyncSetUp()
        self.http.response_body = (f'<envelope><body><error>0</error><content>'
                                   f'<key>{KEY}</key></content></body></envelope>').encode()
        self.handlers, self.writers, self.failures = set(), set(), []
        self.connections = []
        self.media_server = await asyncio.start_server(self.serve_media, '127.0.0.1', 0,
            ssl=self.http.context, ssl_handshake_timeout=1.0)
        media_port = self.media_server.sockets[0].getsockname()[1]
        self.hub = hub_module.Connect3Hub(SimpleNamespace(), SimpleNamespace(data={
            'host': '127.0.0.1', 'auth_code': https_fixture.AUTH_CODE,
            'experimental_video': True, 'cgi_port': self.http.port,
            'media_port': media_port, 'certificate_sha256': self.http.fingerprint,
            'media_certificate_sha256': self.http.fingerprint}))
        # Encode once locally; no device data or network resource involved.
        self.packets = synthetic_video_packets(count=2)
        self.assertEqual(len(self.packets), 2)
        await self.hub.start()

    async def asyncTearDown(self):
        await self.hub.stop()
        self.media_server.close()
        await self.media_server.wait_closed()
        for writer in tuple(self.writers):
            writer.close()
        for handler in tuple(self.handlers):
            handler.cancel()
        await asyncio.gather(*tuple(self.handlers), return_exceptions=True)
        self.assertFalse(self.handlers)
        self.assertFalse(self.writers)
        self.assertEqual(self.failures, [])
        await self.http.asyncTearDown()

    async def serve_media(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        self.writers.add(writer)
        record = {'commands': [], 'closed': asyncio.Event()}
        self.connections.append(record)
        try:
            async with asyncio.timeout(5):
                self.assertEqual(await reader.readexactly(32), b'\xa9' + bytes(31))
                record['commands'].append(0xA9)
                setup = bytearray(32)
                setup[0], setup[10], setup[11] = 0xA9, 2, 1
                writer.write(setup)
                await writer.drain()
                header = aes(await reader.readexactly(32), decrypt=True)
                self.assertEqual(header[0], 1)
                record['commands'].append(1)
                extension_length, parameter_length = struct.unpack_from('<HH', header, 9)
                body = aes(await reader.readexactly(extension_length), decrypt=True)
                expected = (b'adminapp2&&' + sha256(https_fixture.AUTH_CODE.encode()).hexdigest().encode()
                            + b'\0\0')
                self.assertEqual(header[13:17], b'\1\0\1\1')  # channel1, action1, ids2 -> wire1
                self.assertEqual(parameter_length, len(expected))
                self.assertEqual(body[:parameter_length], expected)
                self.assertEqual(body[parameter_length:parameter_length + 32],
                                 sha256(header + expected).digest())
                writer.write(b''.join(control_response()))
                for packet in self.packets:
                    frame = bytearray(frame_bytes(payload=bytes(packet),
                        frame_type=1 if packet.is_keyframe else 0))
                    struct.pack_into('<HH', frame, 16, 64, 48)
                    writer.write(b''.join(media_response(bytes(frame))))
                await writer.drain()
                while True:
                    header = aes(await reader.readexactly(32), decrypt=True)
                    extension_length = struct.unpack_from('<H', header, 9)[0]
                    self.assertEqual(extension_length, 32)
                    body = aes(await reader.readexactly(extension_length), decrypt=True)
                    self.assertEqual(body, sha256(header).digest())
                    record['commands'].append(header[0])
                    self.assertIn(header[0], (0, 7))
                    if header[0] == 7:
                        break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.failures.append(type(exc).__name__)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, TimeoutError):
                pass
            self.writers.discard(writer)
            self.handlers.discard(task)
            record['closed'].set()

    async def test_three_live_cycles_share_one_session_and_release_tls(self):
        decoded = []
        frame_event = asyncio.Event()

        def on_frame(kind, frame):
            decoded.append((kind, frame.width, frame.height, frame.pts))
            if len(decoded) % 2 == 0:
                frame_event.set()

        self.hub.frame_listeners.add(on_frame)
        async with asyncio.timeout(10):
            for cycle in range(3):
                frame_event.clear()
                await asyncio.gather(self.hub.acquire('viewer_one'), self.hub.acquire('viewer_two'))
                await frame_event.wait()
                self.assertEqual(len(self.connections), cycle + 1)
                self.assertTrue(self.hub.connected)
                self.assertEqual(len(self.hub.consumers), 2)
                self.assertTrue(self.hub.image.startswith(b'\xff\xd8'))
                self.assertTrue(self.hub.diagnostics()['device_authenticated'])
                text = json.dumps(self.hub.diagnostics())
                for secret in (KEY, https_fixture.AUTH_CODE, sha256(https_fixture.AUTH_CODE.encode()).hexdigest(),
                               self.http.fingerprint, '127.0.0.1', self.hub.image.hex()):
                    self.assertNotIn(secret, text)
                await self.hub.release('viewer_one')
                self.assertTrue(self.hub.connected)
                self.assertFalse(self.connections[-1]['closed'].is_set())
                await self.hub.release('viewer_two')
                await self.connections[-1]['closed'].wait()
                self.assertFalse(self.hub.connected)
                self.assertFalse(self.hub.consumers)
                self.assertIsNone(self.hub.live.task)
                self.assertEqual(self.connections[-1]['commands'], [0xA9, 1, 7])
        self.assertEqual(len(self.http.requests), 3)
        self.assertEqual(decoded, [('video', 64, 48, pts)
                                   for _ in range(3) for pts in (0, 4500)])
        for method, target, version, headers, body in self.http.requests:
            self.assertEqual((method, target), ('POST', '/tdkcgi'))
            self.assertIn(b'get.device.streamkey', body)
            self.assertNotIn(https_fixture.AUTH_CODE.encode(), body)
        self.assertEqual(self.failures, [])


if __name__ == '__main__':
    unittest.main()
