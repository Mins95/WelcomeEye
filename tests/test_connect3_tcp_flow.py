"""Explicit Connect 3 transport choices, with synthetic certificate outcomes."""
from copy import deepcopy
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


if __name__ == '__main__':
    unittest.main()
