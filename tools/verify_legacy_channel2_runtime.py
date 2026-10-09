"""Actual HA/aiortc legacy channel-2 microphone and photos, synthetic LT only.

No intercom, cloud, physical output or discovery is contacted. The worker emits
generated AV frames; real LT microphone builders write to a recording fake socket.
"""
import asyncio
from contextlib import ExitStack
from fractions import Fraction
import importlib
import importlib.metadata
import io
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import time
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
        modules = {name: importlib.import_module(f'{package}.{name}') for name in (
            'hub', 'client', 'protected', 'camera', 'rtc', 'player', 'image', 'services', 'config_flow')}
        from homeassistant.auth.permissions.const import POLICY_READ, POLICY_CONTROL
        from homeassistant.components.camera.webrtc import WebRTCAnswer
        from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
        import aioice.ice
        import av

        hass = HomeAssistant(temporary)
        hass.config.media_dirs = {'local': str(Path(temporary) / 'private_media')}
        hass.config_entries = ConfigEntries(hass, {})
        if hasattr(dr, 'async_setup'): dr.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        registry = er.async_get(hass)
        reports = []

        async def wait_for(predicate):
            async with asyncio.timeout(10):
                while not predicate(): await asyncio.sleep(.01)

        class Microphone(AudioStreamTrack):
            def __init__(self):
                super().__init__()
                self.samples, self.started = 0, None
            async def recv(self):
                loop = asyncio.get_running_loop()
                if self.started is None: self.started = loop.time()
                await asyncio.sleep(max(0, self.started + self.samples / 48000 - loop.time()))
                frame = av.AudioFrame(format='s16', layout='mono', samples=960)
                frame.sample_rate, frame.pts, frame.time_base = 48000, self.samples, Fraction(1, 48000)
                samples = [int(6000 * math.sin(2 * math.pi * 440 * (self.samples + i) / 48000)) for i in range(960)]
                frame.planes[0].update(struct.pack('<960h', *samples))
                self.samples += 960
                return frame

        for variant in ('connect2_r001', 'connect_v1'):
            entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
                title=f'Synthetic {variant}', unique_id=f'SYNTHETIC_{variant}', data={
                    'host': '192.0.2.1', 'username': 'SYNTHETIC_USER', 'password': 'SYNTHETIC_PASSWORD',
                    'protocol_family': 'legacy_owsp', 'device_variant': variant, 'channel': 16,
                    'second_channel_enabled': True, 'v1_cloud_doorbell_enabled': False},
                options={}, source='user', subentries_data=None, discovery_keys=MappingProxyType({}))
            hass.config_entries._entries[entry.entry_id] = entry
            flow = modules['config_flow'].WelcomeEyeConfigFlow()
            flow.hass, flow.context = hass, {'source': 'reconfigure', 'entry_id': entry.entry_id}
            form = await flow.async_step_reconfigure()
            values = form['data_schema']({'experimental_channel2_microphone': True})
            with patch.object(hass.config_entries, 'async_reload', AsyncMock()):
                result = await flow.async_step_reconfigure(values)
            assert result['type'] == 'abort' and entry.data['experimental_channel2_microphone'] is True
            assert entry.data['v1_cloud_doorbell_enabled'] is False
            hub = modules['hub'].WelcomeEyeHub(hass, entry)
            hub.stopped = False  # Skip startup listener/proxy: this tool tests only explicit media.
            entry.runtime_data = hub
            secondary = hub.channel2
            packets, sessions = [], []

            def worker(generation, stop):
                name, channel, stream, mode = hub._profile_order()[0]
                hub.current_profile = dict(name=name, channel=channel, stream=stream, mode=mode)
                session = modules['client'].Session('192.0.2.1', 'SYNTHETIC_USER', 'SYNTHETIC_PASSWORD', channel)
                session.info = SimpleNamespace(uid='SYNTHETIC_UID')
                session.encryption_profile = 0x01020301
                session.device_time, session.clock_received = 123, time.monotonic()
                session.connection_stage = 'authenticated'
                session.sock = SimpleNamespace(sendall=packets.append, shutdown=lambda how: None)
                original = session.start_talk
                def start():
                    original()
                    body = bytearray(20)
                    struct.pack_into('<H', body, 0, 1)
                    struct.pack_into('<I', body, 4, 8000)
                    struct.pack_into('<HH', body, 12, 31257, 1)
                    struct.pack_into('<H', body, 18, 16)
                    hub.talkback.observe(session, [(332, bytes(body))])
                session.start_talk = start
                sessions.append(session)
                hub.session = session
                hub.loop.call_soon_threadsafe(hub._dispatch, generation, hub._state, True)
                number = 0
                try:
                    while not stop.wait(.04):
                        video = av.VideoFrame(64, 48, 'yuv420p')
                        for index, plane in enumerate(video.planes):
                            plane.update(bytes([32 + number % 150 if index == 0 else 128]) * plane.buffer_size)
                        video.pts, video.time_base = number * 3600, Fraction(1, 90000)
                        audio = av.AudioFrame(format='s16', layout='mono', samples=320)
                        audio.sample_rate, audio.pts, audio.time_base = 8000, number * 320, Fraction(1, 8000)
                        audio.planes[0].update(bytes(640))
                        jpeg = io.BytesIO()
                        video.to_image().save(jpeg, format='JPEG')
                        for callback, args in ((hub._frame, ('video', video)),
                                (hub._frame, ('audio', audio)), (hub._image, (jpeg.getvalue(),))):
                            hub.loop.call_soon_threadsafe(hub._dispatch, generation, callback, *args)
                        number += 1
                finally:
                    hub.talkback.media_closed(session)
                    session.closed.set()
            hub._worker = worker
            cameras = []
            await modules['camera'].async_setup_entry(hass, entry, cameras.extend)
            main_camera, camera = cameras
            for entity in cameras:
                entity.hass = hass
                entity.entity_id = registry.async_get_or_create('camera', 'welcomeeye_local',
                    entity.unique_id, config_entry=entry).entity_id
            image = modules['image'].WelcomeEyeSecondarySnapshotImage(hass, secondary)
            image.entity_id = registry.async_get_or_create('image', 'welcomeeye_local',
                image.unique_id, config_entry=entry).entity_id
            image.capture.entity_id = image.entity_id
            hass.data[DATA_DOMAIN_PLATFORM_ENTITIES] = {('camera', 'welcomeeye_local'):
                {item.entity_id: item for item in cameras}}
            modules['services'].async_setup_services(hass)
            permission = {'read': True, 'control': True}
            connection = SimpleNamespace(user=SimpleNamespace(permissions=SimpleNamespace(
                check_entity=lambda entity_id, policy: permission['read'] if policy == POLICY_READ
                    else permission['control'] if policy == POLICY_CONTROL else False)),
                send_result=Mock(), send_error=Mock(), send_event=Mock(), subscriptions={})
            hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=connection.user))
            connection.user.is_admin = False
            async def capture():
                return await hass.services.async_call('welcomeeye_local', 'capture_snapshot',
                    {'entity_id': camera.entity_id, 'save_to_media': True}, blocking=True,
                    return_response=True, context=Context(user_id='synthetic-user'))
            try:
                await capture()
            except HomeAssistantError: pass
            else: raise AssertionError('Snapshot started media without an active viewer')
            assert not sessions and hub.thread is None

            config = (RTCConfiguration(iceServers=[]), {'ice_server_source': 'synthetic_loopback',
                'ice_server_count': 0, 'stun_server_count': 0, 'turn_server_count': 0, 'turn_available': False})
            with ExitStack() as stack:
                stack.enter_context(patch.object(modules['client'], 'discover', side_effect=AssertionError('No device discovery')))
                stack.enter_context(patch.object(modules['rtc'], '_ice_configuration', return_value=config))
                stack.enter_context(patch.object(aioice.ice, 'get_host_addresses', return_value=['127.0.0.1']))
                stack.enter_context(patch.object(modules['player'], 'get_camera_from_entity_id', return_value=camera))
                stack.enter_context(patch.object(camera, 'async_get_webrtc_client_configuration', return_value=SimpleNamespace(
                    to_frontend_dict=lambda: {'ice_servers': []})))
                for allowed in (False, True):
                    permission['control'] = allowed
                    modules['player'].player_config(hass, connection, {'id': 1, 'entity_id': camera.entity_id})
                    await hass.async_block_till_done(wait_background_tasks=True)
                    assert connection.send_result.call_args.args[1]['microphone_allowed'] is allowed
                browser = RTCPeerConnection(RTCConfiguration(iceServers=[]))
                microphone = Microphone()
                browser.addTransceiver('video', direction='recvonly')
                browser.addTransceiver(microphone, direction='sendrecv')
                channel = browser.createDataChannel('welcomeeye-control')
                received, consumers = {'video': 0, 'audio': 0}, []
                async def consume(track):
                    while True:
                        await track.recv()
                        received[track.kind] += 1
                @browser.on('track')
                def on_track(track): consumers.append(asyncio.create_task(consume(track)))
                try:
                    await browser.setLocalDescription(await browser.createOffer())
                    messages = []
                    await camera.rtc.offer(browser.localDescription.sdp, 'legacy-trial', messages.append, allow_talk=True)
                    assert len(messages) == 1 and isinstance(messages[0], WebRTCAnswer), messages
                    await browser.setRemoteDescription(RTCSessionDescription(messages[0].answer, 'answer'))
                    await wait_for(lambda: min(received.values()) >= 2 and channel.readyState == 'open')
                    channel.send(json.dumps({'type': 'heartbeat'}))
                    channel.send(json.dumps({'type': 'microphone', 'id': 2, 'enabled': True}))
                    await wait_for(lambda: secondary.talkback.active and secondary.talkback.diagnostics['frames_sent'] > 1)
                    assert len(sessions) == 1 and sessions[0].channel == 17
                    audio_packets = [list(modules['protected'].parse_tlvs(packet[8:])) for packet in packets]
                    audio_packets = [parts for parts in audio_packets if parts[0][0] == 97]
                    assert audio_packets and all(parts[0][1] == struct.pack('<B3xI', 18, 0) for parts in audio_packets)
                    assert all([kind for kind, _ in parts] == [97, 98] for parts in audio_packets)
                    permission['control'] = False
                    before = secondary.manual_snapshot.diagnostics['requests']
                    try: await capture()
                    except HomeAssistantError: pass
                    else: raise AssertionError('Unauthorized snapshot accepted')
                    assert secondary.manual_snapshot.diagnostics['requests'] == before
                    permission['control'] = True
                    session, consumer_count = hub.session, len(hub.consumers)
                    response = (await capture())[camera.entity_id]
                    assert response['saved'] and response['channel'] == 2
                    assert '_channel_2/' in response['filename']
                    assert (await image.async_image()).startswith(b'\xff\xd8')
                    assert image.image_last_updated is not None
                    assert len(sessions) == 1 and hub.session is session and len(hub.consumers) == consumer_count
                    assert secondary.talkback.active
                finally:
                    for task in consumers: task.cancel()
                    await asyncio.gather(*consumers, return_exceptions=True)
                    await camera.rtc.close('legacy-trial')
                    await browser.close()
                    microphone.stop()
                assert not hub.talkback.active and hub.talkback.owner is None
                assert not secondary.consumers and hub.thread is None and sessions[0].closed.is_set()
                await hub.acquire('primary-reopen')
                await wait_for(lambda: hub.connected)
                assert hub.session.channel == 16
                await hub.release('primary-reopen')
                assert not secondary.manual_snapshot._waiters
                await hub.stop()
                await main_camera.rtc.close_all()
                await camera.rtc.close_all()
            reports.append({'variant': variant, 'wire_video_channels': [item.channel for item in sessions],
                'microphone_audio_channel': 18, 'actual_webrtc_opus_to_lt_audio': 'pass',
                'active_snapshot_permission_and_private_media': 'pass', 'closed_snapshot_no_io': 'pass',
                'main_reopen_cleanup': 'pass'})
        await hass.async_stop(force=True)
        print(json.dumps({'validation': 'actual_HA_synthetic_legacy_channel2',
            'homeassistant': importlib.metadata.version('homeassistant'), 'python': sys.version.split()[0],
            'hardware_validated': False, 'device_io': 'synthetic', 'external_ice_servers': 0,
            'runs': reports}))


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(120):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
