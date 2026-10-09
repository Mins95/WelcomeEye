"""Explicit physical-trial consent, synthetic entries, no output or device I/O."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from test_connect3_config import flow


STRIKE = 'channel2_strike_trial_enabled'
GATE = 'channel2_gate_trial_enabled'
SAVED = {'host': '192.0.2.1', 'protocol_family': 'connect3_qv_experimental',
    'auth_code': 'SYNTHETIC_PASSWORD', 'opening_code': 'SYNTHETIC_OPENING',
    'certificate_sha256': 'a' * 64, 'media_certificate_sha256': 'b' * 64,
    'media_transport': 'tls', 'cgi_port': 443, 'media_port': 8443,
    'trust_endpoint': {'host': '192.0.2.1', 'cgi_port': 443,
        'media_port': 8443, 'media_transport': 'tls'},
    'experimental_video': True, 'experimental_outputs': True, 'second_channel_enabled': True}


class OutputTrialFlowTests(unittest.IsolatedAsyncioTestCase):
    def configured(self, **extra):
        instance = flow()
        entry = SimpleNamespace(entry_id='existing', unique_id='retained',
            options={'existing_preference': True}, data={**SAVED, **extra})
        instance._get_reconfigure_entry = lambda: entry
        return instance, entry

    async def test_separate_targets_require_literal_confirmation_and_keep_identity(self):
        instance, entry = self.configured()
        original = deepcopy(entry.data)
        pending = await instance.async_step_reconfigure({'host': SAVED['host'], STRIKE: True})
        self.assertEqual(pending['step_id'], 'connect3_output_trials')
        self.assertEqual(set(pending['data_schema']), {STRIKE})
        self.assertEqual(entry.data, original)
        accepted = await instance.async_step_connect3_output_trials({STRIKE: True})
        self.assertEqual(accepted['reason'], 'reconfigure_successful')
        self.assertTrue(entry.data[STRIKE])
        self.assertNotIn(GATE, entry.data)
        self.assertEqual(entry.unique_id, 'retained')
        self.assertEqual(entry.options, {'existing_preference': True})
        self.assertEqual(entry.data['auth_code'], SAVED['auth_code'])
        instance.hass.async_add_executor_job.assert_not_called()
        # Activating the other relay requires its own explicit confirmation.
        pending = await instance.async_step_reconfigure({'host': SAVED['host'], GATE: True})
        self.assertEqual(set(pending['data_schema']), {GATE})
        await instance.async_step_connect3_output_trials({GATE: True})
        self.assertTrue(entry.data[STRIKE] and entry.data[GATE])

    async def test_refusal_cancellation_and_missing_checkbox_never_persist(self):
        for response in ({}, {STRIKE: False}, {STRIKE: 1}, {STRIKE: 'true'}):
            instance, entry = self.configured()
            original = deepcopy(entry.data)
            await instance.async_step_reconfigure({'host': SAVED['host'], STRIKE: True})
            result = await instance.async_step_connect3_output_trials(response)
            self.assertEqual(result['reason'], 'connect3_output_trial_declined')
            self.assertEqual(entry.data, original)
            self.assertIsNone(instance._connect3_output_pending)
        instance, entry = self.configured()
        await instance.async_step_reconfigure({'host': SAVED['host'], STRIKE: True})
        instance._discard_connect3_pending()
        self.assertEqual((await instance.async_step_connect3_output_trials({STRIKE: True}))['type'], 'abort')
        self.assertNotIn(STRIKE, entry.data)

    async def test_changed_configuration_cannot_be_overwritten_by_stale_consent(self):
        instance, entry = self.configured()
        await instance.async_step_reconfigure({'host': SAVED['host'], STRIKE: True})
        entry.data['opening_code'] = 'ANOTHER_SAVED_CODE'
        result = await instance.async_step_connect3_output_trials({STRIKE: True})
        self.assertEqual(result['reason'], 'connect3_config_changed')
        self.assertEqual(entry.data['opening_code'], 'ANOTHER_SAVED_CODE')
        self.assertNotIn(STRIKE, entry.data)

    async def test_same_endpoint_retains_approval_but_changed_endpoint_requires_it_again(self):
        instance, entry = self.configured(**{STRIKE: True})
        result = await instance.async_step_reconfigure({'host': SAVED['host']})
        self.assertEqual(result['reason'], 'reconfigure_successful')
        result = await instance.async_step_reconfigure({'host': '192.0.2.2'})
        self.assertEqual(result['step_id'], 'connect3_tls_changed')
        result = await instance.async_step_connect3_tls_changed({'trust': True})
        self.assertEqual(result['step_id'], 'connect3_output_trials')
        self.assertEqual(entry.data['host'], SAVED['host'])
        await instance.async_step_connect3_output_trials({STRIKE: True})
        self.assertEqual(entry.data['host'], '192.0.2.2')

    async def test_changed_code_requires_renewed_target_consent(self):
        instance, entry = self.configured(**{STRIKE: True, GATE: True})
        result = await instance.async_step_reconfigure({'host': SAVED['host'], 'opening_code': 'NEW_CODE'})
        self.assertEqual(result['step_id'], 'connect3_output_trials')
        self.assertEqual(set(result['data_schema']), {STRIKE, GATE})
        self.assertEqual(entry.data['opening_code'], SAVED['opening_code'])

    async def test_disable_second_panel_revokes_both_trials_without_network(self):
        instance, entry = self.configured(**{STRIKE: True, GATE: True})
        result = await instance.async_step_reconfigure({'host': SAVED['host'], 'second_channel_enabled': False})
        self.assertEqual(result['reason'], 'reconfigure_successful')
        self.assertFalse(entry.data[STRIKE] or entry.data[GATE])
        instance.test_module.inspect_trust.assert_not_called()

    async def test_disabling_video_outputs_or_clearing_credentials_revokes_trials(self):
        for changes in ({'experimental_video': False}, {'experimental_outputs': False}, {'clear_credentials': True}):
            instance, entry = self.configured(**{STRIKE: True, GATE: True})
            result = await instance.async_step_reconfigure({'host': SAVED['host'], **changes})
            self.assertEqual(result['reason'], 'reconfigure_successful')
            self.assertFalse(entry.data[STRIKE] or entry.data[GATE])

    async def test_mono_channel_hides_trial_fields_and_cannot_activate(self):
        instance, entry = self.configured(second_channel_enabled=False)
        form = await instance.async_step_reconfigure()
        self.assertNotIn(STRIKE, form['data_schema']['advanced'])
        self.assertNotIn(GATE, form['data_schema']['advanced'])
        await instance.async_step_reconfigure({'host': SAVED['host'], STRIKE: True})
        self.assertFalse(entry.data[STRIKE])

    async def test_trial_input_types_and_secret_free_confirmation(self):
        instance, entry = self.configured()
        for value in (1, 'true', None):
            result = await instance.async_step_reconfigure({'host': SAVED['host'], STRIKE: value})
            self.assertEqual(result['errors']['base'], 'invalid_connect3_config')
        result = await instance.async_step_reconfigure({'host': SAVED['host'], STRIKE: True, GATE: True})
        for secret in (SAVED['auth_code'], SAVED['opening_code'], SAVED['certificate_sha256'], SAVED['host']):
            self.assertNotIn(secret, repr(result))
