"""Actual HA and aiortc R002 trial; device discovery, HTTPS and TCP are synthetic.

Run in the same clean HA Core images as verify_connect3_media_runtime.py.
The real R002 hub, QV session/reader, codecs, controller and WebRTC run here.
Only loopback ICE is allowed; this does not validate any physical device.
"""
import asyncio
from contextlib import ExitStack
from fractions import Fraction
import importlib
import importlib.metadata
import inspect
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import Context, HomeAssistant, SupportsResponse
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
        sys.path[:0] = [str(root), str(root / 'tests'), str(root / 'tools')]
        package = 'custom_components.welcomeeye_local'
        modules = {name: importlib.import_module(package + ('.' + name if name else '')) for name in (
            '', 'config_flow', 'camera', 'button', 'sensor', 'player', 'rtc', 'diagnostics',
            'connect3.live', 'connect3.cgi', 'r002.hub', 'r002.qv_discovery')}
        integration, config = modules[''], modules['config_flow']
        from homeassistant.auth.permissions.const import POLICY_READ
        from homeassistant.components.camera.webrtc import WebRTCAnswer
        from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
        import aioice.ice
        import av
        from qv_runtime_support import OPENING_CODE, PASSWORD, STREAM_KEY, SyntheticQVPeer
        from test_connect3_discovery import synthetic_packet, synthetic_record
        from test_r002_qv_discovery import FakeNetwork

        hass = HomeAssistant(temporary)
        hass.config_entries = ConfigEntries(hass, {})
        if hasattr(dr, 'async_setup'):
            dr.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        registry = er.async_get(hass)
        entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
            title='R002 synthetic QV runtime', unique_id='r002-synthetic-qv',
            data={'host': '192.0.2.1', 'protocol_family': 'r002_experimental',
                  'auth_code': PASSWORD, 'certificate_sha256': 'a' * 64}, options={},
            source='user', subentries_data=None, discovery_keys=MappingProxyType({}))
        hass.config_entries._entries[entry.entry_id] = entry
        identity = (entry.entry_id, entry.unique_id)
        created = []

        async def forward(config_entry, platforms):
            for platform in platforms:
                module = importlib.import_module(f'{package}.{platform.value}')
                await module.async_setup_entry(hass, config_entry, created.extend)

        qv = modules['r002.qv_discovery']
        with patch.object(hass.config_entries, 'async_forward_entry_setups', side_effect=forward), \
                patch.object(asyncio, 'open_connection', side_effect=AssertionError('startup TCP forbidden')), \
                patch.object(qv, '_open_listener', side_effect=AssertionError('startup UDP forbidden')), \
                patch.object(integration, 'WelcomeEyeHub', side_effect=AssertionError('legacy hub forbidden')):
            assert await integration.async_setup_entry(hass, entry)
            assert len(created) == 1 and isinstance(created[0], modules['sensor'].WelcomeEyeProtocolStatus)
            assert not entry.runtime_data.capabilities.camera and entry.runtime_data.live.task is None
            with patch.object(hass.config_entries, 'async_unload_platforms', AsyncMock(return_value=True)):
                assert await integration.async_unload_entry(hass, entry)
            flow = config.WelcomeEyeConfigFlow()
            flow.hass, flow.context = hass, {'source': 'reconfigure', 'entry_id': entry.entry_id}
            form = await flow.async_step_reconfigure()
            assert form['step_id'] == 'reconfigure' and PASSWORD not in str(form)
            supplied = form['data_schema']({'host': '192.0.2.1', 'experimental_video': True,
                'experimental_outputs': True, 'opening_code': OPENING_CODE})
            with patch.object(hass.config_entries, 'async_reload', AsyncMock()):
                assert (await flow.async_step_reconfigure(supplied))['type'] == 'abort'
            created.clear()
            assert await integration.async_setup_entry(hass, entry)
        hub = entry.runtime_data
        assert type(hub) is modules['r002.hub'].R002InvestigationHub
        assert (entry.entry_id, entry.unique_id) == identity and entry.data['auth_code'] == PASSWORD
        assert len(created) == 4
        camera = next(item for item in created if isinstance(item, modules['camera'].WelcomeEyeConnect3Camera))
        sensor = next(item for item in created if isinstance(item, modules['sensor'].WelcomeEyeProtocolStatus))
        buttons = sorted((item for item in created if isinstance(item, modules['button'].WelcomeEyeOpenButton)),
                         key=lambda item: item.output)
        assert [button.output for button in buttons] == [0, 1]
        for capability in ('camera', 'live_media', 'downstream_audio', 'talkback', 'gate', 'strike', 'r002_qv_read'):
            assert getattr(hub.capabilities, capability), capability
        for capability in ('connect3_read', 'local_ring', 'manual_snapshot'):
            assert not getattr(hub.capabilities, capability), capability
        assert camera._supports_native_async_webrtc
        assert await camera.stream_source() is None and await camera.async_camera_image() is None
        for entity in created:
            entity.hass = hass
        camera.entity_id, sensor.entity_id = 'camera.r002_qv_fixture', 'sensor.r002_qv_fixture_status'
        registry.async_get_or_create('camera', 'welcomeeye_local', camera.unique_id, config_entry=entry)
        button_ids = [registry.async_get_or_create('button', 'welcomeeye_local', button.unique_id,
                      config_entry=entry).entity_id for button in buttons]
        hass.data[DATA_DOMAIN_PLATFORM_ENTITIES] = {('sensor', 'welcomeeye_local'): {sensor.entity_id: sensor}}
        service_names = ('r002_check_access', 'r002_check_qv_certificate', 'r002_observe_doorbell')
        for name in service_names:
            assert hass.services.supports_response('welcomeeye_local', name) is SupportsResponse.ONLY
        user = SimpleNamespace(is_admin=False, permissions=SimpleNamespace(check_entity=Mock(return_value=True)))
        hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=user))
        with patch.object(hub, 'execute', AsyncMock()) as execute, \
                patch.object(hub.doorbell, 'execute', AsyncMock()) as observe:
            for name in service_names:
                data = {'entity_id': sensor.entity_id}
                if name == 'r002_observe_doorbell':
                    data['operation'] = 'status'
                for context, admin, control in ((Context(), True, True),
                        (Context(user_id='nonadmin'), False, True), (Context(user_id='denied'), True, False)):
                    user.is_admin, user.permissions.check_entity.return_value = admin, control
                    try:
                        await hass.services.async_call('welcomeeye_local', name,
                            data, blocking=True, return_response=True, context=context)
                    except HomeAssistantError:
                        pass
                    else:
                        raise AssertionError(f'{name} permitted without admin and entity control')
            execute.assert_not_awaited()
            observe.assert_not_awaited()
        user.is_admin = user.permissions.check_entity.return_value = True

        async def service(name, **data):
            result = await hass.services.async_call('welcomeeye_local', name,
                {'entity_id': sensor.entity_id, **data}, blocking=True, return_response=True,
                context=Context(user_id='admin'))
            return result[sensor.entity_id]

        async def accepted_access(host, auth, **kwargs):
            assert host == '192.0.2.1' and auth == PASSWORD
            assert kwargs['port'] == 443 and kwargs['certificate_sha256'] == 'a' * 64
            assert kwargs['operation'] == 'access'
            kwargs['diagnostics']['authentication_status'] = 'accepted'
            return {'streamkey_received': True, 'authentication': 'cgi_accepted'}

        # Exercise permitted dispatcher calls through the real R002 hub as well.
        # These read-only boundaries do not create a media session or save a pin.
        with patch.object(modules['r002.hub'], 'resolve_endpoint', AsyncMock(return_value={
                'cgi_port': 443, 'port': 34567, 'transport': 'r002_tcp'})) as resolve, \
                patch.object(modules['r002.hub'], 'read_device', side_effect=accepted_access) as access, \
                patch.object(modules['r002.hub'], 'inspect_certificate', AsyncMock(return_value={
                    'status': 'observed', 'certificate_sha256': 'b' * 64})) as certificate:
            result = await service('r002_check_access')
            assert result['status'] == 'ok' and result['device_authenticated']
            result = await service('r002_check_qv_certificate', include_details=True)
            assert result['certificate_sha256'] == 'b' * 64
            certificate.assert_awaited_once_with('192.0.2.1', port=443, include_details=True)
            access.assert_awaited_once()
            assert resolve.await_count == 2
        assert entry.data['certificate_sha256'] == 'a' * 64
        assert hub.live.task is None and not hub.consumers

        # Actual custom-card handlers enforce entity permissions independently
        # for microphone and each output button. No offer reaches media here.
        player = modules['player']
        allowed = set()
        connection = SimpleNamespace(user=SimpleNamespace(permissions=SimpleNamespace(
            check_entity=lambda entity_id, policy: policy == POLICY_READ or entity_id in allowed)),
            send_error=Mock(), send_result=Mock(), send_event=Mock(), subscriptions={})
        with patch.object(player, 'get_camera_from_entity_id', return_value=camera), \
                patch.object(camera, 'async_get_webrtc_client_configuration', return_value=SimpleNamespace(
                    to_frontend_dict=lambda: {})), patch.object(camera.rtc, 'offer', AsyncMock()) as offer:
            for granted in (set(), {camera.entity_id, button_ids[0]}, {camera.entity_id, *button_ids}):
                allowed.clear()
                allowed.update(granted)
                await inspect.unwrap(player.player_config)(hass, connection,
                    {'id': 1, 'entity_id': camera.entity_id})
                answer = connection.send_result.call_args.args[1]
                assert answer['microphone_allowed'] is (camera.entity_id in allowed)
                assert answer['buttons'] == {name: entity_id if entity_id in allowed else None
                    for name, entity_id in zip(('strike', 'gate'), button_ids)}
                await inspect.unwrap(player.player_offer)(hass, connection,
                    {'id': 2, 'entity_id': camera.entity_id, 'offer': 'synthetic-offer'})
                assert offer.call_args.kwargs['allow_talk'] is (camera.entity_id in allowed)
            await camera.async_handle_async_webrtc_offer('synthetic-native-offer', 'native', Mock())
            assert not offer.call_args.kwargs.get('allow_talk', False)

        # Only an independently constructed synthetic discovery record enables
        # media; exercise the real decoder and R002 endpoint policy each cycle.
        record = synthetic_record()
        record[0x188:0x19c] = b'IDS9417AW'.ljust(20, b'\0')
        struct.pack_into('<H', record, 0x78, 0)
        struct.pack_into('<H', record, 0x1cc, 0)
        discovery_events = [(synthetic_packet(record), ('192.0.2.1', 5000), 5003)]
        networks = []
        peers, stages = [], []
        cycle = 0

        async def open_discovery(collection, port):
            if port == 5001:
                networks.append(FakeNetwork(discovery_events, module=qv))
            return await networks[-1].open(collection, port)

        def discovery_count():
            return sum(len(network.sent) for network in networks)

        async def read_material(host, auth, **kwargs):
            assert host == '192.0.2.1' and auth == PASSWORD
            assert kwargs['port'] == 443 and kwargs['certificate_sha256'] == 'a' * 64
            assert discovery_count() == len([peer for peer in peers if peer.mode == 'live']) + 1
            stages.append('pinned_https_streamkey')
            kwargs['diagnostics']['authentication_status'] = 'accepted'
            return modules['connect3.cgi'].StreamMaterial(STREAM_KEY)

        async def open_peer(host, port, **kwargs):
            assert host == '192.0.2.1' and port == 34567 and 'ssl' not in kwargs
            assert stages and stages[-1] == 'pinned_https_streamkey'
            peer = SyntheticQVPeer(audio_codec=8 if cycle == 1 else 4)
            peers.append(peer)
            return peer.reader, peer

        class Microphone(AudioStreamTrack):
            def __init__(self):
                super().__init__()
                self.samples = 0
                self.started = asyncio.get_running_loop().time()

            async def recv(self):
                await asyncio.sleep(max(0, self.started + self.samples / 48000 - asyncio.get_running_loop().time()))
                frame = av.AudioFrame(format='s16', layout='mono', samples=960)
                frame.sample_rate, frame.pts, frame.time_base = 48000, self.samples, Fraction(1, 48000)
                frame.planes[0].update(struct.pack('<960h', *([1000, -1000] * 480)))
                self.samples += 960
                return frame

        async def wait_for(predicate):
            async with asyncio.timeout(10):
                while not predicate():
                    await asyncio.sleep(.01)

        rtc_config = (RTCConfiguration(iceServers=[]), {'ice_server_source': 'synthetic_loopback',
            'ice_server_count': 0, 'stun_server_count': 0, 'turn_server_count': 0, 'turn_available': False})
        cycles = []
        saved_entry = json.dumps([dict(entry.data), dict(entry.options)])
        with ExitStack() as stack:
            stack.enter_context(patch.object(qv, '_open_listener', side_effect=open_discovery))
            stack.enter_context(patch.object(qv, 'TIMEOUT', .02))
            read = stack.enter_context(patch.object(modules['connect3.live'], 'read_stream_material', side_effect=read_material))
            connect = stack.enter_context(patch.object(asyncio, 'open_connection', side_effect=open_peer))
            stack.enter_context(patch.object(modules['rtc'], '_ice_configuration', return_value=rtc_config))
            stack.enter_context(patch.object(aioice.ice, 'get_host_addresses', return_value=['127.0.0.1']))
            for cycle in range(3):
                browser = RTCPeerConnection(RTCConfiguration(iceServers=[]))
                frames = {'video': [], 'audio': []}
                consumers, messages, replies = [], [], {}
                ready = asyncio.Event()
                channel = browser.createDataChannel('welcomeeye-control')
                channel.on('open', ready.set)

                @channel.on('message')
                def receive_reply(raw):
                    value = json.loads(raw)
                    if value.get('type') == 'microphone':
                        replies[value['id']] = value

                async def consume(track):
                    while True:
                        frame = await track.recv()
                        if track.kind == 'video':
                            assert (frame.width, frame.height) == (64, 48)
                        else:
                            assert frame.sample_rate == 48000
                        frames[track.kind].append(frame.pts)

                @browser.on('track')
                def track_started(track):
                    consumers.append(asyncio.create_task(consume(track)))

                async def media(video, audio):
                    await wait_for(lambda: (len(frames['video']) >= video and len(frames['audio']) >= audio)
                        or any(task.done() for task in consumers))
                    for task in consumers:
                        if task.done():
                            task.result()
                            raise AssertionError('Media consumer ended')

                async def microphone_request(identifier, enabled):
                    channel.send(json.dumps({'type': 'microphone', 'id': identifier, 'enabled': enabled}))
                    await wait_for(lambda: identifier in replies)
                    assert replies[identifier]['enabled'] is enabled and not replies[identifier].get('error')

                async def heartbeat():
                    await ready.wait()
                    while channel.readyState == 'open':
                        channel.send(json.dumps({'type': 'heartbeat'}))
                        await asyncio.sleep(.5)

                browser.addTransceiver('video', direction='recvonly')
                microphone = Microphone()
                browser.addTransceiver(microphone, direction='sendrecv')
                identifier, hold = f'r002-synthetic-{cycle}', object()
                beating = asyncio.create_task(heartbeat())
                try:
                    async with asyncio.timeout(30):
                        await browser.setLocalDescription(await browser.createOffer())
                        await camera.rtc.offer(browser.localDescription.sdp, identifier, messages.append, allow_talk=True)
                        assert len(messages) == 1 and isinstance(messages[0], WebRTCAnswer), messages
                        await browser.setRemoteDescription(RTCSessionDescription(messages[0].answer, 'answer'))
                        await ready.wait()
                        await media(2, 2)
                        assert all(values[1] > values[0] for values in frames.values())
                        live_peer = peers[-1]
                        assert live_peer.mode == 'live' and discovery_count() == cycle + 1
                        assert hub.live.session._transport == 'r002_tcp'
                        assert not hub.live.observation['media_tls_verified']
                        assert hub.live.observation['audio']['decode_errors'] == 0
                        assert hub.live.observation['audio']['codec_id'] == (8 if cycle == 1 else 4)
                        assert hub.live.session_count == cycle + 1 and not hub.talkback.active
                        await microphone_request(1, True)
                        peer = peers[-1]
                        await wait_for(lambda: peer.audio_packets > 0)
                        assert peer.mode == 'talk' and hub.talkback.diagnostics['frames_sent'] > 0
                        assert hub.webrtc_diagnostics['inbound_audio_frames_received'] > 0
                        peer.teardown_release.clear()
                        channel.send(json.dumps({'type': 'microphone', 'id': 2, 'enabled': False}))
                        await peer.teardown_started.wait()
                        count = peer.audio_packets
                        assert not hub.talkback.active and not peer.closed
                        await media(len(frames['video']) + 3, len(frames['audio']) + 3)
                        assert peer.audio_packets == count
                        peer.teardown_release.set()
                        await wait_for(lambda: 2 in replies)
                        assert replies[2]['enabled'] is False and peer.closed
                        assert peer.teardowns == peer.close_count == 1
                        if cycle == 0:
                            session_before = hub.live.session
                            with patch.object(hub, 'acquire', side_effect=AssertionError('Observation must not acquire')):
                                for operation, status in (('start', 'observing'), ('mark', 'observing'),
                                                          ('status', 'observing'), ('stop', 'finished')):
                                    observed = await service('r002_observe_doorbell', operation=operation, duration=30)
                                    assert observed['observation']['status'] == status
                                    assert observed['ring_events_emitted'] == 0
                            assert hub.live.session is session_before and len(hub.consumers) == 1
                            for button in buttons:
                                await button.async_press()
                                assert len(hub.consumers) == 1
                            assert live_peer.outputs == [1, 2]
                            live_peer.reject_output = True
                            try:
                                await buttons[0].async_press()
                            except HomeAssistantError:
                                pass
                            else:
                                raise AssertionError('Rejected synthetic output confirmed')
                            assert live_peer.outputs == [1, 2, 1]
                            assert hub.control.diagnostics()['request_send_attempt_count'] == 3
                            assert not hub.control.diagnostics()['physical_activation_verified']
                        await microphone_request(3, True)
                        await wait_for(lambda: peers[-1].audio_packets > 0)
                        await hub.acquire(hold)
                        assert len(hub.consumers) == 2 and hub.live.session_count == cycle + 1
                        await camera.rtc.close(identifier)
                        assert not hub.talkback.active and peers[-1].closed
                        assert hub.connected and not live_peer.closed and len(hub.consumers) == 1
                        await hub.release(hold)
                finally:
                    for peer in peers:
                        peer.teardown_release.set()
                    beating.cancel()
                    await asyncio.gather(beating, return_exceptions=True)
                    for task in consumers:
                        task.cancel()
                    await asyncio.gather(*consumers, return_exceptions=True)
                    await camera.rtc.close(identifier)
                    await hub.release(hold)
                    await browser.close()
                    microphone.stop()
                assert not hub.consumers and hub.live.task is None and live_peer.closed
                assert not camera.rtc.viewers and hub.talkback.owner is None
                cycles.append({'video_frames': len(frames['video']), 'audio_frames': len(frames['audio']),
                    'audio_codec': 'aac' if cycle == 1 else 'pcm_alaw', 'shared_media_sessions': 1,
                    'dedicated_talk_sessions': 2, 'microphone_off_during_teardown': 'pass'})
            assert read.await_count == discovery_count() == 3 and connect.await_count == len(peers) == 9
            assert all(transport.close.call_count == 1 and protocol.closed.done()
                for network in networks for transport, protocol in network.endpoints)
            assert all(peer.closed and peer.teardowns == peer.close_count == 1 for peer in peers)
            assert all(peer.producer is None or peer.producer.done() for peer in peers)
            report = await modules['diagnostics'].async_get_config_entry_diagnostics(hass, entry)
            assert 'r002' in report and 'connect3' not in report
            assert report['r002']['audio']['decoded_frames'] > 0
            assert report['r002']['webrtc']['downstream_frames_queued']['audio'] > 0
            text = json.dumps(report)
            for secret in (PASSWORD, OPENING_CODE, STREAM_KEY, '192.0.2.1', 'a' * 64, 'SYNTHETIC_PRIVATE_UID'):
                assert secret not in text
        assert saved_entry == json.dumps([dict(entry.data), dict(entry.options)])
        assert (entry.entry_id, entry.unique_id) == identity
        # Mixed family unload must remove only the R002 service set.
        other = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
            title='Connect3 service isolation', unique_id='connect3-runtime-isolation',
            data={'host': '192.0.2.2', 'protocol_family': 'connect3_qv_experimental'}, options={},
            source='user', subentries_data=None, discovery_keys=MappingProxyType({}))
        hass.config_entries._entries[other.entry_id] = other
        with patch.object(hass.config_entries, 'async_forward_entry_setups', side_effect=forward), \
                patch.object(asyncio, 'open_connection', side_effect=AssertionError('unload TCP forbidden')), \
                patch.object(qv, '_open_listener', side_effect=AssertionError('unload UDP forbidden')), \
                patch.object(hass.config_entries, 'async_unload_platforms', AsyncMock(return_value=True)):
            assert await integration.async_setup_entry(hass, other)
            assert hass.services.has_service('welcomeeye_local', 'connect3_check_access')
            assert await integration.async_unload_entry(hass, entry)
            assert all(not hass.services.has_service('welcomeeye_local', name) for name in service_names)
            assert hass.services.has_service('welcomeeye_local', 'connect3_check_access')
            assert await integration.async_unload_entry(hass, other)
        assert not hub.close_listeners and not hub.frame_listeners
        assert not camera.rtc.tasks and not camera.rtc.offers
        assert hub.talkback._writer is None and hub.control.diagnostics()['closed']
        await hass.async_stop(force=True)
        print(json.dumps({'validation': 'actual_HA_R002_QV_TCP_WebRTC',
            'homeassistant_version': importlib.metadata.version('homeassistant'),
            'python_version': sys.version.split()[0], 'aiortc_version': importlib.metadata.version('aiortc'),
            'device_io': 'synthetic_boundaries', 'hardware_validated': False, 'external_ice_servers': 0,
            'startup_offline': 'pass', 'reconfigure_identity': 'pass', 'discovery_before_pinned_cgi': 'pass',
            'custom_card_control_permissions': 'pass', 'native_camera_receive_only': 'pass',
            'service_permissions': 'pass', 'single_shot_output_buttons': 'pass',
            'mixed_family_service_unload': 'pass', 'cycles': cycles, 'cleanup': 'pass'}))


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(180):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
