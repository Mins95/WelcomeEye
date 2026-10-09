"""Actual HA APIs and aiortc loopback; all device I/O is synthetic and mocked.

Run inside a clean HA Core image, as verify_connect3_runtime.py does. The
manifest dependency is installed through HA's supported package helper. No
intercom, cloud endpoint, external ICE server or physical command is used.
"""
import asyncio
from fractions import Fraction
from hashlib import sha256
import importlib
import importlib.metadata
from io import BytesIO
import json
import struct
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import DATA_DOMAIN_PLATFORM_ENTITIES
from homeassistant.requirements import pip_kwargs
from homeassistant.util.package import install_package


async def main(root, *, transport='tls'):
    with tempfile.TemporaryDirectory() as temporary:
        manifest = json.loads((root / 'custom_components/welcomeeye_local/manifest.json').read_text())
        for requirement in manifest['requirements']:
            assert await asyncio.to_thread(install_package, requirement, **pip_kwargs(temporary))
        sys.path.insert(0, str(root))
        package = 'custom_components.welcomeeye_local'
        integration = importlib.import_module(package)
        config = importlib.import_module(package + '.config_flow')
        camera_module = importlib.import_module(package + '.camera')
        button_module = importlib.import_module(package + '.button')
        sensor_module = importlib.import_module(package + '.sensor')
        live = importlib.import_module(package + '.connect3.live')
        media_protocol = importlib.import_module(package + '.connect3.protocol')
        talk_module = importlib.import_module(package + '.connect3.talk')
        control_module = importlib.import_module(package + '.connect3.control')
        cgi = importlib.import_module(package + '.connect3.cgi')
        hub_module = importlib.import_module(package + '.connect3.hub')
        trust = importlib.import_module(package + '.connect3.trust')
        qv = importlib.import_module(package + '.r002.qv_discovery')
        rtc = importlib.import_module(package + '.rtc')
        player = importlib.import_module(package + '.player')
        diagnostics = importlib.import_module(package + '.diagnostics')
        media_module = importlib.import_module(package + '.media_source')
        from homeassistant.components.camera.webrtc import WebRTCAnswer
        from homeassistant.components.media_source.models import MediaSourceItem
        import av
        import aioice.ice
        from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
        from PIL import Image

        hass = HomeAssistant(temporary)
        hass.config_entries = ConfigEntries(hass, {})
        if hasattr(dr, 'async_setup'):
            dr.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        registry = er.async_get(hass)
        media_root = Path(temporary) / 'private_media'
        hass.config.media_dirs = {'local': str(media_root)}
        existing_photo = media_root / 'WelcomeEye/connect3_fixture/2026-10-04/existing.jpg'
        existing_photo.parent.mkdir(parents=True)
        jpeg = BytesIO()
        Image.new('RGB', (16, 16), 'green').save(jpeg, format='JPEG')
        existing_photo.write_bytes(jpeg.getvalue())
        before_photo = (existing_photo.stat().st_mtime_ns, sha256(existing_photo.read_bytes()).hexdigest())

        entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
            title='Connect 3 synthetic media fixture', unique_id='connect3-synthetic-media',
            data={'host': '192.0.2.1', 'protocol_family': 'connect3_qv_experimental',
                  'auth_code': 'SYNTHETIC_PASSWORD', 'certificate_sha256': 'a' * 64,
                  'credential_source': 'manual', 'media_transport': transport,
                  'experimental_tcp_controls': False},
            options={}, source='user', subentries_data=None,
            discovery_keys=MappingProxyType({}))
        hass.config_entries._entries[entry.entry_id] = entry
        identity = (entry.entry_id, entry.unique_id)
        created = []

        async def forward(config_entry, platforms):
            for platform in platforms:
                module = importlib.import_module(f'{package}.{platform.value}')
                await module.async_setup_entry(hass, config_entry, created.extend)

        # Opt-in is disabled initially, and enabling it must not connect either.
        def inspected(host, cgi_port=443, media_port=8443, *, cgi_pin='', media_pin='', media_tls=True):
            endpoint = trust.EndpointTrust('pinned', cgi_pin or 'a' * 64,
                validity_status='valid', not_valid_after='2099-01-01T00:00:00+00:00')
            return trust.TrustInspection(endpoint,
                endpoint if media_tls else trust.EndpointTrust('not_applicable'))
        with patch.object(hass.config_entries, 'async_forward_entry_setups', side_effect=forward), patch.object(
            asyncio, 'open_connection', side_effect=AssertionError('device TCP forbidden')), patch.object(
            qv, '_open_listener', side_effect=AssertionError('device UDP forbidden')), patch.object(
            integration, 'WelcomeEyeHub', side_effect=AssertionError('legacy path forbidden')), patch.object(
            config, 'inspect_trust', AsyncMock(side_effect=inspected)):
            assert await integration.async_setup_entry(hass, entry)
            assert len(created) == 1 and isinstance(created[0], sensor_module.WelcomeEyeConnect3Status)
            assert not entry.runtime_data.capabilities.camera
            with patch.object(hass.config_entries, 'async_unload_platforms', AsyncMock(return_value=True)):
                assert await integration.async_unload_entry(hass, entry)
            flow = config.WelcomeEyeConfigFlow()
            flow.hass = hass
            flow.context = {'source': 'reconfigure', 'entry_id': entry.entry_id}
            form = await flow.async_step_reconfigure()
            assert form['step_id'] == 'connect3_reconfigure'
            assert 'SYNTHETIC_PASSWORD' not in str(form)
            data = form['data_schema']({'host': '192.0.2.1', 'experimental_video': True,
                'experimental_outputs': True, 'opening_code': 'SYNTHETIC_OPENING_CODE',
                'media_transport': transport, 'experimental_tcp_controls': transport == 'connect3_tcp'})
            with patch.object(hass.config_entries, 'async_reload', AsyncMock()):
                result = await flow.async_step_connect3_reconfigure(data)
                if result['type'] == 'form':
                    assert transport == 'connect3_tcp'
                    result = await flow.async_step_connect3_tls_confirm({'trust': True})
            assert result['type'] == 'abort'
            assert (entry.entry_id, entry.unique_id) == identity
            assert entry.data['auth_code'] == 'SYNTHETIC_PASSWORD'
            created.clear()
            assert await integration.async_setup_entry(hass, entry)
        assert len(created) == 5
        camera = next(e for e in created if type(e) is camera_module.WelcomeEyeConnect3Camera)
        trial_camera = next(e for e in created if type(e) is camera_module.WelcomeEyeConnect3Channel2Camera)
        assert trial_camera.hub is entry.runtime_data.channel2
        assert trial_camera.unique_id == f'{entry.unique_id}_camera_channel_2'
        assert trial_camera.device_info == camera.device_info
        sensor = next(e for e in created if isinstance(e, sensor_module.WelcomeEyeConnect3Status))
        buttons = sorted((e for e in created if isinstance(e, button_module.WelcomeEyeOpenButton)),
                         key=lambda e: e.output)
        assert [button.output for button in buttons] == [0, 1]
        hub = entry.runtime_data
        assert hub.capabilities.camera and hub.capabilities.live_media
        assert hub.capabilities.downstream_audio
        assert hub.capabilities.talkback and hub.capabilities.strike and hub.capabilities.gate
        for capability in ('local_ring', 'manual_snapshot'):
            assert not getattr(hub.capabilities, capability), capability
        assert hub.talkback.owner is None and not hub.talkback.active
        assert camera._supports_native_async_webrtc
        assert await camera.stream_source() is None
        assert await camera.async_camera_image() is None
        assert hub.live.task is None and not hub.consumers
        camera.hass = sensor.hass = hass
        for button in buttons:
            button.hass = hass
            registered = registry.async_get_or_create('button', 'welcomeeye_local',
                button.unique_id, config_entry=entry)
            button.entity_id = registered.entity_id
        camera.entity_id = 'camera.connect3_fixture'
        sensor.entity_id = 'sensor.connect3_fixture_status'
        registered_camera = registry.async_get_or_create('camera', 'welcomeeye_local',
            camera.unique_id, config_entry=entry)
        hass.data[DATA_DOMAIN_PLATFORM_ENTITIES] = {('sensor', 'welcomeeye_local'): {sensor.entity_id: sensor}}
        # HA's native camera route has no control-authorized player context.
        # Preserve its default receive-only permission even with talk enabled.
        with patch.object(camera.rtc, 'offer', AsyncMock()) as native_offer:
            await camera.async_handle_async_webrtc_offer('synthetic-offer', 'native', Mock())
            assert not native_offer.call_args.kwargs.get('allow_talk', False)

        # Actual websocket handlers repeat read/control permissions before
        # advertising buttons or granting the microphone. Only entity lookup
        # and ICE display configuration are replaced; no peer/media is opened.
        from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
        permission = {'read': True, 'control': False}
        connection = SimpleNamespace(user=SimpleNamespace(permissions=SimpleNamespace(
            check_entity=lambda entity_id, policy: permission['control'] if policy == POLICY_CONTROL
                else permission['read'] if policy == POLICY_READ else False)),
            send_error=Mock(), send_result=Mock(), send_event=Mock(), subscriptions={})
        async def dispatch_websocket(handler, message):
            # Invoke HA's decorated synchronous dispatcher, then join its real
            # async_response background task before inspecting responses.
            handler(hass, connection, message)
            await hass.async_block_till_done(wait_background_tasks=True)
        client_configuration = SimpleNamespace(to_frontend_dict=lambda: {'ice_servers': []})
        with patch.object(player, 'get_camera_from_entity_id', return_value=camera), patch.object(
                camera, 'async_get_webrtc_client_configuration', return_value=client_configuration), patch.object(
                camera.rtc, 'offer', AsyncMock()) as authorized_offer:
            for allowed in (False, True):
                permission['control'] = allowed
                await dispatch_websocket(player.player_config, {'id': 40, 'entity_id': camera.entity_id})
                frontend = connection.send_result.call_args.args[1]
                assert frontend['microphone_allowed'] is allowed
                assert all(bool(value) is allowed for value in frontend['buttons'].values())
                await dispatch_websocket(player.player_offer,
                    {'id': 41, 'entity_id': camera.entity_id, 'offer': 'synthetic-permission-offer'})
                assert authorized_offer.await_args.kwargs['allow_talk'] is allowed
            permission['read'] = False
            authorized_offer.reset_mock()
            await dispatch_websocket(player.player_offer,
                {'id': 42, 'entity_id': camera.entity_id, 'offer': 'synthetic-denied-offer'})
            authorized_offer.assert_not_awaited()
            assert connection.send_error.call_args.args[1] == 'unauthorized'
        assert hub.live.task is None and not hub.consumers

        # The separate media-certificate action retains the same admin/control
        # permission gates. Permission failure must happen before device I/O.
        user = SimpleNamespace(is_admin=False, permissions=SimpleNamespace(check_entity=Mock(return_value=True)))
        hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=user))
        async def certificate_action(context):
            return await hass.services.async_call('welcomeeye_local', 'connect3_check_media_certificate',
                {'entity_id': sensor.entity_id, 'include_details': True}, blocking=True,
                return_response=True, context=context)
        with patch.object(hub_module, 'inspect_certificate', AsyncMock()) as inspect:
            for context in (Context(), Context(user_id='nonadmin')):
                try:
                    await certificate_action(context)
                except HomeAssistantError:
                    pass
                else:
                    raise AssertionError('Media-certificate admin gate bypassed')
            user.is_admin = True
            user.permissions.check_entity.return_value = False
            try:
                await certificate_action(Context(user_id='admin'))
            except HomeAssistantError:
                pass
            else:
                raise AssertionError('Media-certificate control gate bypassed')
            inspect.assert_not_called()

        async def doorbell_action(operation, context=None):
            return await hass.services.async_call('welcomeeye_local', 'connect3_observe_doorbell',
                {'entity_id': sensor.entity_id, 'operation': operation, 'duration': 30},
                blocking=True, return_response=True,
                context=context if context is not None else Context(user_id='admin'))

        with patch.object(hub.doorbell, 'execute', wraps=hub.doorbell.execute) as observe:
            for context, admin, control in ((Context(), True, True),
                    (Context(user_id='nonadmin'), False, True),
                    (Context(user_id='denied'), True, False)):
                user.is_admin = admin
                user.permissions.check_entity.return_value = control
                try:
                    await doorbell_action('start', context)
                except HomeAssistantError:
                    pass
                else:
                    raise AssertionError('Doorbell observation permission gate bypassed')
            observe.assert_not_called()
        assert hub.doorbell.diagnostics()['runs'] == 0
        user.is_admin = True
        user.permissions.check_entity.return_value = True
        # Even an authorized action cannot create live media or a listener.
        with patch.object(hub, 'acquire', side_effect=AssertionError('Observation must not acquire media')):
            try:
                await doorbell_action('start')
            except HomeAssistantError:
                pass
            else:
                raise AssertionError('Observation started without existing video')
        assert hub.live.task is None and not hub.consumers

        sessions = []
        async def read_material(*args, diagnostics=None, **kwargs):
            diagnostics['authentication_status'] = 'accepted'
            diagnostics.update(tls_verified=True, tls_policy='certificate_pin')
            return cgi.StreamMaterial('SYNTHETIC_STREAM_KEY_OF_SUFFICIENT_LENGTH')

        class SyntheticQVSession:
            """Replace device I/O only; real VideoDecoder and WebRTC remain."""
            def __init__(self, *args, **kwargs):
                (self._host, self._port, self._pin, self._stream_key,
                 self._password, self.observation) = args
                self._close_task = None
                self.observation = args[-1]
                self.closed = False
                self._transport = kwargs.get('transport', 'tls')
                self._cgi_verified = kwargs.get('cgi_verified', False)
                self._tcp_outputs_enabled = kwargs.get('tcp_outputs_enabled', False)
                if self._transport == 'connect3_tcp':
                    assert self._port == 34567 and self._cgi_verified is True and self._tcp_outputs_enabled is True
                self.outputs = []
                self.output_result = 0
                self.output_counters = dict(request_send_attempt_count=0, request_sent_count=0,
                    response_count=0, physical_request_uncertain=False)
                sessions.append(self)
                self.audio_codec = 8 if self._transport == 'tls' and len(sessions) == 2 else 4
            async def run(self, callback):
                encoder = av.CodecContext.create('libx264', 'w')
                encoder.width, encoder.height = 64, 48
                encoder.pix_fmt = 'yuv420p'
                encoder.time_base = Fraction(1, 20)
                encoder.framerate = Fraction(20)
                encoder.gop_size = 20
                encoder.options = {'preset': 'ultrafast', 'tune': 'zerolatency'}
                audio_encoder = None
                if self.audio_codec == 8:
                    audio_encoder = av.CodecContext.create('aac', 'w')
                    audio_encoder.sample_rate = 8000
                    audio_encoder.layout = 'mono'
                    audio_encoder.format = 'fltp'
                    audio_encoder.bit_rate = 24000
                self.observation.update(stage='waiting_video', media_tls_verified=self._transport == 'tls',
                                        play_accepted=True, tcp_closed=False)
                # Exercise the actual parser on independent synthetic overlap
                # packets before real H264/WebRTC. No hardware bytes are used.
                from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                material = media_protocol.CipherMaterial('SYNTHETIC_STREAM_KEY_OF_SUFFICIENT_LENGTH')
                def encrypt(data):
                    cipher = Cipher(algorithms.AES(material.key), modes.CBC(b'0' * 16)).encryptor()
                    return cipher.update(data) + cipher.finalize()
                assembler = media_protocol.FrameAssembler()
                index = 0
                try:
                    while True:
                        frame = av.VideoFrame(64, 48, 'yuv420p')
                        for plane_index, plane in enumerate(frame.planes):
                            plane.update(bytes([50 + index % 100 if plane_index == 0 else 128]) * plane.buffer_size)
                        frame.pts, frame.time_base = index, Fraction(1, 20)
                        for packet in encoder.encode(frame):
                            payload = bytes(packet)
                            inner = bytearray(20)
                            inner[:4] = b'\0\0\1' + bytes((0xE1 if packet.is_keyframe else 0xE0,))
                            struct.pack_into('<I', inner, 4, len(payload))
                            inner[14], inner[15] = 1, 80
                            struct.pack_into('<HH', inner, 16, 64, 48)
                            body = bytes(inner) + payload
                            wire_body = encrypt(body[:16]) + body[16:]
                            outer = bytearray(32)
                            outer[0] = 0xA1
                            struct.pack_into('<H', outer, 9, 16)
                            struct.pack_into('<I', outer, 11, len(body))
                            metadata = {}
                            parsed = media_protocol.decode_packet_header(encrypt(bytes(outer)),
                                material, diagnostics=metadata)
                            chunk = media_protocol.decode_packet(parsed, wire_body, material)
                            self.observation['last_media_header'] = metadata
                            for decoded in assembler.feed(chunk.data):
                                await callback(decoded)
                        # Cycle two uses real AAC to cover its planar-float
                        # decode -> packed S16 -> WebRTC Opus boundary.
                        audio_payloads = [b'\xd5' * 400]
                        if audio_encoder is not None:
                            source_audio = av.AudioFrame(format='fltp', layout='mono', samples=400)
                            source_audio.sample_rate = 8000
                            source_audio.pts, source_audio.time_base = index * 400, Fraction(1, 8000)
                            source_audio.planes[0].update(struct.pack('<400f', *([.1, -.1] * 200)))
                            audio_payloads = []
                            for encoded_audio in audio_encoder.encode(source_audio):
                                raw_audio = bytes(encoded_audio)
                                length = len(raw_audio) + 7
                                # Independently framed AAC-LC, 8 kHz mono.
                                # Production decoder adds no transport header.
                                adts = bytes((0xff, 0xf1, 0x6c, 0x40 | (length >> 11),
                                              (length >> 3) & 0xff,
                                              ((length & 7) << 5) | 0x1f, 0xfc))
                                audio_payloads.append(adts + raw_audio)
                        for audio_payload in audio_payloads:
                            inner = bytearray(20)
                            inner[:4] = b'\0\0\1' + (b'\xe3' if self._transport == 'connect3_tcp' else b'\xe2')
                            struct.pack_into('<I', inner, 4, len(audio_payload))
                            inner[14], inner[15] = self.audio_codec, 1
                            struct.pack_into('<H', inner, 16, 8000)
                            for decoded in assembler.feed(bytes(inner) + audio_payload):
                                await callback(decoded)
                        index += 1
                        await asyncio.sleep(.05)
                finally:
                    await self.close()
            async def close(self):
                self.closed = True
                self.observation['tcp_closed'] = True

            def output_diagnostics(self):
                return dict(self.output_counters)

            async def execute_output(self, output, opening_code, *, channel):
                # Simulated accepted/rejected peer boundary: no physical write.
                assert opening_code == 'SYNTHETIC_OPENING_CODE'
                assert opening_code != self._password
                assert output in (1, 2) and channel == 1 and not self.closed
                self.outputs.append(output)
                for key in ('request_send_attempt_count', 'request_sent_count', 'response_count'):
                    self.output_counters[key] += 1
                return control_module.UnlockResponse(self.output_result)

        class SyntheticMicrophone(AudioStreamTrack):
            """A browser PCM source; no microphone capture API or hardware."""
            def __init__(self):
                super().__init__()
                self.samples = 0
                self.started = None

            async def recv(self):
                loop = asyncio.get_running_loop()
                if self.started is None:
                    self.started = loop.time()
                await asyncio.sleep(max(0, self.started + self.samples / 48000 - loop.time()))
                frame = av.AudioFrame(format='s16', layout='mono', samples=960)
                frame.sample_rate, frame.pts, frame.time_base = 48000, self.samples, Fraction(1, 48000)
                frame.planes[0].update(struct.pack('<960h', *([1000, -1000] * 480)))
                self.samples += 960
                return frame

        class SyntheticTalkPeer:
            """In-memory TLS boundary with independent AES/SHA response bytes."""
            def __init__(self, codec_mask):
                self.reader = asyncio.StreamReader()
                self.codec_mask = codec_mask
                self.closed = False
                self.audio_packets = self.teardowns = self.close_count = 0
                self.commands = []
                self.teardown_started = asyncio.Event()
                self.teardown_release = asyncio.Event()
                self.teardown_release.set()

            @staticmethod
            def crypt(data, *, decrypt=False):
                from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                cipher = Cipher(algorithms.AES(b'SYNTHETIC_STREAM_KEY_OF_SUFFICIENT_LENGTH'[:32]),
                                modes.CBC(b'0' * 16))
                operation = cipher.decryptor() if decrypt else cipher.encryptor()
                return operation.update(data) + operation.finalize()

            def write(self, data):
                assert not self.closed
                if not self.commands:
                    assert len(data) == 32 and data[0] == 0xA9 and data[9] == 2
                    self.commands.append(0xA9)
                    response = bytearray(32)
                    response[0], response[10], response[11] = 0xA9, 2, 1
                    self.reader.feed_data(bytes(response))
                    return
                header = self.crypt(data[:32], decrypt=True)
                command = header[0]
                if command == 0xA2:
                    self.audio_packets += 1
                    return
                self.commands.append(command)
                if command == 7:
                    self.teardowns += 1
                    self.teardown_started.set()
                    return
                assert command in (0, 0x0B, 0x0C, 0x0D), command
                response = bytearray(32)
                response[0] = command
                struct.pack_into('<H', response, 9, 32)
                if command == 0x0B:
                    struct.pack_into('<H', response, 12, self.codec_mask)
                    response[14] = 1
                raw = bytes(response)
                self.reader.feed_data(self.crypt(raw) + self.crypt(sha256(raw).digest()))

            async def drain(self):
                if self.teardowns:
                    await self.teardown_release.wait()

            def is_closing(self):
                return self.closed

            def close(self):
                self.close_count += 1
                self.closed = True
                self.reader.feed_eof()

            async def wait_closed(self):
                assert self.closed

        peers = []
        async def open_talk_peer(host, port, pin, observation):
            assert host == '192.0.2.1' and pin == 'a' * 64
            peer = SyntheticTalkPeer(16 if sessions[-1].audio_codec == 8 else 1)
            peers.append(peer)
            observation['media_tls_verified'] = True
            return peer.reader, peer

        async def open_talk_tcp_peer(host, port, observation):
            assert port == 34567
            reader, peer = await open_talk_peer(host, port, 'a' * 64, observation)
            observation.update(media_tls_verified=False, media_transport='connect3_tcp')
            return reader, peer

        async def wait_for(predicate):
            async with asyncio.timeout(10):
                while not predicate():
                    await asyncio.sleep(.01)

        configuration = (RTCConfiguration(iceServers=[]), {'ice_server_source': 'synthetic_loopback',
            'ice_server_count': 0, 'stun_server_count': 0, 'turn_server_count': 0, 'turn_available': False})
        cycles = []
        entry_before = json.dumps([dict(entry.data), dict(entry.options)])
        with patch.object(live, 'QVSession', SyntheticQVSession), patch.object(
            live, 'read_stream_material', AsyncMock(side_effect=read_material)) as read, patch.object(
            asyncio, 'open_connection', side_effect=AssertionError('device TCP forbidden')), patch.object(
            qv, '_open_listener', side_effect=AssertionError('device UDP forbidden')), patch.object(
            rtc, '_ice_configuration', return_value=configuration), patch.object(
            talk_module, 'open_media_tls', side_effect=open_talk_peer), patch.object(
            talk_module, 'open_connect3_media_tcp', side_effect=open_talk_tcp_peer, create=True), patch.object(
            aioice.ice, 'get_host_addresses', return_value=['127.0.0.1']):
            for cycle in range(3):
                browser = RTCPeerConnection(RTCConfiguration(iceServers=[]))
                received = []
                received_audio = []
                consumers = []
                channel_ready = asyncio.Event()
                replies = {}
                channel = browser.createDataChannel('welcomeeye-control')
                @channel.on('open')
                def opened():
                    channel_ready.set()
                @channel.on('message')
                def reply(raw):
                    value = json.loads(raw)
                    if value.get('type') == 'microphone':
                        replies[value['id']] = value
                async def set_microphone(identifier, enabled):
                    channel.send(json.dumps({'type': 'microphone', 'id': identifier, 'enabled': enabled}))
                    await wait_for(lambda: identifier in replies)
                    assert replies[identifier]['enabled'] is enabled, replies[identifier]
                    assert not replies[identifier].get('error'), replies[identifier]
                async def heartbeat():
                    await channel_ready.wait()
                    while channel.readyState == 'open':
                        channel.send(json.dumps({'type': 'heartbeat'}))
                        await asyncio.sleep(.5)
                async def consume(track):
                    while True:
                        frame = await track.recv()
                        if track.kind == 'video':
                            assert (frame.width, frame.height) == (64, 48)
                            received.append(frame.pts)
                        else:
                            assert track.kind == 'audio' and frame.sample_rate == 48000
                            received_audio.append(frame.pts)
                @browser.on('track')
                def track_started(track):
                    consumers.append(asyncio.create_task(consume(track)))
                async def require_media(video_count, audio_count):
                    try:
                        await wait_for(lambda: (len(received) >= video_count and len(received_audio) >= audio_count)
                            or any(task.done() for task in consumers))
                    except TimeoutError:
                        raise AssertionError(f'Media stalled: video={len(received)}, audio={len(received_audio)}') from None
                    for task in consumers:
                        if task.done():
                            task.result()
                            raise AssertionError('Media consumer ended unexpectedly')
                browser.addTransceiver('video', direction='recvonly')
                microphone = SyntheticMicrophone()
                browser.addTransceiver(microphone, direction='sendrecv')
                messages = []
                session_id = f'synthetic-{cycle}'
                hold = object()
                heartbeat_task = asyncio.create_task(heartbeat())
                try:
                    async with asyncio.timeout(30):
                        await browser.setLocalDescription(await browser.createOffer())
                        # Authorized player route passes this flag after its
                        # HA POLICY_CONTROL check; native camera default above
                        # retains receive-only behavior.
                        await camera.rtc.offer(browser.localDescription.sdp,
                            session_id, messages.append, allow_talk=True)
                        assert len(messages) == 1 and isinstance(messages[0], WebRTCAnswer), messages
                        await browser.setRemoteDescription(RTCSessionDescription(messages[0].answer, 'answer'))
                        await channel_ready.wait()
                        await require_media(2, 2)
                        assert received[1] > received[0] and received_audio[1] > received_audio[0]
                        assert hub.live.observation['audio']['decode_errors'] == 0
                        assert hub.live.observation['audio']['codec_id'] == (8 if transport == 'tls' and cycle == 1 else 4)
                        assert hub.live.session_count == cycle + 1
                        assert set(camera.rtc.viewers[session_id].tracks) == {'audio', 'video'}
                        assert not camera.rtc.viewers[session_id].mic_enabled
                        assert not hub.talkback.active and hub.talkback.owner is None
                        # Real browser Opus -> inbound AudioFrame -> native
                        # talk encoder/packet path, with only TLS I/O mocked.
                        await set_microphone(1, True)
                        viewer = camera.rtc.viewers[session_id]
                        assert hub.talkback.active and hub.talkback.owner is viewer
                        peer = peers[-1]
                        await wait_for(lambda: peer.audio_packets > 0)
                        assert hub.webrtc_diagnostics['inbound_audio_frames_received'] > 0
                        assert hub.talkback.diagnostics['frames_sent'] > 0
                        peer.teardown_release.clear()
                        channel.send(json.dumps({'type': 'microphone', 'id': 2, 'enabled': False}))
                        await peer.teardown_started.wait()
                        assert not hub.talkback.active and not peer.closed
                        off_packets, off_video, off_audio = peer.audio_packets, len(received), len(received_audio)
                        # The delayed teardown remains pending while both
                        # downstream media tracks continue. No queued browser
                        # microphone frame may escape after synchronous Off.
                        await require_media(off_video + 3, off_audio + 3)
                        assert peer.audio_packets == off_packets
                        peer.teardown_release.set()
                        await wait_for(lambda: 2 in replies)
                        assert replies[2]['enabled'] is False
                        assert peer.closed and peer.teardowns == peer.close_count == 1
                        assert hub.talkback.owner is None

                        # Actual button entities and controller, simulated peer
                        # acknowledgements only. Each action uses one request
                        # and releases its own lease without interrupting video.
                        if cycle == 0:
                            session = sessions[-1]
                            leases = len(hub.consumers)
                            with patch.object(hub, 'acquire', side_effect=AssertionError('Observation must not acquire media')):
                                for operation, status in (('start', 'observing'), ('mark', 'observing'),
                                                          ('status', 'observing'), ('stop', 'finished')):
                                    answer = (await doorbell_action(operation))[sensor.entity_id]
                                    assert answer['observation']['status'] == status
                                    assert not answer['local_detection_implemented']
                                    assert answer['ring_events_emitted'] == 0
                            assert len(answer['observation']['markers']) == 1
                            assert hub.live.session is session and len(hub.consumers) == leases
                            assert hub.doorbell._timer is None
                            for button in buttons:
                                await button.async_press()
                                assert len(hub.consumers) == 1 and hub.live.session is session
                            assert session.outputs == [1, 2]
                            assert hub.control.diagnostics()['response_count'] == 2
                            session.output_result = -10029
                            try:
                                await buttons[0].async_press()
                            except HomeAssistantError as error:
                                assert 'SYNTHETIC_' not in str(error)
                            else:
                                raise AssertionError('Rejected synthetic output unexpectedly confirmed')
                            assert session.outputs == [1, 2, 1]  # No implicit retry.
                            assert len(hub.consumers) == 1
                            assert not hub.control.diagnostics()['physical_activation_verified']
                            session.output_result = 0
                        await set_microphone(3, True)
                        await wait_for(lambda: peers[-1].audio_packets > 0)
                        # A second independent lease shares this exact session.
                        await hub.acquire(hold)
                        assert len(hub.consumers) == 2
                        assert hub.live.session_count == cycle + 1
                        await camera.rtc.close(session_id)
                        assert not hub.talkback.active and hub.talkback.owner is None
                        assert peers[-1].closed and peers[-1].teardowns == peers[-1].close_count == 1
                        assert hub.connected and hub.live.task is not None and not sessions[-1].closed
                        assert len(hub.consumers) == 1
                        await hub.release(hold)
                finally:
                    for peer in peers:
                        peer.teardown_release.set()
                    heartbeat_task.cancel()
                    await asyncio.gather(heartbeat_task, return_exceptions=True)
                    for task in consumers:
                        task.cancel()
                    await asyncio.gather(*consumers, return_exceptions=True)
                    await camera.rtc.close(session_id)
                    await hub.release(hold)
                    await browser.close()
                    microphone.stop()
                assert browser.connectionState == 'closed'
                assert not hub.consumers and hub.live.task is None and sessions[-1].closed
                assert not camera.rtc.viewers
                assert hub.talkback.owner is None and hub.talkback._read_task is None
                cycles.append({'frames_received': len(received), 'audio_frames_received': len(received_audio),
                               'audio_codec': 'aac' if transport == 'tls' and cycle == 1 else 'pcm_alaw',
                               'microphone_start_stop_restart': 'pass', 'off_while_teardown_pending': 'pass',
                               'talk_sessions': 2, 'media_sessions': 1, 'leases_remaining': 0})
            assert read.await_count == len(sessions) == 3
            assert all(s.closed for s in sessions)
            assert len(peers) == 6 and all(p.closed and p.teardowns == p.close_count == 1 for p in peers)
            assert camera.rtc.hub.webrtc_diagnostics.get('cleanup_error_type') is None
            diagnostic_data = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
            assert diagnostic_data['connect3']['audio']['decoded_frames'] > 0
            assert diagnostic_data['connect3']['audio']['decode_errors'] == 0
            assert diagnostic_data['connect3']['webrtc']['downstream_frames_queued']['audio'] > 0
            header = diagnostic_data['connect3']['media']['last_media_header']
            assert header['media_offset'] == 0 and header['extension_length'] == 16
            assert header['offset_before_extension'] and header['header_validation_errors'] == []
            diagnostic = json.dumps(diagnostic_data)
            for secret in ('SYNTHETIC_PASSWORD', 'SYNTHETIC_STREAM_KEY', 'SYNTHETIC_OPENING_CODE',
                           '192.0.2.1', 'a' * 64):
                assert secret not in diagnostic, secret
        assert entry_before == json.dumps([dict(entry.data), dict(entry.options)])
        assert (entry.entry_id, entry.unique_id) == identity
        assert registry.async_get(registered_camera.entity_id).unique_id == camera.unique_id
        # The prior private media file and its authenticated HA URL survive.
        source = await media_module.async_get_media_source(hass)
        item = MediaSourceItem(hass, 'welcomeeye_local',
            'local/WelcomeEye/connect3_fixture/2026-10-04/existing.jpg', None)
        resolved = await source.async_resolve_media(item)
        assert resolved.path == existing_photo and resolved.mime_type == 'image/jpeg'
        assert resolved.url.startswith('/media/local/WelcomeEye/')
        assert before_photo == (existing_photo.stat().st_mtime_ns, sha256(existing_photo.read_bytes()).hexdigest())
        await camera.rtc.close_all()
        with patch.object(hass.config_entries, 'async_unload_platforms', AsyncMock(return_value=True)):
            assert await integration.async_unload_entry(hass, entry)
        assert not camera.rtc.tasks and not camera.rtc.offers
        assert not hub.close_listeners and not hub.frame_listeners
        assert hub.live.task is None and not hub.consumers
        assert hub.talkback.owner is None and hub.talkback._writer is None
        assert hub.control.diagnostics()['closed']
        assert not hass.services.has_service('welcomeeye_local', 'connect3_check_media_certificate')
        assert not hass.services.has_service('welcomeeye_local', 'connect3_observe_doorbell')
        await hass.async_stop(force=True)
        print(json.dumps({'validation': 'actual_HA_synthetic_QV_video_WebRTC',
            'media_transport': transport,
            'homeassistant_version': importlib.metadata.version('homeassistant'),
            'python_version': sys.version.split()[0],
            'aiortc_version': importlib.metadata.version('aiortc'),
            'device_io': 'mocked', 'external_ice_servers': 0, 'hardware_validated': False,
            'opt_in_and_identity': 'pass', 'capability_isolation': 'pass',
            'synthetic_output_buttons': 'pass', 'native_camera_microphone_default_denied': 'pass',
            'websocket_read_control_permissions': 'pass',
            'doorbell_observation_permissions_and_no_acquisition': 'pass',
            'media_certificate_permissions': 'pass', 'private_media_preserved': 'pass',
            'cycles': cycles, 'cleanup': 'pass'}))


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(180):
            await main(Path(__file__).resolve().parents[1],
                transport='connect3_tcp' if '--tcp' in sys.argv[1:] else 'tls')
    asyncio.run(bounded())
