"""Real HA permissions, four-relay QV wire and passive channel-2 observation.

All device I/O is replaced at its transport boundary. No physical relay runs.
"""
import asyncio
from contextlib import ExitStack
from hashlib import sha256
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
        sys.path[:0] = [str(root), str(root / 'tools')]
        package = 'custom_components.welcomeeye_local'
        modules = {name: importlib.import_module(package + ('.' + name if name else '')) for name in (
            '', 'config_flow', 'button', 'sensor', 'diagnostics', 'connect3.trust',
            'connect3.live', 'connect3.cgi', 'connect3.session', 'connect3.control', 'r002.qv_discovery')}
        from qv_runtime_support import OPENING_CODE, PASSWORD, STREAM_KEY, SyntheticQVPeer, crypt
        from homeassistant.components import button as ha_button
        from homeassistant.auth.permissions.const import POLICY_CONTROL
        hass = HomeAssistant(temporary)
        hass.config_entries = ConfigEntries(hass, {})
        if hasattr(dr, 'async_setup'):
            dr.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        registry = er.async_get(hass)
        entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
            title='Synthetic four relay fixture', unique_id='synthetic-four-relay',
            data={'host': '192.0.2.1', 'protocol_family': 'connect3_qv_experimental',
                'auth_code': PASSWORD, 'opening_code': OPENING_CODE,
                'certificate_sha256': 'a' * 64, 'media_transport': 'connect3_tcp',
                'media_tcp_approved': True, 'experimental_tcp_controls': True,
                'experimental_video': True, 'experimental_outputs': True, 'second_channel_enabled': True,
                'trust_endpoint': {'host': '192.0.2.1', 'cgi_port': 443, 'media_port': 34567,
                                   'media_transport': 'connect3_tcp'}},
            options={}, source='user', subentries_data=None, discovery_keys=MappingProxyType({}))
        hass.config_entries._entries[entry.entry_id] = entry
        original_identity = entry.entry_id, entry.unique_id
        created = []
        peers = []
        read_count = 0

        async def forward(config_entry, platforms):
            for platform in platforms:
                await importlib.import_module(package + '.' + platform.value).async_setup_entry(
                    hass, config_entry, created.extend)

        trust = modules['connect3.trust']
        inspection = trust.TrustInspection(trust.EndpointTrust('pinned', 'a' * 64,
            validity_status='valid', not_valid_after='2099-01-01T00:00:00+00:00'),
            trust.EndpointTrust('not_applicable'))
        with patch.object(modules['config_flow'], 'inspect_trust', AsyncMock(return_value=inspection)), \
                patch.object(hass.config_entries, 'async_reload', AsyncMock()):
            flow = modules['config_flow'].WelcomeEyeConfigFlow()
            flow.hass, flow.context = hass, {'source': 'reconfigure', 'entry_id': entry.entry_id}
            requested = {'channel2_strike_trial_enabled': True, 'channel2_gate_trial_enabled': True}
            result = await flow.async_step_reconfigure({'host': '192.0.2.1', **requested})
            assert result['step_id'] == 'connect3_output_trials', result
            assert not entry.data.get('channel2_strike_trial_enabled')
            assert not entry.data.get('channel2_gate_trial_enabled')
            result = await flow.async_step_connect3_output_trials(result['data_schema'](requested))
            assert result['type'] == 'abort', result
        assert original_identity == (entry.entry_id, entry.unique_id)

        async def read_material(host, auth, *, diagnostics, **kwargs):
            nonlocal read_count
            assert host == '192.0.2.1' and auth == PASSWORD and kwargs['port'] == 443
            read_count += 1
            diagnostics.update(tls_verified=True, tls_policy='certificate_pin', authentication_status='accepted')
            return modules['connect3.cgi'].StreamMaterial(STREAM_KEY)

        async def open_peer(host, port, observation):
            assert host == '192.0.2.1' and port == 34567
            peer = SyntheticQVPeer(allowed_outputs=((1, 1), (1, 2), (2, 1), (2, 2)))
            peers.append(peer)
            observation.update(media_tcp_connected=True, media_tls_verified=False)
            return peer.reader, peer

        with ExitStack() as stack:
            stack.enter_context(patch.object(asyncio, 'open_connection', side_effect=AssertionError('device TCP forbidden')))
            stack.enter_context(patch.object(modules['r002.qv_discovery'], '_open_listener',
                                             side_effect=AssertionError('device UDP forbidden')))
            stack.enter_context(patch.object(modules['connect3.live'], 'read_stream_material', side_effect=read_material))
            stack.enter_context(patch.object(modules['connect3.session'], 'open_connect3_media_tcp', side_effect=open_peer))
            stack.enter_context(patch.object(hass.config_entries, 'async_forward_entry_setups', side_effect=forward))
            assert await modules[''].async_setup_entry(hass, entry)
            hub = entry.runtime_data
            assert read_count == 0 and not peers
            buttons = [item for item in created if isinstance(item, (
                modules['button'].WelcomeEyeOpenButton, modules['button'].WelcomeEyeSecondOutputButton))]
            assert len(buttons) == 4
            # The public setup registers the real press service in all three
            # supported HA versions; button.services exists only since 2026.10.
            assert await ha_button.async_setup(hass, {})
            component = hass.data[ha_button.DATA_COMPONENT]
            by_target = {}
            for button in buttons:
                button.hass = hass
                button.entity_id = registry.async_get_or_create('button', 'welcomeeye_local',
                    button.unique_id, config_entry=entry).entity_id
                # Real HA service dispatcher + real ButtonEntity press action.
                # Only state publication is stubbed; no entity platform UI runs here.
                button.async_write_ha_state = Mock()
                component._platforms['button'].entities[button.entity_id] = button
                component._entities[button.entity_id] = button
                assert component.get_entity(button.entity_id) is button
                by_target[button.extra_state_attributes['welcomeeye_output_target']] = button
            hass.data[DATA_DOMAIN_PLATFORM_ENTITIES] = {('button', 'welcomeeye_local'):
                {button.entity_id: button for button in buttons}}
            sensors = [item for item in created if isinstance(item, modules['sensor'].WelcomeEyeConnect3Status)]
            assert len(sensors) == 1
            sensor = sensors[0]
            sensor.hass = hass
            sensor.entity_id = registry.async_get_or_create('sensor', 'welcomeeye_local',
                sensor.unique_id, config_entry=entry).entity_id
            sensor.async_write_ha_state = Mock()
            hass.data[DATA_DOMAIN_PLATFORM_ENTITIES][('sensor', 'welcomeeye_local')] = {
                sensor.entity_id: sensor}
            user = SimpleNamespace(id='fixture', is_admin=False,
                permissions=SimpleNamespace(check_entity=Mock(return_value=False)))
            hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=user))

            async def press(target):
                await hass.services.async_call('button', 'press',
                    {'entity_id': by_target[target].entity_id}, blocking=True, context=Context(user_id='fixture'))

            for target in by_target:
                try:
                    await press(target)
                except HomeAssistantError:
                    pass
                else:
                    raise AssertionError('Unauthorized physical target accepted')
            assert read_count == 0 and not peers
            user.permissions.check_entity.return_value = True
            for target, expected in (('strike_1', (1, 1)), ('gate_1', (1, 2)),
                                     ('strike_2', (2, 1)), ('gate_2', (2, 2))):
                before = sum(len(peer.output_targets) for peer in peers)
                await press(target)
                assert sum(len(peer.output_targets) for peer in peers) == before + 1
                assert peers[-1].output_targets == [expected]
                assert peers[-1].closed and peers[-1].teardowns == 1
                assert not hub.consumers and not hub.channel2.consumers
            user.permissions.check_entity.assert_any_call(by_target['gate_2'].entity_id, POLICY_CONTROL)
            # A disabled route cannot be reached through an old button instance.
            hass.config_entries.async_update_entry(entry,
                data={**entry.data, 'channel2_gate_trial_enabled': False})
            before = read_count
            try:
                await by_target['gate_2'].async_press()
            except HomeAssistantError:
                pass
            else:
                raise AssertionError('Revoked target accepted')
            assert read_count == before

            async def observe(operation, context=None):
                response = await hass.services.async_call('welcomeeye_local', 'connect3_observe_doorbell',
                    {'entity_id': sensor.entity_id, 'operation': operation, 'duration': 30, 'channel': 2},
                    blocking=True, return_response=True,
                    context=context if context is not None else Context(user_id='fixture'))
                return response[sensor.entity_id]

            # Use the registered HA service and actual sensor. Permissions must
            # reject before the observer, even when no live session exists.
            with patch.object(hub.doorbell, 'execute', wraps=hub.doorbell.execute) as execute:
                for context, admin, allowed in ((Context(), True, True),
                        (Context(user_id='fixture'), False, True),
                        (Context(user_id='fixture'), True, False)):
                    user.is_admin, user.permissions.check_entity.return_value = admin, allowed
                    try:
                        await observe('start', context)
                    except HomeAssistantError:
                        pass
                    else:
                        raise AssertionError('Doorbell observation permission gate bypassed')
                execute.assert_not_called()
            assert hub.doorbell.runs == 0 and read_count == before
            user.is_admin, user.permissions.check_entity.return_value = True, True
            with patch.object(hub.channel2, 'acquire', side_effect=AssertionError('Observation acquired video')):
                try:
                    await observe('start')
                except HomeAssistantError:
                    pass
                else:
                    raise AssertionError('Observation started without existing channel-2 video')
            assert hub.doorbell.runs == 0 and read_count == before

            # Only the explicit viewer opens synthetic video. The observer
            # must use its existing reader and preserve its consumer lease.
            viewer = object()
            previous_peers = len(peers)
            await hub.channel2.acquire(viewer)
            assert read_count == before + 1 and len(peers) == previous_peers + 1
            peer = peers[-1]
            session = hub.channel2.live.session
            reader, media_task = session._reader, hub.channel2.live.task
            output_count = sum(len(item.output_targets) for item in peers)
            commands = list(peer.commands)
            private_tail = 'SYNTHETIC_PRIVATE_DOORBELL_IDENTIFIER'
            parameters = b'\x01' + private_tail.encode()
            header = bytearray(32)
            header[0], header[13] = 0xFE, 23
            size = (len(parameters) + 47) // 16 * 16
            struct.pack_into('<HH', header, 9, size, len(parameters))
            body = parameters + sha256(bytes(header) + parameters).digest()
            candidate = crypt(bytes(header)) + crypt(body + bytes(size - len(body)))
            with ExitStack() as passive:
                for selected in (hub, hub.channel2):
                    passive.enter_context(patch.object(selected, 'acquire',
                        side_effect=AssertionError('Observation acquired media')))
                    passive.enter_context(patch.object(selected, 'release',
                        side_effect=AssertionError('Observation released viewer media')))
                report = await observe('start')
                assert report['observation']['status'] == 'observing'
                assert report['observation']['channel'] == 2
                await observe('mark')
                peer.reader.feed_data(candidate)
                async with asyncio.timeout(2):
                    while not hub.doorbell.diagnostics()['observation']['other_doorbell_call_candidates']:
                        await asyncio.sleep(.01)
                report = await observe('status')
                event = next(item for item in report['observation']['events']
                             if item['candidate_type'] == 'other_doorbell_call')
                assert event['channel'] == 2 and event['order'] == 23
                assert event['parameter_bytes'] == len(parameters) and event['candidate_structure_valid']
                assert event['nearest_marker'] == 1 and type(event['marker_delta_ms']) is int
                assert not report['physical_ring_confirmed'] and report['ring_events_emitted'] == 0
                assert hub.channel2.live.session is session and session._reader is reader is peer.reader
                assert hub.channel2.live.task is media_task and not media_task.done()
                assert hub.channel2.consumers == {viewer} and not hub.consumers
                assert read_count == before + 1 and len(peers) == previous_peers + 1
                assert peer.commands == commands and not peer.closed
                assert sum(len(item.output_targets) for item in peers) == output_count
                assert private_tail not in json.dumps(report)
            user.permissions.check_entity.assert_any_call(sensor.entity_id, POLICY_CONTROL)
            await hub.channel2.release(viewer)
            report = await observe('status')
            assert report['observation']['status'] == 'finished'
            assert report['observation']['end_reason'] == 'media_closed'
            assert report['observation']['channel'] == 2
            assert peer.closed and peer.teardowns == 1 and peer.commands == commands + [7]
            assert not hub.channel2.consumers and hub.channel2.live.session is None
            assert read_count == before + 1 and len(peers) == previous_peers + 1
            assert sum(len(item.output_targets) for item in peers) == output_count
            diagnostic = await modules['diagnostics'].async_get_config_entry_diagnostics(hass, entry)
            for target, pair in modules['connect3.control'].OUTPUT_TARGETS.items():
                value = diagnostic['connect3']['control']['targets'][target]
                assert (value['channel'], value['output']) == pair
                assert value['request_send_attempt_count'] == 1
                assert value['native_ack_accepted'] is True
                assert value['physical_activation_verified'] is False
            raw = json.dumps(diagnostic)
            for secret in (PASSWORD, OPENING_CODE, STREAM_KEY, '192.0.2.1', 'a' * 64, private_tail):
                assert secret not in raw
            with patch.object(hass.config_entries, 'async_unload_platforms', AsyncMock(return_value=True)):
                assert await modules[''].async_unload_entry(hass, entry)
            component._platforms['button'].entities.clear()
            component._entities.clear()
            assert all(peer.closed for peer in peers)
        await hass.async_stop(force=True)
        print(json.dumps({'validation': 'actual_HA_four_outputs_synthetic',
            'homeassistant': importlib.metadata.version('homeassistant'), 'hardware_validated': False,
            'flow_target_consents': 'pass', 'button_permissions': 'pass', 'four_exact_wire_targets': 'pass',
            'one_write_per_click': 'pass', 'revocation': 'pass', 'cleanup_and_privacy': 'pass',
            'doorbell_channel2_service_permissions': 'pass', 'doorbell_existing_reader_only': 'pass',
            'doorbell_close_and_payload_privacy': 'pass'}))


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(90):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
