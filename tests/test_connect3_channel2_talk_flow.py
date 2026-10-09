"""Explicit secondary microphone configuration; synthetic entries and no I/O."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from test_connect3_config import flow
from test_connect3_output_trial_flow import SAVED

OPTION = 'experimental_channel2_microphone'


class Channel2TalkFlowTests(unittest.IsolatedAsyncioTestCase):
    def configured(self, **extra):
        instance = flow()
        entry = SimpleNamespace(entry_id='retained', unique_id='retained-identity',
            options={'retained': True}, data={**SAVED, **extra})
        instance._get_reconfigure_entry = lambda: entry
        return instance, entry

    async def test_default_off_and_only_second_panel_exposes_advanced_option(self):
        instance, entry = self.configured()
        form = await instance.async_step_reconfigure()
        self.assertIn(OPTION, form['data_schema']['advanced'])
        self.assertNotIn(OPTION, entry.data)
        await instance.async_step_reconfigure({'host': SAVED['host']})
        self.assertNotIn(OPTION, entry.data)
        instance, entry = self.configured(second_channel_enabled=False)
        form = await instance.async_step_reconfigure()
        self.assertNotIn(OPTION, form['data_schema']['advanced'])
        await instance.async_step_reconfigure({'host': SAVED['host'], OPTION: True})
        self.assertFalse(entry.data[OPTION])

    async def test_explicit_opt_in_persists_without_opening_code_or_output_permission(self):
        instance, entry = self.configured(experimental_outputs=False, opening_code='')
        original = deepcopy(entry.data)
        result = await instance.async_step_reconfigure({'host': SAVED['host'], 'advanced': {OPTION: True}})
        self.assertEqual(result['reason'], 'reconfigure_successful')
        self.assertTrue(entry.data[OPTION])
        self.assertFalse(entry.data['experimental_outputs'])
        self.assertEqual(entry.data['auth_code'], original['auth_code'])
        self.assertEqual(entry.data['certificate_sha256'], original['certificate_sha256'])
        self.assertEqual(entry.unique_id, 'retained-identity')
        self.assertEqual(entry.options, {'retained': True})
        instance.hass.async_add_executor_job.assert_not_called()
        await instance.async_step_reconfigure({'host': SAVED['host']})
        self.assertTrue(entry.data[OPTION])
        await instance.async_step_reconfigure({'host': SAVED['host'], OPTION: False})
        self.assertFalse(entry.data[OPTION])

    async def test_disable_video_channel_or_clear_credentials_revokes_microphone_choice(self):
        for changes in ({'experimental_video': False}, {'second_channel_enabled': False}, {'clear_credentials': True}):
            instance, entry = self.configured(**{OPTION: True})
            result = await instance.async_step_reconfigure({'host': SAVED['host'], **changes})
            self.assertEqual(result['reason'], 'reconfigure_successful')
            self.assertFalse(entry.data[OPTION])
            if 'second_channel_enabled' in changes:
                instance.test_module.inspect_trust.assert_not_called()

    async def test_tcp_controls_disabled_cannot_enable_secondary_microphone(self):
        instance, entry = self.configured(media_transport='connect3_tcp',
            media_tcp_approved=True, experimental_tcp_controls=False,
            trust_endpoint={'host': SAVED['host'], 'cgi_port': 443,
                'media_port': 34567, 'media_transport': 'connect3_tcp'})
        await instance.async_step_reconfigure({'host': SAVED['host'], OPTION: True})
        self.assertFalse(entry.data[OPTION])

    async def test_non_boolean_input_rejected_before_trust_check(self):
        for value in (1, None, 'true'):
            instance, entry = self.configured()
            previous = deepcopy(entry.data)
            result = await instance.async_step_reconfigure({'host': SAVED['host'], OPTION: value})
            self.assertEqual(result['errors']['base'], 'invalid_connect3_config')
            self.assertEqual(entry.data, previous)
            instance.test_module.inspect_trust.assert_not_called()


if __name__ == '__main__':
    unittest.main()
