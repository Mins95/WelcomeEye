"""Production flow methods with synthetic inspection results, never device I/O.

The real HA schemas, selectors, flow manager and Repairs APIs are tested by
tools/verify_connect3_tls_runtime.py. These tests isolate the state transitions.
"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from test_connect3_config import flow, trust_module


def result(*, cgi='a' * 64, media='b' * 64, status='candidate', media_status=None, reason=None):
    return trust_module.TrustInspection(
        trust_module.EndpointTrust(status, cgi, reason,
            validity_status='valid', not_valid_after='2099-01-01T00:00:00+00:00'),
        trust_module.EndpointTrust(media_status or status, media, reason,
            validity_status='valid', not_valid_after='2099-01-02T00:00:00+00:00'))


INPUT = {'host': '192.0.2.1', 'auth_code': 'LOCAL_SYNTHETIC_PASSWORD',
         'experimental_video': True, 'experimental_outputs': True,
         'opening_code': 'SYNTHETIC_OPENING_CODE'}


class TLSFlowTests(unittest.IsolatedAsyncioTestCase):
    def setup_flow(self, *responses, entry=None):
        instance = flow()
        instance.test_module.inspect_trust = AsyncMock(side_effect=responses)
        if entry is not None:
            instance._get_reconfigure_entry = lambda: entry
        return instance

    def entry(self):
        return SimpleNamespace(entry_id='synthetic-entry', unique_id='connect3-retained',
            data={'host': '192.0.2.1', 'protocol_family': 'connect3_qv_experimental',
                'auth_code': 'EXISTING_LOCAL_SECRET', 'opening_code': 'EXISTING_OPENING_SECRET',
                'cgi_port': 443, 'media_port': 8443, 'certificate_sha256': 'a' * 64,
                'media_certificate_sha256': 'b' * 64,
                'experimental_video': True, 'experimental_outputs': True,
                'trust_endpoint': {'host': '192.0.2.1', 'cgi_port': 443, 'media_port': 8443}})

    async def test_ca_valid_automatic_pins_private_endpoint_and_no_credential_request(self):
        instance = self.setup_flow(result(status='system_ca'))
        created = await instance.async_step_connect3(INPUT)
        self.assertEqual(created['type'], 'create_entry')
        data = created['data']
        self.assertEqual(data['certificate_sha256'], 'a' * 64)
        self.assertEqual(data['media_certificate_sha256'], 'b' * 64)
        self.assertEqual(data['auth_code'], INPUT['auth_code'])
        self.assertEqual(data['trust_endpoint'], {'host': '192.0.2.1', 'cgi_port': 443, 'media_port': 8443})
        self.assertEqual(set(data['tls_certificate_expires']), {'cgi', 'media'})
        call = instance.test_module.inspect_trust.await_args
        self.assertEqual(call.args, ('192.0.2.1', 443, 8443))
        self.assertEqual(call.kwargs, {'cgi_pin': '', 'media_pin': ''})
        self.assertNotIn('SECRET', repr(call))
        instance.hass.async_add_executor_job.assert_not_called()

    async def test_same_or_different_selfsigned_certificates_one_confirmation(self):
        for media in ('a' * 64, 'b' * 64):
            instance = self.setup_flow(result(media=media), result(media=media, status='pinned'))
            pending = await instance.async_step_connect3(INPUT)
            self.assertEqual(pending['step_id'], 'connect3_tls_confirm')
            self.assertNotIn('PASSWORD', repr(pending))
            self.assertNotIn('OPENING_CODE', repr(pending))
            self.assertFalse(hasattr(instance, 'uid'))
            created = await instance.async_step_connect3_tls_confirm({'trust': True})
            self.assertEqual(created['type'], 'create_entry')
            self.assertEqual(created['data']['media_certificate_sha256'], media)
            self.assertIsNone(instance._connect3_pending)
            self.assertEqual(instance.test_module.inspect_trust.await_count, 2)

    async def test_new_decline_discards_secrets_and_does_not_reinspect(self):
        instance = self.setup_flow(result())
        await instance.async_step_connect3(INPUT)
        refused = await instance.async_step_connect3_tls_confirm({'trust': False})
        self.assertEqual(refused['reason'], 'connect3_tls_declined')
        self.assertIsNone(instance._connect3_pending)
        self.assertIsNone(instance._connect3_inspection)
        self.assertFalse(hasattr(instance, 'uid'))
        self.assertEqual(instance.test_module.inspect_trust.await_count, 1)

    async def test_existing_manual_pins_and_identity_preserved_when_same(self):
        entry = self.entry()
        instance = self.setup_flow(result(status='pinned'), entry=entry)
        original = deepcopy(entry.data)
        done = await instance.async_step_reconfigure({'host': entry.data['host']})
        self.assertEqual(done['reason'], 'reconfigure_successful')
        self.assertEqual(entry.unique_id, 'connect3-retained')
        for key, value in original.items():
            self.assertEqual(entry.data[key], value, key)
        instance.test_module.inspect_trust.assert_awaited_once_with('192.0.2.1', 443, 8443,
            cgi_pin='a' * 64, media_pin='b' * 64)

    async def test_changed_certificate_requires_approval_and_decline_keeps_existing(self):
        entry = self.entry()
        original = deepcopy(entry.data)
        instance = self.setup_flow(result(cgi='c' * 64, status='pin_mismatch'), entry=entry)
        pending = await instance.async_step_reconfigure({'host': entry.data['host']})
        self.assertEqual(pending['step_id'], 'connect3_tls_changed')
        self.assertEqual(entry.data, original)
        refused = await instance.async_step_connect3_tls_changed({'trust': False})
        self.assertEqual(refused['reason'], 'connect3_tls_declined')
        self.assertEqual(entry.data, original)
        instance.test_module.async_clear_tls_issue.assert_not_called()

    async def test_changed_certificates_saved_only_after_rechecked_explicit_approval(self):
        entry = self.entry()
        instance = self.setup_flow(result(cgi='c' * 64, media='d' * 64, status='pin_mismatch'),
            result(cgi='c' * 64, media='d' * 64, status='pinned'), entry=entry)
        await instance.async_step_reconfigure({'host': entry.data['host']})
        await instance.async_step_connect3_tls_changed({'trust': True})
        self.assertEqual(entry.data['certificate_sha256'], 'c' * 64)
        self.assertEqual(entry.data['media_certificate_sha256'], 'd' * 64)
        self.assertEqual(entry.data['auth_code'], 'EXISTING_LOCAL_SECRET')
        instance.test_module.async_clear_tls_issue.assert_called_once_with(instance.hass, entry.entry_id)

    async def test_ip_or_port_changed_even_same_certificate_needs_approval(self):
        for update in ({'host': '192.0.2.2'}, {'host': '192.0.2.1', 'advanced': {'cgi_port': 444}},
                       {'host': '192.0.2.1', 'advanced': {'media_port': 8444}}):
            entry = self.entry()
            instance = self.setup_flow(result(status='pinned'), result(status='pinned'), entry=entry)
            original = deepcopy(entry.data)
            pending = await instance.async_step_reconfigure(update)
            self.assertEqual(pending['step_id'], 'connect3_tls_changed')
            self.assertEqual(entry.data, original)
            await instance.async_step_connect3_tls_changed({'trust': True})
            self.assertTrue(trust_module.trust_endpoint_matches(entry.data))
            self.assertEqual(entry.unique_id, 'connect3-retained')

    async def test_new_certificate_while_dialog_open_cannot_be_approved_unseen(self):
        instance = self.setup_flow(result(), result(cgi='c' * 64, status='pin_mismatch'),
            result(cgi='c' * 64, status='pinned'))
        await instance.async_step_connect3(INPUT)
        changed = await instance.async_step_connect3_tls_confirm({'trust': True})
        self.assertEqual(changed['step_id'], 'connect3_tls_changed')
        self.assertEqual(changed['errors']['base'], 'connect3_certificate_changed')
        self.assertFalse(hasattr(instance, 'uid'))
        created = await instance.async_step_connect3_tls_changed({'trust': True})
        self.assertEqual(created['data']['certificate_sha256'], 'c' * 64)

    async def test_previously_edited_endpoint_cannot_rebind_without_confirmation(self):
        entry = self.entry()
        entry.data['host'] = '192.0.2.99'
        instance = self.setup_flow(result(status='pinned'), result(status='pinned'), entry=entry)
        pending = await instance.async_step_reconfigure({'host': '192.0.2.99'})
        self.assertEqual(pending['step_id'], 'connect3_tls_changed')
        self.assertEqual(entry.data['trust_endpoint']['host'], '192.0.2.1')
        await instance.async_step_connect3_tls_changed({'trust': True})
        self.assertEqual(entry.data['trust_endpoint']['host'], '192.0.2.99')

    async def test_failures_do_not_offer_trust_or_persist_partial_cgi_pin(self):
        for reason, expected in (
            ('certificate_expired', 'connect3_certificate_expired'),
            ('certificate_not_yet_valid', 'connect3_certificate_expired'),
            ('certificate_malformed', 'connect3_invalid_certificate'),
            ('certificate_timeout', 'connect3_device_unreachable'),
            ('certificate_connection_refused', 'connect3_device_unreachable'),
            ('certificate_network_unreachable', 'connect3_device_unreachable'),
            ('certificate_tls_error', 'connect3_tls_failed')):
            instance = self.setup_flow(result(status='system_ca', media_status='failed', reason=reason))
            failed = await instance.async_step_connect3(INPUT)
            self.assertEqual(failed['step_id'], 'connect3')
            self.assertEqual(failed['errors']['base'], expected)
            self.assertFalse(hasattr(instance, 'uid'))
            self.assertIsNone(getattr(instance, '_connect3_pending', None))

    async def test_clear_credentials_explicit_no_network_no_leftover_binding(self):
        entry = self.entry()
        instance = self.setup_flow(entry=entry)
        done = await instance.async_step_reconfigure({'host': entry.data['host'],
                                                     'advanced': {'clear_credentials': True}})
        self.assertEqual(done['reason'], 'reconfigure_successful')
        self.assertEqual(entry.data['auth_code'], '')
        self.assertEqual(entry.data['certificate_sha256'], '')
        self.assertEqual(entry.data['media_certificate_sha256'], '')
        self.assertIsNone(entry.data['trust_endpoint'])
        instance.test_module.inspect_trust.assert_not_called()

    async def test_no_overwrite_when_another_reconfiguration_finished_during_confirmation(self):
        entry = self.entry()
        instance = self.setup_flow(result(status='pin_mismatch'), result(status='pinned'), entry=entry)
        await instance.async_step_reconfigure({'host': entry.data['host']})
        entry.data['auth_code'] = 'ANOTHER_RECONFIGURATION'
        done = await instance.async_step_connect3_tls_changed({'trust': True})
        self.assertEqual(done['reason'], 'connect3_config_changed')
        self.assertEqual(entry.data['auth_code'], 'ANOTHER_RECONFIGURATION')
        instance.test_module.async_clear_tls_issue.assert_not_called()

    async def test_cancel_during_inspection_has_no_saved_entry_or_background_task(self):
        instance = self.setup_flow()
        entered = asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Future()
        instance.test_module.inspect_trust.side_effect = blocked
        task = asyncio.create_task(instance.async_step_connect3(INPUT))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(hasattr(instance, 'uid'))
        self.assertIsNone(getattr(instance, '_connect3_pending', None))
        self.assertEqual({t for t in asyncio.all_tasks() if not t.done()}, {asyncio.current_task()})

    async def test_main_form_no_manual_pins_and_no_secret_defaults(self):
        entry = self.entry()
        instance = self.setup_flow(entry=entry)
        form = await instance.async_step_reconfigure()
        self.assertNotIn('EXISTING_', repr(form))
        self.assertNotIn('certificate_sha256', {key for key in form['data_schema'] if key != 'advanced'})
        self.assertIn('certificate_sha256', form['data_schema']['advanced'])
        instance.test_module.inspect_trust.assert_not_called()


if __name__ == '__main__':
    unittest.main()
