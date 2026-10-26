"""Verify local authentication before saving manual QV configuration."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import aiohttp

from test_connect3_config import flow
from test_connect3_tls_flow import result as inspected_result
from test_r002_manual_ip import manual_data
from load_integration import load

CGIError = load('connect3.cgi').CGIError
PASSWORD = 'SYNTHETIC_LOCAL_PASSWORD'
INPUT = {'host': '192.0.2.10', 'auth_code': PASSWORD, 'media_transport': 'tls'}


class ManualAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_families_reject_device_401_before_creating_entry(self):
        for step in ('async_step_connect3', 'async_step_r002'):
            instance = flow()
            reader = instance.test_module.read_device
            reader.side_effect = CGIError('auth_code_rejected', device_error_code=401)
            rejected = await getattr(instance, step)(INPUT)
            self.assertEqual(rejected['type'], 'form')
            self.assertEqual(rejected['errors']['base'], 'qv_local_auth_rejected')
            self.assertFalse(hasattr(instance, 'uid'))
            self.assertNotIn(PASSWORD, repr(rejected))
            self.assertNotIn('a' * 64, repr(rejected))
            reader.assert_awaited_once_with('192.0.2.10', PASSWORD, port=443,
                                           certificate_sha256='a' * 64, operation='access')
            reader.side_effect = None
            created = await getattr(instance, step)({**INPUT, 'auth_code': 'CORRECTED_LOCAL_PASSWORD'})
            self.assertEqual(created['type'], 'create_entry')
            self.assertEqual(created['data']['auth_code'], 'CORRECTED_LOCAL_PASSWORD')
            self.assertEqual(reader.await_count, 2)  # One check per explicit submit.

    async def test_no_credentials_before_tls_and_transport_approval(self):
        for accept in (True, False):
            instance = flow()
            instance.test_module.inspect_trust = AsyncMock(side_effect=[
                inspected_result(status='candidate', media_status='not_applicable'),
                inspected_result(status='pinned', media_status='not_applicable')])
            pending = await instance.async_step_r002({**INPUT, 'media_transport': 'connect3_tcp'})
            self.assertEqual(pending['type'], 'form')
            instance.test_module.read_device.assert_not_awaited()
            result = await instance.async_step_connect3_tcp_tls_confirm({'trust': accept})
            if accept:
                self.assertEqual(result['type'], 'create_entry')
                instance.test_module.read_device.assert_awaited_once()
            else:
                self.assertEqual(result['type'], 'abort')
                instance.test_module.read_device.assert_not_awaited()

    async def test_failed_reconfiguration_keeps_password_pins_options_and_identity(self):
        instance = flow()
        entry = SimpleNamespace(entry_id='unchanged-entry', unique_id='unchanged-id', title='My camera',
            data=manual_data(credential_source='manual', language='es', qv_cloud_account={'fixture': True}))
        instance._get_reconfigure_entry = lambda: entry
        before = deepcopy(entry.data)
        instance.test_module.read_device.side_effect = CGIError('auth_code_rejected')
        rejected = await instance.async_step_reconfigure({'auth_code': 'REJECTED_NEW_PASSWORD'})
        self.assertEqual(rejected['step_id'], 'r002_connection')
        self.assertEqual(rejected['errors']['base'], 'qv_local_auth_rejected')
        self.assertEqual(entry.data, before)
        self.assertEqual((entry.entry_id, entry.unique_id, entry.title),
                         ('unchanged-entry', 'unchanged-id', 'My camera'))
        self.assertNotIn('REJECTED_NEW_PASSWORD', repr(rejected))
        self.assertNotIn(before['auth_code'], repr(rejected))
        instance.test_module.async_clear_tls_issue.assert_not_called()
        # Blank input keeps and validates the original saved password, not the rejected one.
        instance.test_module.read_device.side_effect = None
        saved = await instance.async_step_reconfigure({'auth_code': ''})
        self.assertEqual(saved['reason'], 'reconfigure_successful')
        self.assertEqual(instance.test_module.read_device.call_args.args[1], before['auth_code'])
        self.assertEqual(entry.data['auth_code'], before['auth_code'])
        self.assertEqual(entry.data['qv_cloud_account'], before['qv_cloud_account'])

    async def test_transport_failures_and_bad_responses_never_save_or_retry(self):
        failures = (
            (TimeoutError(), 'qv_access_unreachable'),
            (ConnectionRefusedError(), 'qv_access_unreachable'),
            (aiohttp.ServerFingerprintMismatch(b'a', b'b', '192.0.2.10', 443), 'qv_access_certificate_changed'),
            (CGIError('http_unauthorized', http_status=401), 'qv_access_failed'),
            (CGIError('malformed_xml'), 'qv_access_failed'),
        )
        for failure, error in failures:
            instance = flow()
            instance.test_module.read_device.side_effect = failure
            result = await instance.async_step_r002(INPUT)
            self.assertEqual(result['errors']['base'], error)
            self.assertFalse(hasattr(instance, 'uid'))
            instance.test_module.read_device.assert_awaited_once()
            instance.test_module.probe_tcp_setup.assert_not_awaited()

    async def test_concurrent_reconfiguration_during_access_is_not_overwritten(self):
        instance = flow()
        entry = SimpleNamespace(data=manual_data(), entry_id='fixture')
        instance._get_reconfigure_entry = lambda: entry

        async def change(*args, **kwargs):
            entry.data['auth_code'] = 'CONCURRENT_UPDATE'

        instance.test_module.read_device.side_effect = change
        result = await instance.async_step_reconfigure({'auth_code': 'OLDER_PENDING_UPDATE'})
        self.assertEqual(result['reason'], 'connect3_config_changed')
        self.assertEqual(entry.data['auth_code'], 'CONCURRENT_UPDATE')

    async def test_cancel_during_validation_does_not_save(self):
        instance = flow()
        entered = asyncio.Event()

        async def hold(*args, **kwargs):
            entered.set()
            await asyncio.Future()

        instance.test_module.read_device.side_effect = hold
        task = asyncio.create_task(instance.async_step_r002(INPUT))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(hasattr(instance, 'uid'))
        self.assertIsNone(instance._connect3_pending)

    async def test_qr_identity_check_is_not_bypassed_by_manual_access_validation(self):
        instance = flow()
        entry = SimpleNamespace(entry_id='qr-fixture', data={
            **manual_data(), 'protocol_family': 'connect3_qv_experimental', 'device_variant': 'connect3',
            'credential_source': 'apk_space', 'credential_device_uid': 'SYNTHETIC_BOUND_UID'})
        instance._get_reconfigure_entry = lambda: entry
        await instance.async_step_reconfigure({})
        instance.test_module.read_device.assert_not_awaited()
        self.assertEqual(entry.data['credential_device_uid'], 'SYNTHETIC_BOUND_UID')

    async def test_output_and_presentation_changes_do_not_start_access_check(self):
        instance = flow()
        entry = SimpleNamespace(entry_id='fixture', data=manual_data())
        instance._get_reconfigure_entry = lambda: entry
        result = await instance.async_step_reconfigure({'language': 'es'})
        self.assertEqual(result['reason'], 'reconfigure_successful')
        instance.test_module.read_device.assert_not_awaited()
        instance.test_module.inspect_trust.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
