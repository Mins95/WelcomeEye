"""Actual HA common secondary-output flows and ButtonEntity service permissions.

Only synthetic entries are created. Real controllers supply target policy, but
their physical command boundary is mocked. No hub is started or device contacted.
"""
import asyncio
from contextlib import ExitStack
from copy import deepcopy
import importlib
import importlib.metadata
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

STRIKE = 'channel2_strike_trial_enabled'
GATE = 'channel2_gate_trial_enabled'


async def main(root):
    with tempfile.TemporaryDirectory() as temporary:
        manifest = json.loads((root / 'custom_components/welcomeeye_local/manifest.json').read_text())
        for requirement in manifest['requirements']:
            assert await asyncio.to_thread(install_package, requirement, **pip_kwargs(temporary))
        sys.path.insert(0, str(root))
        package = 'custom_components.welcomeeye_local'
        modules = {name: importlib.import_module(package + '.' + name) for name in (
            'config_flow', 'button', 'hub', 'r002.hub', 'client', 'r002.qv_discovery')}
        from homeassistant.components import button as ha_button
        from homeassistant.auth.permissions.const import POLICY_CONTROL
        hass = HomeAssistant(temporary)
        hass.config_entries = ConfigEntries(hass, {})
        if hasattr(dr, 'async_setup'):
            dr.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        registry = er.async_get(hass)
        assert await ha_button.async_setup(hass, {})
        component = hass.data[ha_button.DATA_COMPONENT]
        summary = []

        def flow_for(entry):
            flow = modules['config_flow'].WelcomeEyeConfigFlow()
            flow.hass, flow.context = hass, {'source': 'reconfigure', 'entry_id': entry.entry_id}
            return flow

        with ExitStack() as stack:
            stack.enter_context(patch.object(asyncio, 'open_connection', side_effect=AssertionError('device TCP forbidden')))
            stack.enter_context(patch.object(modules['client'].Session, 'connect', side_effect=AssertionError('device LT forbidden')))
            stack.enter_context(patch.object(modules['r002.qv_discovery'], '_open_listener', side_effect=AssertionError('device UDP forbidden')))
            stack.enter_context(patch.object(hass.config_entries, 'async_reload', AsyncMock()))
            for index, variant in enumerate(('connect_v1', 'connect2_r001', 'connect2_r002'), 1):
                data = {'host': f'192.0.2.{index}', 'username': 'SYNTHETIC_USER',
                    'password': 'SYNTHETIC_PASSWORD', 'device_variant': variant,
                    'protocol_family': 'r002_experimental' if variant == 'connect2_r002' else 'legacy_owsp',
                    'second_channel_enabled': True, 'v1_cloud_doorbell_enabled': False}
                if variant == 'connect2_r002':
                    data.update(auth_code='SYNTHETIC_AUTH', opening_code='SYNTHETIC_OPENING',
                        certificate_sha256='a'*64, experimental_video=True, experimental_outputs=True)
                entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
                    title='Synthetic '+variant, unique_id='synthetic-'+variant, data=data,
                    options={'ring_image_capture': False}, source='user', subentries_data=None,
                    discovery_keys=MappingProxyType({}))
                hass.config_entries._entries[entry.entry_id] = entry
                identity = entry.entry_id, entry.unique_id, entry.title, dict(entry.options)
                original = deepcopy(dict(entry.data))
                request = {'host': data['host'], STRIKE: True}

                # Reject both non-boolean enablement and every non-explicit
                # confirmation. None of these flows may update the ConfigEntry.
                for invalid in (1, 'true', None):
                    result = await flow_for(entry).async_step_reconfigure({**request, STRIKE: invalid})
                    assert result['type'] == 'form' and result['errors']
                    assert dict(entry.data) == original
                for answer in ({}, {STRIKE: False}, {STRIKE: 1}, {STRIKE: 'true'}):
                    flow = flow_for(entry)
                    result = await flow.async_step_reconfigure(request)
                    assert result['step_id'] == 'secondary_output_trials'
                    assert {str(key) for key in result['data_schema'].schema} == {STRIKE}
                    for secret in ('SYNTHETIC_PASSWORD', 'SYNTHETIC_AUTH', 'SYNTHETIC_OPENING', 'a'*64):
                        assert secret not in repr(result)
                    result = await flow.async_step_secondary_output_trials(answer)
                    assert result['reason'] == 'connect3_output_trial_declined'
                    assert dict(entry.data) == original

                # A back/edit invalid submission cannot reuse the old consent.
                flow = flow_for(entry)
                await flow.async_step_reconfigure(request)
                await flow.async_step_reconfigure({**request, STRIKE: 'invalid'})
                result = await flow.async_step_secondary_output_trials({STRIKE: True})
                assert result['reason'] == 'connect3_tls_no_pending'
                assert dict(entry.data) == original
                flow = flow_for(entry)
                await flow.async_step_reconfigure(request)
                hass.config_entries.async_update_entry(entry, data={**entry.data, 'unrelated_setting': True})
                result = await flow.async_step_secondary_output_trials({STRIKE: True})
                assert result['reason'] == 'connect3_config_changed' and not entry.data.get(STRIKE)
                hass.config_entries.async_update_entry(entry, data=original)

                # Each exact relay has its own real HA confirmation schema.
                for field in (STRIKE, GATE):
                    flow = flow_for(entry)
                    result = await flow.async_step_reconfigure({'host': data['host'], field: True})
                    assert result['step_id'] == 'secondary_output_trials'
                    assert {str(key) for key in result['data_schema'].schema} == {field}
                    before = deepcopy(dict(entry.data))
                    result = await flow.async_step_secondary_output_trials(result['data_schema']({field: True}))
                    assert result['type'] == 'abort' and result['reason'] == 'reconfigure_successful'
                    assert entry.data[field] is True
                    for key, value in before.items():
                        if key != field:
                            assert entry.data[key] == value
                assert identity == (entry.entry_id, entry.unique_id, entry.title, dict(entry.options))
                assert all(entry.data[key] == value for key, value in original.items())

                # Actual hub/controller policy and actual integration platform.
                # No start() call: setup itself must not acquire live media.
                cls = modules['r002.hub'].R002InvestigationHub if variant == 'connect2_r002' else modules['hub'].WelcomeEyeHub
                hub = cls(hass, entry)
                hub.stopped = False
                entry.runtime_data = hub
                assert hub.control.target_enabled('strike_2') and hub.control.target_enabled('gate_2')
                created = []
                primary_unlock = AsyncMock() if variant == 'connect2_r002' else Mock()
                with patch.object(hub.control, 'unlock_target', AsyncMock()) as unlock, \
                        patch.object(hub.control, 'unlock', primary_unlock):
                    await modules['button'].async_setup_entry(hass, entry, created.extend)
                    assert len(created) == 4
                    unlock.assert_not_called()
                    primary_unlock.assert_not_called()
                    by_target = {}
                    for button in created:
                        button.hass = hass
                        registered = registry.async_get_or_create('button', 'welcomeeye_local', button.unique_id, config_entry=entry)
                        if isinstance(button, modules['button'].WelcomeEyeSecondOutputButton) and button.target == 'strike_2':
                            registered = registry.async_update_entity(registered.entity_id, new_entity_id='button.owner_renamed_'+str(index))
                        button.entity_id = registered.entity_id
                        button.async_write_ha_state = Mock()
                        component._platforms['button'].entities[button.entity_id] = button
                        component._entities[button.entity_id] = button
                        by_target[button.extra_state_attributes['welcomeeye_output_target']] = button
                    hass.data[DATA_DOMAIN_PLATFORM_ENTITIES] = {('button', 'welcomeeye_local'):
                        {button.entity_id: button for button in created}}
                    user = SimpleNamespace(id='fixture', is_admin=False,
                        permissions=SimpleNamespace(check_entity=Mock(return_value=False)))
                    hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=user))

                    async def press(target):
                        await hass.services.async_call('button', 'press',
                            {'entity_id': by_target[target].entity_id}, blocking=True, context=Context(user_id='fixture'))

                    for target in ('strike_2', 'gate_2'):
                        try:
                            await press(target)
                        except HomeAssistantError:
                            pass
                        else:
                            raise AssertionError('Entity control permission bypassed')
                    unlock.assert_not_called()
                    # Ordinary users with entity control may operate a button;
                    # admin is not required by HA's normal button service.
                    user.permissions.check_entity.return_value = True
                    for target in ('strike_2', 'gate_2'):
                        unlock.reset_mock()
                        await press(target)
                        unlock.assert_awaited_once_with(target)
                        primary_unlock.assert_not_called()
                        user.permissions.check_entity.assert_any_call(by_target[target].entity_id, POLICY_CONTROL)
                        attrs = by_target[target].extra_state_attributes
                        assert attrs['welcomeeye_output_channel'] == 2
                        assert attrs['welcomeeye_output_number'] == (1 if target == 'strike_2' else 2)
                        assert attrs['validation_status'] == 'hardware_pending'
                    # Recreating platform entities retains user-renamed registry
                    # identifiers; it must never invoke the physical boundary.
                    unlock.reset_mock()
                    recreated = []
                    await modules['button'].async_setup_entry(hass, entry, recreated.extend)
                    for button in recreated:
                        target = button.extra_state_attributes['welcomeeye_output_target']
                        registered = registry.async_get_or_create('button', 'welcomeeye_local', button.unique_id, config_entry=entry)
                        assert registered.entity_id == by_target[target].entity_id
                    unlock.assert_not_called()
                    approved = deepcopy(dict(entry.data))
                    await flow_for(entry).async_step_reconfigure({'host': data['host'], 'second_channel_enabled': False})
                    assert entry.data[STRIKE] is False and entry.data[GATE] is False
                    assert not by_target['strike_2'].available and not by_target['gate_2'].available
                    unlock.assert_not_called()
                    hass.config_entries.async_update_entry(entry, data=approved)
                    if variant != 'connect2_r002':
                        flow = flow_for(entry)
                        flow.context = {'source': 'reauth', 'entry_id': entry.entry_id}
                        with patch.object(modules['config_flow'], 'validate_connection', return_value=entry.unique_id):
                            await flow.async_step_reauth_confirm({'password': 'NEW_SYNTHETIC_PASSWORD'})
                        assert entry.data[STRIKE] is False and entry.data[GATE] is False
                    unlock.assert_not_called()
                    primary_unlock.assert_not_called()
                    for button in created:
                        component._platforms['button'].entities.pop(button.entity_id, None)
                        component._entities.pop(button.entity_id, None)
                summary.append({'variant': variant, 'strict_separate_consent': 'pass',
                    'decline_and_stale_consent': 'pass', 'entry_secrets_identity_preserved': 'pass',
                    'actual_button_service_permissions': 'pass', 'exact_single_target': 'pass',
                    'renamed_entity_id_preserved': 'pass', 'setup_and_revocation_no_commands': 'pass'})
        await hass.async_stop(force=True)
        print(json.dumps({'validation': 'actual_HA_common_secondary_outputs_synthetic',
            'homeassistant': importlib.metadata.version('homeassistant'), 'hardware_validated': False,
            'device_calls': 0, 'families': summary}))


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(90):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
