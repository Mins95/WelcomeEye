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


async def main(root):
    with tempfile.TemporaryDirectory() as temporary:
        manifest = json.loads((root / 'custom_components/welcomeeye_local/manifest.json').read_text())
        for requirement in manifest['requirements']:
            assert await asyncio.to_thread(install_package, requirement, **pip_kwargs(temporary))
        sys.path.insert(0, str(root))
        package = 'custom_components.welcomeeye_local'
        integration = importlib.import_module(package)
        config = importlib.import_module(package + '.config_flow')
        camera_module = importlib.import_module(package + '.camera')
        sensor_module = importlib.import_module(package + '.sensor')
        live = importlib.import_module(package + '.connect3.live')
        cgi = importlib.import_module(package + '.connect3.cgi')
        hub_module = importlib.import_module(package + '.connect3.hub')
        qv = importlib.import_module(package + '.r002.qv_discovery')
        rtc = importlib.import_module(package + '.rtc')
        diagnostics = importlib.import_module(package + '.diagnostics')
        media_module = importlib.import_module(package + '.media_source')
        from homeassistant.components.camera.webrtc import WebRTCAnswer
        from homeassistant.components.media_source.models import MediaSourceItem
        import av
        from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
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
                  'credential_source': 'manual'},
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
        with patch.object(hass.config_entries, 'async_forward_entry_setups', side_effect=forward), patch.object(
            asyncio, 'open_connection', side_effect=AssertionError('device TCP forbidden')), patch.object(
            qv, '_open_listener', side_effect=AssertionError('device UDP forbidden')), patch.object(
            integration, 'WelcomeEyeHub', side_effect=AssertionError('legacy path forbidden')):
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
            data = form['data_schema']({'host': '192.0.2.1', 'experimental_video': True})
            with patch.object(hass.config_entries, 'async_reload', AsyncMock()):
                result = await flow.async_step_connect3_reconfigure(data)
            assert result['type'] == 'abort'
            assert (entry.entry_id, entry.unique_id) == identity
            assert entry.data['auth_code'] == 'SYNTHETIC_PASSWORD'
            created.clear()
            assert await integration.async_setup_entry(hass, entry)
        assert len(created) == 2
        camera = next(e for e in created if isinstance(e, camera_module.WelcomeEyeConnect3Camera))
        sensor = next(e for e in created if isinstance(e, sensor_module.WelcomeEyeConnect3Status))
        hub = entry.runtime_data
        assert hub.capabilities.camera and hub.capabilities.live_media
        for capability in ('downstream_audio', 'talkback', 'strike', 'gate', 'local_ring', 'manual_snapshot'):
            assert not getattr(hub.capabilities, capability), capability
        assert not hasattr(hub, 'talkback')
        assert camera._supports_native_async_webrtc
        assert await camera.stream_source() is None
        assert await camera.async_camera_image() is None
        assert hub.live.task is None and not hub.consumers
        camera.hass = sensor.hass = hass
        camera.entity_id = 'camera.connect3_fixture'
        sensor.entity_id = 'sensor.connect3_fixture_status'
        registered_camera = registry.async_get_or_create('camera', 'welcomeeye_local',
            camera.unique_id, config_entry=entry)
        hass.data[DATA_DOMAIN_PLATFORM_ENTITIES] = {('sensor', 'welcomeeye_local'): {sensor.entity_id: sensor}}

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

        sessions = []
        async def read_material(*args, diagnostics=None, **kwargs):
            diagnostics['authentication_status'] = 'accepted'
            return cgi.StreamMaterial('SYNTHETIC_STREAM_KEY_OF_SUFFICIENT_LENGTH')

        class SyntheticQVSession:
            """Replace device I/O only; real VideoDecoder and WebRTC remain."""
            def __init__(self, *args):
                self.observation = args[-1]
                self.closed = False
                sessions.append(self)
            async def run(self, callback):
                encoder = av.CodecContext.create('libx264', 'w')
                encoder.width, encoder.height = 64, 48
                encoder.pix_fmt = 'yuv420p'
                encoder.time_base = Fraction(1, 20)
                encoder.framerate = Fraction(20)
                encoder.options = {'preset': 'ultrafast', 'tune': 'zerolatency'}
                self.observation.update(stage='waiting_video', media_tls_verified=True, tcp_closed=False)
                from custom_components.welcomeeye_local.connect3.protocol import MediaFrame
                index = 0
                try:
                    while True:
                        frame = av.VideoFrame(64, 48, 'yuv420p')
                        for plane_index, plane in enumerate(frame.planes):
                            plane.update(bytes([50 + index % 100 if plane_index == 0 else 128]) * plane.buffer_size)
                        frame.pts, frame.time_base = index, Fraction(1, 20)
                        for packet in encoder.encode(frame):
                            await callback(MediaFrame(1 if packet.is_keyframe else 0,
                                1, 64, 48, 20, 0, 0, bytes(packet)))
                        index += 1
                        await asyncio.sleep(.05)
                finally:
                    await self.close()
            async def close(self):
                self.closed = True
                self.observation['tcp_closed'] = True

        configuration = (RTCConfiguration(iceServers=[]), {'ice_server_source': 'synthetic_loopback',
            'ice_server_count': 0, 'stun_server_count': 0, 'turn_server_count': 0, 'turn_available': False})
        cycles = []
        entry_before = json.dumps([dict(entry.data), dict(entry.options)])
        with patch.object(live, 'QVSession', SyntheticQVSession), patch.object(
            live, 'read_stream_material', AsyncMock(side_effect=read_material)) as read, patch.object(
            asyncio, 'open_connection', side_effect=AssertionError('device TCP forbidden')), patch.object(
            qv, '_open_listener', side_effect=AssertionError('device UDP forbidden')), patch.object(
            rtc, '_ice_configuration', return_value=configuration):
            for cycle in range(3):
                browser = RTCPeerConnection(RTCConfiguration(iceServers=[]))
                received = []
                consumers = []
                channel_ready = asyncio.Event()
                channel = browser.createDataChannel('welcomeeye-control')
                @channel.on('open')
                def opened():
                    channel.send(json.dumps({'type': 'microphone', 'id': 1, 'enabled': True}))
                    channel_ready.set()
                async def consume(track):
                    assert track.kind == 'video', 'Connect 3 must not expose downstream audio'
                    for _ in range(2):
                        frame = await track.recv()
                        assert (frame.width, frame.height) == (64, 48)
                        received.append(frame.pts)
                @browser.on('track')
                def track_started(track):
                    consumers.append(asyncio.create_task(consume(track)))
                browser.addTransceiver('video', direction='recvonly')
                browser.addTransceiver('audio', direction='recvonly')
                messages = []
                session_id = f'synthetic-{cycle}'
                hold = object()
                try:
                    async with asyncio.timeout(30):
                        await browser.setLocalDescription(await browser.createOffer())
                        await camera.async_handle_async_webrtc_offer(browser.localDescription.sdp,
                            session_id, messages.append)
                        assert len(messages) == 1 and isinstance(messages[0], WebRTCAnswer), messages
                        await browser.setRemoteDescription(RTCSessionDescription(messages[0].answer, 'answer'))
                        await channel_ready.wait()
                        await asyncio.gather(*consumers)
                        assert len(received) == 2 and received[1] > received[0]
                        assert hub.live.session_count == cycle + 1
                        assert set(camera.rtc.viewers[session_id].tracks) == {'video'}
                        assert not camera.rtc.viewers[session_id].mic_enabled
                        # A second independent lease shares this exact session.
                        await hub.acquire(hold)
                        assert len(hub.consumers) == 2
                        assert hub.live.session_count == cycle + 1
                        await camera.rtc.close(session_id)
                        assert hub.connected and hub.live.task is not None and not sessions[-1].closed
                        assert len(hub.consumers) == 1
                        await hub.release(hold)
                finally:
                    for task in consumers:
                        task.cancel()
                    await asyncio.gather(*consumers, return_exceptions=True)
                    await camera.rtc.close(session_id)
                    await hub.release(hold)
                    await browser.close()
                assert browser.connectionState == 'closed'
                assert not hub.consumers and hub.live.task is None and sessions[-1].closed
                assert not camera.rtc.viewers
                cycles.append({'frames_received': len(received), 'media_sessions': 1, 'leases_remaining': 0})
            assert read.await_count == len(sessions) == 3
            assert all(s.closed for s in sessions)
            assert camera.rtc.hub.webrtc_diagnostics.get('cleanup_error_type') is None
            diagnostic = json.dumps(await diagnostics.async_get_config_entry_diagnostics(hass, entry))
            for secret in ('SYNTHETIC_PASSWORD', 'SYNTHETIC_STREAM_KEY', '192.0.2.1', 'a' * 64):
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
        assert not hass.services.has_service('welcomeeye_local', 'connect3_check_media_certificate')
        await hass.async_stop(force=True)
        print(json.dumps({'validation': 'actual_HA_synthetic_QV_video_WebRTC',
            'homeassistant_version': importlib.metadata.version('homeassistant'),
            'python_version': sys.version.split()[0],
            'aiortc_version': importlib.metadata.version('aiortc'),
            'device_io': 'mocked', 'external_ice_servers': 0, 'hardware_validated': False,
            'opt_in_and_identity': 'pass', 'capability_isolation': 'pass',
            'media_certificate_permissions': 'pass', 'private_media_preserved': 'pass',
            'cycles': cycles, 'cleanup': 'pass'}))


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(180):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
