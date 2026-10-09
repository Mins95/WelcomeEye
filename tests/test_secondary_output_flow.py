"""Synthetic legacy/R002 entries: per-relay approval never sends a command."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from test_connect3_config import flow

STRIKE = 'channel2_strike_trial_enabled'
GATE = 'channel2_gate_trial_enabled'


class SecondaryOutputFlowTests(unittest.IsolatedAsyncioTestCase):
    def configured(self, variant='connect2_r001', **extra):
        instance = flow()
        data = {'host': '192.0.2.1', 'username': 'admin', 'password': 'SYNTHETIC_PASSWORD',
                'device_variant': variant, 'second_channel_enabled': True,
                'protocol_family': 'r002_experimental' if variant == 'connect2_r002' else 'legacy_owsp'}
        if variant == 'connect2_r002':
            data.update(auth_code='SYNTHETIC_AUTH', opening_code='SYNTHETIC_OPENING',
                        experimental_video=True, experimental_outputs=True, certificate_sha256='a'*64)
        entry = SimpleNamespace(entry_id='existing', unique_id='unchanged', data={**data, **extra}, options={})
        instance._get_reconfigure_entry = lambda: entry
        return instance, entry

    async def test_each_family_requires_separate_consent_and_preserves_entry(self):
        for variant in ('connect_v1', 'connect2_r001', 'connect2_r002'):
            with self.subTest(variant=variant):
                instance, entry = self.configured(variant)
                original = deepcopy(entry.data)
                result = await instance.async_step_reconfigure({'host': original['host'], STRIKE: True})
                self.assertEqual(result['step_id'], 'secondary_output_trials')
                self.assertEqual(set(result['data_schema']), {STRIKE})
                self.assertEqual(entry.data, original)
                await instance.async_step_secondary_output_trials({STRIKE: True})
                self.assertTrue(entry.data[STRIKE])
                self.assertNotIn(GATE, entry.data)
                self.assertEqual(entry.unique_id, 'unchanged')
                self.assertEqual(entry.data['password'], original['password'])
                result = await instance.async_step_reconfigure({'host': original['host'], GATE: True})
                self.assertEqual(set(result['data_schema']), {GATE})
                await instance.async_step_secondary_output_trials({GATE: True})
                self.assertTrue(entry.data[STRIKE] and entry.data[GATE])
                instance.hass.async_add_executor_job.assert_not_called()
                instance.test_module.inspect_trust.assert_not_called()
                instance.test_module.probe_tcp_setup.assert_not_called()

    async def test_refusal_and_non_boolean_inputs_preserve_existing_configuration(self):
        for variant in ('connect_v1', 'connect2_r001', 'connect2_r002'):
            for answer in ({}, {STRIKE: False}, {STRIKE: 1}, {STRIKE: 'true'}):
                instance, entry = self.configured(variant)
                original = deepcopy(entry.data)
                await instance.async_step_reconfigure({'host': original['host'], STRIKE: True})
                result = await instance.async_step_secondary_output_trials(answer)
                self.assertEqual(result['reason'], 'connect3_output_trial_declined')
                self.assertEqual(entry.data, original)
            result = await instance.async_step_reconfigure({'host': original['host'], STRIKE: 1})
            self.assertTrue(result['errors'])

    async def test_changed_entry_and_back_edit_cannot_reuse_stale_approval(self):
        for variant in ('connect_v1', 'connect2_r001', 'connect2_r002'):
            instance, entry = self.configured(variant)
            await instance.async_step_reconfigure({'host': entry.data['host'], STRIKE: True})
            entry.data['password'] = 'NEW_SYNTHETIC_PASSWORD'
            result = await instance.async_step_secondary_output_trials({STRIKE: True})
            self.assertEqual(result['reason'], 'connect3_config_changed')
            self.assertNotIn(STRIKE, entry.data)
            await instance.async_step_reconfigure({'host': entry.data['host'], STRIKE: True})
            await instance.async_step_reconfigure({'host': entry.data['host'], STRIKE: 'invalid'})
            self.assertEqual((await instance.async_step_secondary_output_trials({STRIKE: True}))['type'], 'abort')
            self.assertNotIn(STRIKE, entry.data)

    async def test_turning_off_second_panel_revokes_trials_and_does_not_run_network(self):
        for variant in ('connect_v1', 'connect2_r001', 'connect2_r002'):
            instance, entry = self.configured(variant, **{STRIKE: True, GATE: True})
            await instance.async_step_reconfigure({'host': entry.data['host'], 'second_channel_enabled': False})
            self.assertFalse(entry.data[STRIKE] or entry.data[GATE])
            instance.hass.async_add_executor_job.assert_not_called()
            await instance.async_step_reconfigure({'host': entry.data['host'], STRIKE: True})
            self.assertFalse(entry.data[STRIKE])

    async def test_r002_disable_outputs_or_clear_credentials_revokes_secondary_consent(self):
        for change in ({'experimental_video': False}, {'experimental_outputs': False}, {'clear_credentials': True}):
            instance, entry = self.configured('connect2_r002', **{STRIKE: True, GATE: True})
            await instance.async_step_reconfigure({'host': entry.data['host'], **change})
            self.assertFalse(entry.data[STRIKE] or entry.data[GATE])

    async def test_r002_target_change_requires_reapproval_without_replacing_identity(self):
        instance, entry = self.configured('connect2_r002', **{STRIKE: True, GATE: True})
        original = deepcopy(entry.data)
        result = await instance.async_step_reconfigure({'host': '192.0.2.2'})
        self.assertEqual(result['step_id'], 'secondary_output_trials')
        self.assertEqual(entry.data, original)
        self.assertEqual(set(result['data_schema']), {STRIKE, GATE})
        for secret in (original['host'], original['auth_code'], original['opening_code'], original['certificate_sha256']):
            self.assertNotIn(secret, repr(result))

    async def test_legacy_password_change_revokes_secondary_consent(self):
        instance, entry = self.configured(**{STRIKE: True, GATE: True})
        instance._get_reauth_entry = lambda: entry
        instance.hass.async_add_executor_job.side_effect = None
        instance.hass.async_add_executor_job.return_value = entry.unique_id
        instance.test_module.validate_connection = lambda *args: entry.unique_id
        await instance.async_step_reauth_confirm({'password': 'NEW_SYNTHETIC_PASSWORD'})
        self.assertFalse(entry.data[STRIKE] or entry.data[GATE])
