"""Explicit Connect 3 transport choices, with synthetic certificate outcomes."""
from copy import deepcopy
import asyncio
import unittest

from test_connect3_config import flow, trust_module
import test_connect3_tls_flow as tls_flow
from test_connect3_tls_flow import result
from unittest.mock import AsyncMock


def cgi_only(status='pinned', pin='a' * 64):
    return trust_module.TrustInspection(result(status=status, cgi=pin).cgi,
                                       trust_module.EndpointTrust('not_applicable'))


class TCPFlowTests(unittest.IsolatedAsyncioTestCase):
    def setup_flow(self, *responses, entry=None):
        instance = flow()
        instance.test_module.inspect_trust = AsyncMock(side_effect=responses)
        if entry:
            instance._get_reconfigure_entry = lambda: entry
        return instance

    async def test_new_tcp_always_requires_explicit_warning_even_ca_trusted(self):
        instance = self.setup_flow(cgi_only('system_ca'), cgi_only())
        pending = await instance.async_step_connect3({'host': '192.0.2.1',
            'auth_code': 'SYNTHETIC_LOCAL_SECRET', 'experimental_video': True,
            'media_transport': 'connect3_tcp'})
        self.assertEqual(pending['step_id'], 'connect3_tcp_confirm')
        self.assertFalse(hasattr(instance, 'uid'))
        self.assertNotIn('SYNTHETIC_LOCAL_SECRET', repr(pending))
        self.assertNotIn('media_fingerprint', pending['data_schema']['certificate_details'])
        created = await instance.async_step_connect3_tcp_confirm({'trust': True})
        data = created['data']
        self.assertTrue(data['media_tcp_approved'])
        self.assertEqual(data['media_transport'], 'connect3_tcp')
        self.assertEqual(data['trust_endpoint']['media_port'], 34567)
        self.assertEqual(data['media_port'], 8443)
        self.assertEqual(data['tls_certificate_expires'].keys(), {'cgi'})
        self.assertTrue(trust_module.trust_endpoint_matches(data))
        self.assertEqual(instance.test_module.inspect_trust.await_count, 2)
        for call in instance.test_module.inspect_trust.await_args_list:
            self.assertIs(call.kwargs['media_tls'], False)

    async def test_unknown_cgi_combines_tofu_and_tcp_consent(self):
        instance = self.setup_flow(cgi_only('candidate'), cgi_only())
        pending = await instance.async_step_connect3({'host': '192.0.2.1',
            'auth_code': 'SYNTHETIC', 'media_transport': 'connect3_tcp'})
        self.assertEqual(pending['step_id'], 'connect3_tcp_tls_confirm')
        created = await instance.async_step_connect3_tcp_tls_confirm({'trust': True})
        self.assertTrue(created['data']['media_tcp_approved'])

    async def test_switch_declined_preserves_all_existing_parameters(self):
        entry = tls_flow.TLSFlowTests().entry()
        original = deepcopy(entry.data)
        instance = self.setup_flow(cgi_only(), entry=entry)
        pending = await instance.async_step_reconfigure({'host': entry.data['host'],
                                                        'media_transport': 'connect3_tcp'})
        self.assertEqual(pending['step_id'], 'connect3_tcp_tls_confirm')
        declined = await instance.async_step_connect3_tcp_tls_confirm({'trust': False})
        self.assertEqual(declined['reason'], 'connect3_tls_declined')
        self.assertEqual(entry.data, original)
        self.assertEqual(entry.unique_id, 'connect3-retained')
        self.assertEqual(instance.test_module.inspect_trust.await_count, 1)

    async def test_tls_tcp_tls_keeps_pins_port_credentials_and_identity(self):
        entry = tls_flow.TLSFlowTests().entry()
        entry.data['media_port'] = 8444
        entry.data['trust_endpoint']['media_port'] = 8444
        instance = self.setup_flow(cgi_only(), cgi_only(), cgi_only(),
                                  result(status='pinned'), result(status='pinned'), entry=entry)
        await instance.async_step_reconfigure({'host': entry.data['host'], 'media_transport': 'connect3_tcp'})
        await instance.async_step_connect3_tcp_tls_confirm({'trust': True})
        self.assertEqual(entry.data['media_certificate_sha256'], 'b' * 64)
        self.assertEqual(entry.data['media_port'], 8444)
        self.assertEqual(entry.data['auth_code'], 'EXISTING_LOCAL_SECRET')
        self.assertEqual(entry.data['opening_code'], 'EXISTING_OPENING_SECRET')
        self.assertTrue(entry.data['experimental_outputs'])
        # Ordinary reload/reconfigure never switches transport or repeats consent.
        unchanged = await instance.async_step_reconfigure({'host': entry.data['host']})
        self.assertEqual(unchanged['reason'], 'reconfigure_successful')
        self.assertEqual(entry.data['media_transport'], 'connect3_tcp')
        back = await instance.async_step_reconfigure({'host': entry.data['host'], 'media_transport': 'tls'})
        self.assertEqual(back['step_id'], 'connect3_tls_changed')
        await instance.async_step_connect3_tls_changed({'trust': True})
        self.assertFalse(entry.data['media_tcp_approved'])
        self.assertEqual(entry.data['media_port'], 8444)
        self.assertEqual(entry.unique_id, 'connect3-retained')
        self.assertTrue(trust_module.trust_endpoint_matches(entry.data))

    async def test_changed_cgi_during_tcp_confirmation_not_silently_replaced(self):
        instance = self.setup_flow(cgi_only('candidate'), cgi_only('pin_mismatch', 'c' * 64), cgi_only('pinned', 'c' * 64))
        await instance.async_step_connect3({'host': '192.0.2.1', 'auth_code': 'SYNTHETIC',
                                          'media_transport': 'connect3_tcp'})
        changed = await instance.async_step_connect3_tcp_tls_confirm({'trust': True})
        self.assertEqual(changed['errors']['base'], 'connect3_certificate_changed')
        self.assertFalse(hasattr(instance, 'uid'))
        done = await instance.async_step_connect3_tcp_tls_confirm({'trust': True})
        self.assertEqual(done['data']['certificate_sha256'], 'c' * 64)

    async def test_tcp_cgi_failure_shows_details_without_consent_or_saved_parameters(self):
        instance = self.setup_flow(tls_flow.verification_failure(tcp=True), cgi_only('system_ca'), cgi_only())
        inputs = {'host': '192.0.2.1', 'auth_code': 'SYNTHETIC_LOCAL_SECRET',
                  'media_transport': 'connect3_tcp', 'experimental_video': True}
        failed = await instance.async_step_connect3(inputs)
        self.assertEqual(failed['step_id'], 'connect3')
        self.assertEqual(failed['errors']['base'], 'connect3_certificate_weak_key')
        self.assertEqual(set(failed['data_schema']['verification_details']), tls_flow.VERIFICATION_FIELDS)
        self.assertNotIn('trust', failed['data_schema'])
        self.assertFalse(hasattr(instance, 'uid'))
        self.assertIsNone(getattr(instance, '_connect3_pending', None))
        instance.test_module.async_clear_tls_issue.assert_not_called()
        pending = await instance.async_step_connect3(inputs)
        self.assertEqual(pending['step_id'], 'connect3_tcp_confirm')
        self.assertNotIn('verification_details', pending['data_schema'])
        created = await instance.async_step_connect3_tcp_confirm({'trust': True})
        self.assertEqual(created['type'], 'create_entry')
        self.assertTrue(created['data']['media_tcp_approved'])
        self.assertNotIn('verification_details', created['data'])
        self.assertFalse(tls_flow.VERIFICATION_FIELDS & created['data'].keys())
        for call in instance.test_module.inspect_trust.await_args_list:
            self.assertIs(call.kwargs['media_tls'], False)

    async def test_tcp_confirmation_cancellation_creates_no_entry_or_worker(self):
        instance = self.setup_flow(cgi_only('candidate'))
        await instance.async_step_connect3({'host': '192.0.2.1', 'auth_code': 'SYNTHETIC_PRIVATE',
                                          'media_transport': 'connect3_tcp'})
        entered = asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Future()
        instance.test_module.inspect_trust.side_effect = blocked
        task = asyncio.create_task(instance.async_step_connect3_tcp_tls_confirm({'trust': True}))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(instance._connect3_pending)
        self.assertIsNone(instance._connect3_inspection)
        self.assertFalse(hasattr(instance, 'uid'))
        self.assertEqual({task for task in asyncio.all_tasks() if not task.done()}, {asyncio.current_task()})

    async def test_invalid_mode_no_network_and_legacy_tls_default(self):
        instance = self.setup_flow(result(status='pinned'))
        invalid = await instance.async_step_connect3({'host': '192.0.2.1', 'auth_code': 'SYNTHETIC',
                                                     'media_transport': 'auto'})
        self.assertEqual(invalid['errors']['base'], 'invalid_connect3_config')
        instance.test_module.inspect_trust.assert_not_called()
        done = await instance.async_step_connect3({'host': '192.0.2.1', 'auth_code': 'SYNTHETIC'})
        self.assertEqual(done['data']['media_transport'], 'tls')
        self.assertFalse(done['data']['media_tcp_approved'])

    def test_transport_bound_approvals_do_not_allow_silent_tcp_switch(self):
        data = tls_flow.TLSFlowTests().entry().data
        self.assertTrue(trust_module.trust_endpoint_matches(data))
        self.assertFalse(trust_module.trust_endpoint_matches({**data, 'media_transport': 'connect3_tcp'}))
        self.assertFalse(trust_module.trust_endpoint_matches({**data, 'media_transport': 'auto'}))
        self.assertFalse(trust_module.trust_endpoint_matches({'host': '192.0.2.1', 'media_transport': 'connect3_tcp'}))

    async def test_tcp_controls_require_new_opt_in_preserve_identity_and_survive_reload(self):
        entry = tls_flow.TLSFlowTests().entry()
        instance = self.setup_flow(*(cgi_only() for _ in range(6)), entry=entry)
        await instance.async_step_reconfigure({'host': entry.data['host'], 'media_transport': 'connect3_tcp'})
        await instance.async_step_connect3_tcp_tls_confirm({'trust': True})
        self.assertFalse(entry.data['experimental_tcp_controls'])
        before = deepcopy(entry.data)
        pending = await instance.async_step_reconfigure({'host': entry.data['host'],
                                                        'experimental_tcp_controls': True})
        self.assertEqual(pending['step_id'], 'connect3_tcp_confirm')
        self.assertEqual(entry.data, before)
        await instance.async_step_connect3_tcp_confirm({'trust': True})
        self.assertTrue(entry.data['experimental_tcp_controls'])
        self.assertEqual(entry.unique_id, 'connect3-retained')
        self.assertEqual(entry.data['auth_code'], 'EXISTING_LOCAL_SECRET')
        result = await instance.async_step_reconfigure({'host': entry.data['host']})
        self.assertEqual(result['reason'], 'reconfigure_successful')
        self.assertTrue(entry.data['experimental_tcp_controls'])
        await instance.async_step_reconfigure({'host': entry.data['host'], 'clear_credentials': True})
        self.assertFalse(entry.data['experimental_tcp_controls'])

    async def test_tcp_controls_rejection_keeps_prior_entry_and_sends_no_credentials(self):
        entry = tls_flow.TLSFlowTests().entry()
        instance = self.setup_flow(cgi_only(), cgi_only(), cgi_only(), entry=entry)
        await instance.async_step_reconfigure({'host': entry.data['host'], 'media_transport': 'connect3_tcp'})
        await instance.async_step_connect3_tcp_tls_confirm({'trust': True})
        before = deepcopy(entry.data)
        await instance.async_step_reconfigure({'host': entry.data['host'], 'experimental_tcp_controls': True})
        declined = await instance.async_step_connect3_tcp_confirm({'trust': False})
        self.assertEqual(declined['reason'], 'connect3_tls_declined')
        self.assertEqual(entry.data, before)
        for call in instance.test_module.inspect_trust.await_args_list:
            self.assertNotIn('EXISTING_LOCAL_SECRET', repr(call))
            self.assertNotIn('EXISTING_OPENING_SECRET', repr(call))

    async def test_tcp_output_enablement_requires_distinct_opening_code(self):
        instance = self.setup_flow(cgi_only())
        result = await instance.async_step_connect3({'host': '192.0.2.1', 'auth_code': 'SYNTHETIC',
            'media_transport': 'connect3_tcp', 'experimental_video': True,
            'experimental_outputs': True, 'experimental_tcp_controls': True})
        self.assertEqual(result['errors']['base'], 'connect3_opening_code_required')
        instance.test_module.inspect_trust.assert_not_called()

    async def test_tcp_controls_reject_nonboolean_before_inspection(self):
        for value in (1, 'true', None):
            instance = self.setup_flow(cgi_only())
            result = await instance.async_step_connect3({'host': '192.0.2.1', 'auth_code': 'SYNTHETIC',
                'media_transport': 'connect3_tcp', 'experimental_tcp_controls': value})
            self.assertEqual(result['errors']['base'], 'invalid_connect3_config')
            instance.test_module.inspect_trust.assert_not_called()


if __name__ == '__main__':
    unittest.main()
