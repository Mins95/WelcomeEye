"""Simple Connect 3 defaults, explicit transport consent and retained entries."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from test_connect3_config import flow, inspected, trust_module
from test_connect3_tls_flow import verification_failure

INPUT = {'host': '192.0.2.1', 'auth_code': 'SYNTHETIC_LOCAL_PASSWORD'}


class SimpleFlowTests(unittest.IsolatedAsyncioTestCase):
    def refused_tls(self):
        return trust_module.TrustInspection(inspected(INPUT['host']).cgi,
            trust_module.EndpointTrust('failed', reason='certificate_connection_refused'))

    async def test_new_default_tls_video_and_sound_without_extra_controls_checkbox(self):
        instance = flow()
        form = await instance.async_step_connect3()
        self.assertNotIn('media_transport', form['data_schema'])
        self.assertNotIn('experimental_tcp_controls', form['data_schema'])
        self.assertIn('media_transport', form['data_schema']['advanced'])
        created = await instance.async_step_connect3(INPUT)
        self.assertTrue(created['data']['experimental_video'])
        self.assertFalse(created['data']['experimental_outputs'])
        self.assertFalse(created['data']['second_channel_enabled'])
        self.assertEqual(created['data']['media_transport'], 'tls')
        instance.test_module.probe_tcp_setup.assert_not_called()
        instance.test_module.discover_candidates.assert_not_called()

    async def test_auto_tcp_requires_detected_setup_and_explicit_rechecked_consent(self):
        instance = flow()
        instance.test_module.inspect_trust = AsyncMock(side_effect=[
            self.refused_tls(), inspected(INPUT['host'], media_tls=False)])
        instance.test_module.probe_tcp_setup = AsyncMock(return_value=True)
        pending = await instance.async_step_connect3(INPUT)
        self.assertEqual(pending['step_id'], 'connect3_tcp_confirm')
        self.assertTrue(instance._connect3_pending['experimental_tcp_controls'])
        self.assertEqual(instance.test_module.inspect_trust.await_args.args, ('192.0.2.1', 443, 8443))
        instance.test_module.probe_tcp_setup.assert_awaited_once_with('192.0.2.1')
        created = await instance.async_step_connect3_tcp_confirm({'trust': True})
        self.assertEqual(created['data']['media_transport'], 'connect3_tcp')
        self.assertTrue(created['data']['media_tcp_approved'])
        self.assertEqual(created['data']['trust_endpoint']['media_port'], 34567)
        self.assertEqual(instance.test_module.probe_tcp_setup.await_count, 2)
        self.assertFalse(instance.test_module.inspect_trust.await_args.kwargs['media_tls'])
        self.assertNotIn('observed_media_channels', created['data'])

    async def test_tcp_setup_recheck_failure_keeps_pending_without_saving(self):
        instance = flow()
        instance.test_module.inspect_trust = AsyncMock(side_effect=[
            self.refused_tls(), inspected(INPUT['host'], media_tls=False)])
        instance.test_module.probe_tcp_setup = AsyncMock(side_effect=[True, False])
        await instance.async_step_connect3(INPUT)
        failed = await instance.async_step_connect3_tcp_confirm({'trust': True})
        self.assertEqual(failed['errors']['base'], 'connect3_tcp_not_detected')
        self.assertNotIn('data', failed)
        self.assertEqual((await instance.async_step_connect3_tcp_confirm({'trust': False}))['type'], 'abort')
        self.assertIsNone(instance._connect3_pending)

    async def test_certificate_failures_timeouts_or_custom_tls_port_never_probe_tcp(self):
        failures = ['certificate_expired', 'certificate_weak_key', 'certificate_invalid_signature',
                    'certificate_malformed', 'certificate_timeout', 'certificate_connection_reset']
        for reason in failures:
            instance = flow()
            instance.test_module.inspect_trust = AsyncMock(return_value=verification_failure('media', reason=reason))
            result = await instance.async_step_connect3(INPUT)
            self.assertEqual(result['type'], 'form')
            instance.test_module.probe_tcp_setup.assert_not_called()
        instance = flow()
        instance.test_module.inspect_trust = AsyncMock(return_value=self.refused_tls())
        await instance.async_step_connect3({**INPUT, 'media_port': 9999})
        instance.test_module.probe_tcp_setup.assert_not_called()
        instance = flow()
        instance.test_module.inspect_trust = AsyncMock(return_value=verification_failure('cgi', reason='certificate_connection_refused'))
        await instance.async_step_connect3(INPUT)
        instance.test_module.probe_tcp_setup.assert_not_called()

    async def test_existing_transport_and_disabled_controls_not_implicitly_enabled(self):
        initial = {**INPUT, 'protocol_family': 'connect3_qv_experimental',
            'media_transport': 'connect3_tcp', 'media_tcp_approved': True,
            'experimental_video': False, 'experimental_outputs': False,
            'experimental_tcp_controls': False, 'certificate_sha256': 'a' * 64,
            'media_certificate_sha256': 'b' * 64, 'opening_code': 'SYNTHETIC_OPENING'}
        entry = SimpleNamespace(entry_id='existing', unique_id='existing-identity', data=deepcopy(initial))
        instance = flow()
        instance._get_reconfigure_entry = lambda: entry
        await instance.async_step_reconfigure({'host': INPUT['host']})
        for key, value in initial.items():
            self.assertEqual(entry.data[key], value, key)
        self.assertNotIn('second_channel_enabled', entry.data)
        instance.test_module.probe_tcp_setup.assert_not_called()
        instance.test_module.discover_candidates.assert_not_called()
        self.assertFalse(instance.test_module.inspect_trust.await_args.kwargs['media_tls'])

    async def test_existing_tls_refusal_does_not_auto_change_selected_profile(self):
        entry = SimpleNamespace(entry_id='existing', unique_id='existing-identity', data={
            **INPUT, 'protocol_family': 'connect3_qv_experimental', 'media_transport': 'tls'})
        original = deepcopy(entry.data)
        instance = flow()
        instance._get_reconfigure_entry = lambda: entry
        instance.test_module.inspect_trust = AsyncMock(return_value=self.refused_tls())
        await instance.async_step_reconfigure({'host': INPUT['host']})
        self.assertEqual(entry.data, original)
        instance.test_module.probe_tcp_setup.assert_not_called()

    async def test_second_panel_is_explicit_boolean_and_does_not_probe_any_channel(self):
        for value in (1, 'true', None):
            instance = flow()
            result = await instance.async_step_connect3({**INPUT, 'second_channel_enabled': value})
            self.assertEqual(result['errors']['base'], 'invalid_connect3_config')
            instance.test_module.inspect_trust.assert_not_called()
        instance = flow()
        created = await instance.async_step_connect3({**INPUT, 'second_channel_enabled': True})
        self.assertTrue(created['data']['second_channel_enabled'])
        instance.test_module.probe_tcp_setup.assert_not_called()

    async def test_second_panel_only_change_keeps_trust_and_repairs_without_network(self):
        for profile in ('tls', 'connect3_tcp'):
            initial = {**INPUT, 'protocol_family': 'connect3_qv_experimental',
                'media_transport': profile, 'media_tcp_approved': profile == 'connect3_tcp',
                'certificate_sha256': 'a' * 64, 'media_certificate_sha256': 'b' * 64,
                'trust_endpoint': {'host': INPUT['host'], 'cgi_port': 443,
                    'media_port': 8443 if profile == 'tls' else 34567, 'media_transport': profile},
                'tls_certificate_date_exceptions': {'cgi': {'fixture': 'retained'}},
                'tls_certificate_key_exceptions': {'cgi': {'fixture': 'retained'}},
                'experimental_video': True, 'experimental_tcp_controls': False}
            entry = SimpleNamespace(entry_id='existing', unique_id='unchanged', data=deepcopy(initial))
            instance = flow()
            instance._get_reconfigure_entry = lambda: entry
            for value in (True, False):
                result = await instance.async_step_reconfigure({
                    'host': INPUT['host'], 'auth_code': '', 'second_channel_enabled': value})
                self.assertEqual(result['reason'], 'reconfigure_successful')
                self.assertEqual(entry.data, {**initial, 'second_channel_enabled': value})
                self.assertEqual(entry.unique_id, 'unchanged')
            instance.test_module.inspect_trust.assert_not_called()
            instance.test_module.probe_tcp_setup.assert_not_called()
            instance.test_module.discover_candidates.assert_not_called()
            instance.test_module.async_clear_tls_issue.assert_not_called()

    async def test_security_changes_or_unchanged_form_still_inspect_tls(self):
        submissions = ({'host': '192.0.2.2'}, {'auth_code': 'CHANGED_PASSWORD'},
            {'opening_code': 'CHANGED_OPENING'}, {'certificate_sha256': 'c' * 64},
            {'media_port': 9443}, {'media_transport': 'auto'}, {'experimental_video': False},
            {'second_channel_enabled': False})
        for changed in submissions:
            instance = flow()
            entry = SimpleNamespace(entry_id='existing', data={**INPUT,
                'protocol_family': 'connect3_qv_experimental', 'experimental_video': True})
            instance._get_reconfigure_entry = lambda: entry
            await instance.async_step_reconfigure({'host': INPUT['host'],
                'second_channel_enabled': True, **changed})
            instance.test_module.inspect_trust.assert_awaited_once()

    async def test_optional_discovery_selection_is_ephemeral_and_manual_remains_available(self):
        instance = flow()
        instance.test_module.discover_candidates = AsyncMock(return_value=['192.0.2.1'])
        found = await instance.async_step_connect3_discover()
        self.assertEqual(found['step_id'], 'connect3_discover')
        selected = await instance.async_step_connect3_discover({'host': '192.0.2.1'})
        self.assertEqual(selected['step_id'], 'connect3')
        created = await instance.async_step_connect3(INPUT)
        self.assertEqual(created['data']['host'], '192.0.2.1')
        self.assertEqual(created['data'].get('credential_device_uid', ''), '')
        instance.test_module.discover_candidates.assert_awaited_once()
        instance = flow()
        empty = await instance.async_step_connect3_discover()
        self.assertEqual(empty['errors']['base'], 'connect3_discovery_empty')
        manual = await instance.async_step_connect3_discover({'host': 'manual'})
        self.assertEqual(manual['step_id'], 'connect3')
        instance.test_module.discover_candidates.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
