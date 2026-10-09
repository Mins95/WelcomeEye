"""Actual HA + aiortc multichannel audio/video, using a synthetic QV peer.

Run in the same clean HA Core images as verify_connect3_media_runtime.py.
Real config flows, entities, permissions, QV framing, decoding and WebRTC run.
Only loopback ICE is allowed; this is not evidence about an outdoor-panel map.
"""
import asyncio
from contextlib import ExitStack
import importlib
import importlib.metadata
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.requirements import pip_kwargs
from homeassistant.util.package import install_package


async def main(root):
    with tempfile.TemporaryDirectory() as temporary:
        manifest = json.loads((root / 'custom_components/welcomeeye_local/manifest.json').read_text())
        for requirement in manifest['requirements']:
            assert await asyncio.to_thread(install_package, requirement, **pip_kwargs(temporary))
        sys.path[:0] = [str(root), str(root / 'tools')]
        package = 'custom_components.welcomeeye_local'
        modules = {name: importlib.import_module(package + ('.' + name if name else '')) for name in (
            '', 'config_flow', 'camera', 'button', 'player', 'rtc', 'diagnostics',
            'connect3.live', 'connect3.cgi', 'connect3.session', 'connect3.trust',
            'connect3.channel2', 'connect3.talk', 'r002.qv_discovery')}
        integration, config, trust = modules[''], modules['config_flow'], modules['connect3.trust']
        from homeassistant.auth.permissions.const import POLICY_READ, POLICY_CONTROL
        from homeassistant.components.camera.webrtc import WebRTCAnswer
        from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
        import aioice.ice
        from qv_runtime_support import OPENING_CODE, PASSWORD, STREAM_KEY, SyntheticQVPeer, crypt

        hass = HomeAssistant(temporary)
        hass.config_entries = ConfigEntries(hass, {})
        if hasattr(dr, 'async_setup'):
            dr.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        registry = er.async_get(hass)
        saved = {'host': '192.0.2.1', 'protocol_family': 'connect3_qv_experimental',
            'auth_code': PASSWORD, 'opening_code': OPENING_CODE, 'credential_source': 'manual',
            'certificate_sha256': 'a' * 64, 'media_certificate_sha256': 'b' * 64,
            'cgi_port': 443, 'media_port': 34567, 'media_transport': 'connect3_tcp',
            'media_tcp_approved': True, 'experimental_video': False, 'second_channel_enabled': True,
            'experimental_outputs': True, 'experimental_tcp_controls': True,
            'trust_endpoint': {'host': '192.0.2.1', 'cgi_port': 443, 'media_port': 34567,
                               'media_transport': 'connect3_tcp'},
            'tls_certificate_expires': {'cgi': '2099-01-01T00:00:00+00:00'}}
        entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
            title='Connect 3 synthetic channel trial', unique_id='connect3-synthetic-channel2',
            data=saved, options={'retained_fixture': True}, source='user', subentries_data=None,
            discovery_keys=MappingProxyType({}))
        hass.config_entries._entries[entry.entry_id] = entry
        identity = (entry.entry_id, entry.unique_id)
        created = []

        async def forward(config_entry, platforms):
            for platform in platforms:
                module = importlib.import_module(f'{package}.{platform.value}')
                await module.async_setup_entry(hass, config_entry, created.extend)

        def inspected(host, cgi_port=443, media_port=8443, **kwargs):
            assert host == saved['host'] and cgi_port == 443
            return trust.TrustInspection(trust.EndpointTrust('pinned', 'a' * 64,
                validity_status='valid', not_valid_after='2099-01-01T00:00:00+00:00'),
                trust.EndpointTrust('not_applicable'))

        async def reconfigure(enabled):
            flow = config.WelcomeEyeConfigFlow()
            flow.hass, flow.context = hass, {'source': 'reconfigure', 'entry_id': entry.entry_id}
            form = await flow.async_step_reconfigure()
            assert form['step_id'] == 'connect3_reconfigure' and PASSWORD not in str(form)
            supplied = form['data_schema']({'host': saved['host'], 'experimental_video': enabled})
            with patch.object(config, 'inspect_trust', AsyncMock(side_effect=inspected)), \
                    patch.object(hass.config_entries, 'async_reload', AsyncMock()):
                result = await flow.async_step_connect3_reconfigure(supplied)
            assert result['type'] == 'abort', result
            assert entry.data['experimental_video'] is enabled
            assert 'experimental_channel2' not in entry.data
            assert (entry.entry_id, entry.unique_id) == identity
            for key, value in saved.items():
                assert entry.data[key] == (enabled if key == 'experimental_video' else value), key
            assert dict(entry.options) == {'retained_fixture': True}

        def cameras():
            return [entity for entity in created
                    if isinstance(entity, modules['camera'].WelcomeEyeConnect3Camera)]

        async def setup():
            created.clear()
            with patch.object(hass.config_entries, 'async_forward_entry_setups', side_effect=forward):
                assert await integration.async_setup_entry(hass, entry)
            for entity in created:
                entity.hass = hass
            for camera in cameras():
                registered = registry.async_get_or_create('camera', 'welcomeeye_local',
                    camera.unique_id, config_entry=entry)
                camera.entity_id = registered.entity_id
                assert camera._supports_native_async_webrtc
                assert await camera.stream_source() is None
                assert await camera.async_camera_image() is None

        async def unload():
            with patch.object(hass.config_entries, 'async_unload_platforms', AsyncMock(return_value=True)):
                assert await integration.async_unload_entry(hass, entry)

        # Only an enabled secondary source exposes a second camera. Explicit
        # viewer acquisition, never startup/still polling, starts device I/O.
        with patch.object(asyncio, 'open_connection', side_effect=AssertionError('startup TCP forbidden')), \
                patch.object(modules['r002.qv_discovery'], '_open_listener',
                             side_effect=AssertionError('startup UDP forbidden')), \
                patch.object(modules['connect3.live'], 'read_stream_material',
                             side_effect=AssertionError('startup CGI forbidden')):
            await setup()
            assert not cameras()
            assert getattr(entry.runtime_data, 'channel2', None) is None
            await unload()
            await reconfigure(True)
            await setup()

        hub = entry.runtime_data
        main_camera = next(camera for camera in cameras() if camera.unique_id == f'{entry.unique_id}_camera')
        trial_camera = next(camera for camera in cameras() if camera.unique_id == f'{entry.unique_id}_camera_channel_2')
        assert len(cameras()) == 2
        main_entity_id = main_camera.entity_id
        assert trial_camera.hub is hub.channel2 and hub.channel2.entry is entry
        assert main_camera.device_info == trial_camera.device_info
        assert trial_camera.entity_category is None and trial_camera.entity_registry_enabled_default
        assert trial_camera.translation_key == 'channel_2_trial'
        trial_entity_id = trial_camera.entity_id
        registry.async_update_entity(trial_entity_id, name='Retained trial camera name')
        assert hub.channel2.capabilities.downstream_audio
        for capability in ('talkback', 'strike', 'gate', 'local_ring',
                           'manual_snapshot', 'ring_image_capture', 'last_ring_image', 'last_snapshot'):
            assert not getattr(hub.channel2.capabilities, capability), capability
        assert hub.capabilities.talkback and hub.capabilities.strike and hub.capabilities.gate
        buttons = [entity for entity in created if isinstance(entity, modules['button'].WelcomeEyeOpenButton)]
        assert len(buttons) == 2 and all(button.hub is hub for button in buttons)
        for button in buttons:
            button.entity_id = registry.async_get_or_create('button', 'welcomeeye_local',
                button.unique_id, config_entry=entry).entity_id
        assert hub.live.task is None and hub.channel2.live.task is None

        # Actual HA websocket dispatch retains permissions and identifies the
        # primary target of its validated outputs, even on the secondary view.
        permission = {'read': True, 'control': True}
        connection = SimpleNamespace(user=SimpleNamespace(permissions=SimpleNamespace(
            check_entity=lambda entity_id, policy: permission['read'] if policy == POLICY_READ
                else permission['control'] if policy == POLICY_CONTROL else False)),
            send_result=Mock(), send_error=Mock(), send_event=Mock(), subscriptions={})
        async def dispatch(handler, message):
            handler(hass, connection, message)
            await hass.async_block_till_done(wait_background_tasks=True)
        player = modules['player']
        camera_by_id = {main_entity_id: main_camera, trial_entity_id: trial_camera}
        with patch.object(player, 'get_camera_from_entity_id', side_effect=lambda hass, entity_id: camera_by_id[entity_id]), \
                patch.object(trial_camera, 'async_get_webrtc_client_configuration', return_value=SimpleNamespace(
                    to_frontend_dict=lambda: {'ice_servers': []})), \
                patch.object(trial_camera.rtc, 'offer', AsyncMock()) as offer:
            for control in (False, True):
                permission['control'] = control
                await dispatch(player.player_config, {'id': 1, 'entity_id': trial_entity_id})
                frontend = connection.send_result.call_args.args[1]
                assert frontend['microphone_allowed'] is False
                assert frontend['buttons_channel'] == 1
                assert {item['entity_id'] for item in frontend['channels']} == set(camera_by_id)
                assert all(bool(value) is control for value in frontend['buttons'].values())
                await dispatch(player.player_offer, {'id': 2, 'entity_id': trial_entity_id,
                                                      'offer': 'synthetic-permission-offer'})
                assert offer.await_args.kwargs['allow_talk'] is False
            permission['read'] = False
            offer.reset_mock()
            await dispatch(player.player_offer, {'id': 3, 'entity_id': trial_entity_id,
                                                  'offer': 'synthetic-denied-offer'})
            offer.assert_not_awaited()
            assert connection.send_error.call_args.args[1] == 'unauthorized'
        permission['read'] = True
        assert hub.live.task is None and hub.channel2.live.task is None

        peers = []
        expected_channel = 1
        class ChannelPeer(SyntheticQVPeer):
            def __init__(self, expected):
                super().__init__()
                self.expected_channel, self.play_channels = expected, []

            def write(self, data):
                if self.mode is not None:
                    header = crypt(data[:32], decrypt=True)
                    command = header[0]
                    assert command in (0, 1, 7), 'No microphone or output command is allowed in this trial'
                    if command == 1:
                        channel = struct.unpack_from('<H', header, 13)[0]
                        assert channel == self.expected_channel and header[16] == 1
                        self.play_channels.append(channel)
                super().write(data)

        async def read_material(host, auth, *, diagnostics, **kwargs):
            assert host == saved['host'] and auth == PASSWORD
            assert kwargs['port'] == 443 and kwargs['certificate_sha256'] == 'a' * 64
            diagnostics.update(authentication_status='accepted', tls_verified=True, tls_policy='certificate_pin')
            return modules['connect3.cgi'].StreamMaterial(STREAM_KEY)

        async def open_peer(host, port, observation):
            assert host == saved['host'] and port == 34567
            peer = ChannelPeer(expected_channel)
            peers.append(peer)
            observation.update(media_tcp_connected=True, media_tls_verified=False, media_transport='connect3_tcp')
            return peer.reader, peer

        async def wait_for(predicate):
            async with asyncio.timeout(12):
                while not predicate():
                    await asyncio.sleep(.01)

        async def refused_acquire(target, owner, reads):
            before = (reads.await_count, len(peers))
            try:
                await target.acquire(owner)
            except RuntimeError:
                pass
            else:
                await target.release(owner)
                raise AssertionError('Concurrent main and trial acquisition was accepted')
            assert (reads.await_count, len(peers)) == before
            assert owner not in target.consumers

        async def browser_open(camera, identifier):
            browser = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            received, consumers = {'video': [], 'audio': []}, []
            channel = browser.createDataChannel('welcomeeye-control')
            browser.addTransceiver('video', direction='recvonly')
            browser.addTransceiver('audio', direction='sendrecv')
            async def consume(track):
                while True:
                    frame = await track.recv()
                    if track.kind == 'video':
                        assert (frame.width, frame.height) == (64, 48)
                    else:
                        assert frame.samples > 0
                    received[track.kind].append(frame.pts)
            @browser.on('track')
            def track_started(track):
                consumers.append(asyncio.create_task(consume(track)))
            try:
                await browser.setLocalDescription(await browser.createOffer())
                messages = []
                # Native and custom cards use the same per-camera RTC owner.
                await camera.async_handle_async_webrtc_offer(browser.localDescription.sdp, identifier, messages.append)
                assert len(messages) == 1 and isinstance(messages[0], WebRTCAnswer), messages
                await browser.setRemoteDescription(RTCSessionDescription(messages[0].answer, 'answer'))
                await wait_for(lambda: min(map(len, received.values())) >= 2 and channel.readyState == 'open')
                assert received['video'][1] > received['video'][0]
                assert set(camera.rtc.viewers[identifier].tracks) == {'video', 'audio'}
                return browser, channel, consumers, received
            except BaseException:
                for task in consumers:
                    task.cancel()
                await asyncio.gather(*consumers, return_exceptions=True)
                await camera.rtc.close(identifier)
                await browser.close()
                raise

        async def browser_close(camera, identifier, current):
            browser, channel, consumers, received = current
            for task in consumers:
                task.cancel()
            await asyncio.gather(*consumers, return_exceptions=True)
            await camera.rtc.close(identifier)
            await browser.close()
            assert browser.connectionState == 'closed'

        configuration = (RTCConfiguration(iceServers=[]), {'ice_server_source': 'synthetic_loopback',
            'ice_server_count': 0, 'stun_server_count': 0, 'turn_server_count': 0, 'turn_available': False})
        with ExitStack() as stack:
            read = stack.enter_context(patch.object(modules['connect3.live'], 'read_stream_material',
                                                   AsyncMock(side_effect=read_material)))
            stack.enter_context(patch.object(modules['connect3.session'], 'open_connect3_media_tcp', side_effect=open_peer))
            stack.enter_context(patch.object(asyncio, 'open_connection', side_effect=AssertionError('device TCP forbidden')))
            stack.enter_context(patch.object(modules['r002.qv_discovery'], '_open_listener',
                                             side_effect=AssertionError('device UDP forbidden')))
            stack.enter_context(patch.object(modules['connect3.talk'], 'open_connect3_media_tcp',
                                             side_effect=AssertionError('microphone TCP forbidden')))
            stack.enter_context(patch.object(modules['rtc'], '_ice_configuration', return_value=configuration))
            stack.enter_context(patch.object(aioice.ice, 'get_host_addresses', return_value=['127.0.0.1']))

            await hub.acquire('main-first')
            await refused_acquire(hub.channel2, 'trial-while-main', read)
            await hub.release('main-first')
            assert peers[-1].closed

            expected_channel = 2
            trial = await browser_open(trial_camera, 'trial-manual')
            try:
                await refused_acquire(hub, 'main-while-trial', read)
                before = (read.await_count, len(peers))
                trial[1].send(json.dumps({'type': 'microphone', 'id': 10, 'enabled': True}))
                before_frames = len(trial[3]['video'])
                await wait_for(lambda: len(trial[3]['video']) >= before_frames + 3)
                assert not trial_camera.rtc.viewers['trial-manual'].mic_enabled
                assert not hub.talkback.active and hub.talkback.owner is None
                assert (read.await_count, len(peers)) == before
                assert hub.channel2.live.observation['decoded_frames'] >= 2
                assert hub.channel2.live.observation['audio']['decoded_frames'] >= 2
                assert not hub.channel2.webrtc_diagnostics.get('microphone_start_requests')
            finally:
                await browser_close(trial_camera, 'trial-manual', trial)
            assert not hub.channel2.consumers and hub.channel2.live.task is None and peers[-1].closed

            assert not hasattr(modules['connect3.channel2'], 'TRIAL_TIMEOUT_SECONDS')

            expected_channel = 1
            main = await browser_open(main_camera, 'main-after-trial')
            try:
                assert peers[-1].play_channels == [1]
                assert hub.live.observation['decoded_frames'] >= 2
            finally:
                await browser_close(main_camera, 'main-after-trial', main)

            expected_channel = 2
            trial = await browser_open(trial_camera, 'trial-unload')
            try:
                await unload()
                assert not trial_camera.rtc.viewers and trial_camera.rtc.closed
                assert not hub.channel2.consumers and hub.channel2.live.task is None
                assert not hub.channel2.frame_listeners and not hub.channel2.close_listeners
                assert not hub.consumers and hub.live.task is None
            finally:
                await browser_close(trial_camera, 'trial-unload', trial)
            assert read.await_count == len(peers) == 4
            assert [peer.play_channels for peer in peers] == [[1], [2], [1], [2]]
            assert all(peer.closed and peer.close_count == 1 and peer.teardowns == 1 for peer in peers)
            assert all(not peer.outputs for peer in peers)
            assert entry.data['observed_media_channels']['channels'] == [1, 2]
            diagnostic_data = await modules['diagnostics'].async_get_config_entry_diagnostics(hass, entry)
            assert set(diagnostic_data['connect3']['channels']) == {'1', '2'}
            diagnostic = json.dumps(diagnostic_data)
            for secret in (PASSWORD, OPENING_CODE, STREAM_KEY, saved['host'], 'a' * 64, 'b' * 64):
                assert secret not in diagnostic

        with patch.object(asyncio, 'open_connection', side_effect=AssertionError('reload TCP forbidden')), \
                patch.object(modules['r002.qv_discovery'], '_open_listener',
                             side_effect=AssertionError('reload UDP forbidden')), \
                patch.object(modules['connect3.live'], 'read_stream_material',
                             side_effect=AssertionError('reload CGI forbidden')):
            # A regular reload retains the existing main camera and the
            # supplementary camera's registry identity and custom name.
            await setup()
            restored = next(camera for camera in cameras() if camera.unique_id.endswith('_camera_channel_2'))
            restored_main = next(camera for camera in cameras() if camera.unique_id == f'{entry.unique_id}_camera')
            assert restored_main.entity_id == main_entity_id
            assert restored.entity_id == trial_entity_id
            assert set(entry.runtime_data.confirmed_media_channels) == {1, 2}
            assert registry.async_get(trial_entity_id).name == 'Retained trial camera name'
            await unload()
            await reconfigure(False)
            await setup()
            assert not cameras() and getattr(entry.runtime_data, 'channel2', None) is None
            assert registry.async_get(trial_entity_id).name == 'Retained trial camera name'
            await unload()
        await hass.async_stop(force=True)
        print(json.dumps({'validation': 'actual_HA_synthetic_Connect3_channel2_WebRTC',
            'homeassistant_version': importlib.metadata.version('homeassistant'),
            'python_version': sys.version.split()[0], 'aiortc_version': importlib.metadata.version('aiortc'),
            'device_io': 'synthetic', 'external_ice_servers': 0, 'hardware_validated': False,
            'direct_camera_identity_and_reload': 'pass', 'no_startup_or_still_io': 'pass',
            'secondary_audio_and_websocket_permissions': 'pass', 'wire_channels': [1, 2, 1, 2],
            'exclusive_before_device_io': 'pass', 'channel_switch_and_active_unload': 'pass',
            'main_camera_after_trial': 'pass', 'diagnostic_privacy': 'pass'}))


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(120):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
