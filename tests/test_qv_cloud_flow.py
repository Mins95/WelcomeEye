"""Account selection is transactional and never registers push in an open flow."""
from copy import deepcopy
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from test_connect3_config import flow


def fixture(family='connect3_qv_experimental'):
    instance = flow()
    entry = SimpleNamespace(entry_id='local-entry', unique_id='provisional-private-id', data={
        'host': '192.0.2.1', 'protocol_family': family, 'auth_code': 'LOCAL_SECRET',
        'certificate_sha256': 'a' * 64, 'opening_code': 'OPENING_SECRET'})
    instance._get_reconfigure_entry = lambda: entry
    instance._qv_authorized_devices = AsyncMock(return_value=(
        [SimpleNamespace(uid='CLOUD_UID', name='Front panel')], 0))
    return instance, entry


class CloudFlowTests(unittest.IsolatedAsyncioTestCase):
    async def enroll(self, instance):
        return await instance.async_step_qv_cloud_account({
            'email': 'synthetic@example.invalid', 'password': 'ACCOUNT_SECRET', 'confirm_cloud': True})

    async def test_two_families_explicit_account_and_target_confirmation(self):
        for family in ('connect3_qv_experimental', 'r002_experimental'):
            instance, entry = fixture(family)
            previous = deepcopy(entry.data)
            form = await instance.async_step_reconfigure({'host': '192.0.2.1', 'qv_cloud_action': 'configure'})
            self.assertEqual(form['step_id'], 'qv_cloud_account')
            self.assertEqual(entry.data, previous)
            self.assertNotIn('SECRET', repr(form))
            selection = await self.enroll(instance)
            self.assertEqual(selection['step_id'], 'qv_cloud_device')
            self.assertNotIn('SECRET', repr(selection))
            self.assertEqual(entry.data, previous)
            refused = await instance.async_step_qv_cloud_device({'device': '0', 'confirm_device': False})
            self.assertEqual(refused['errors']['base'], 'qv_cloud_device_confirmation')
            await instance.async_step_qv_cloud_device({'device': '0', 'confirm_device': True})
            self.assertTrue(entry.data['qv_cloud_enabled'])
            self.assertEqual(entry.data['qv_cloud_account']['device'], {'uid': 'CLOUD_UID'})
            self.assertEqual(entry.data['auth_code'], 'LOCAL_SECRET')
            self.assertEqual(entry.unique_id, 'provisional-private-id')

    async def test_keep_or_disable_never_authenticates_or_changes_local_credentials(self):
        for action in ('keep', 'disable'):
            instance, entry = fixture()
            await instance.async_step_reconfigure({'host': '192.0.2.1', 'qv_cloud_action': action})
            instance._qv_authorized_devices.assert_not_awaited()
            self.assertEqual(entry.data['auth_code'], 'LOCAL_SECRET')
            if action == 'disable':
                self.assertFalse(entry.data['qv_cloud_enabled'])
            else:
                self.assertNotIn('qv_cloud_enabled', entry.data)

    async def test_no_permission_or_empty_password_never_uses_local_code(self):
        instance, entry = fixture()
        await instance.async_step_reconfigure({'qv_cloud_action': 'configure'})
        for data in ({'email': 'synthetic@example.invalid', 'confirm_cloud': True},
                     {'email': 'synthetic@example.invalid', 'password': 'ACCOUNT_SECRET'}):
            result = await instance.async_step_qv_cloud_account(data)
            self.assertEqual(result['errors']['base'], 'qv_cloud_credentials_required')
            instance._qv_authorized_devices.assert_not_awaited()
        self.assertNotIn('qv_cloud_enabled', entry.data)

    async def test_changed_entry_aborts_without_overwriting(self):
        instance, entry = fixture()
        await instance.async_step_reconfigure({'qv_cloud_action': 'configure'})
        await self.enroll(instance)
        entry.data['host'] = '192.0.2.2'
        result = await instance.async_step_qv_cloud_device({'device': '0', 'confirm_device': True})
        self.assertEqual(result['reason'], 'connect3_config_changed')
        self.assertNotIn('qv_cloud_enabled', entry.data)

    async def test_empty_accounts_failures_and_arbitrary_choices_no_persistence(self):
        instance, entry = fixture()
        await instance.async_step_reconfigure({'qv_cloud_action': 'configure'})
        instance._qv_authorized_devices.return_value = ([], 0)
        self.assertEqual((await self.enroll(instance))['errors']['base'], 'qv_cloud_no_devices')
        instance._qv_authorized_devices.side_effect = RuntimeError('SECRET_URL')
        result = await self.enroll(instance)
        self.assertNotIn('SECRET', repr(result))
        self.assertEqual(result['errors']['base'], 'qv_cloud_connect_failed')
        self.assertNotIn('qv_cloud_enabled', entry.data)

    async def test_imported_uid_and_existing_cloud_target_cannot_silently_change(self):
        for saved in ({'credential_device_uid': 'OTHER'},
                      {'qv_cloud_account': {'device': {'uid': 'OTHER'}}}):
            instance, entry = fixture()
            entry.data.update(saved)
            await instance.async_step_reconfigure({'qv_cloud_action': 'configure'})
            await self.enroll(instance)
            result = await instance.async_step_qv_cloud_device({'device': '0', 'confirm_device': True})
            self.assertEqual(result['errors']['base'], 'qv_cloud_wrong_device')
            self.assertNotIn('qv_cloud_enabled', entry.data)

    async def test_duplicate_cloud_device_refused(self):
        instance, entry = fixture()
        await instance.async_step_reconfigure({'qv_cloud_action': 'configure'})
        await self.enroll(instance)
        instance._async_current_entries = lambda: [entry, SimpleNamespace(entry_id='other', data={
            'qv_cloud_enabled': True, 'qv_cloud_account': {'device': {'uid': 'CLOUD_UID'}}})]
        result = await instance.async_step_qv_cloud_device({'device': '0', 'confirm_device': True})
        self.assertEqual(result['errors']['base'], 'qv_cloud_already_bound')

    async def test_cancelled_login_never_persists_and_new_flow_starts_cleanly(self):
        instance, entry = fixture()
        previous = deepcopy(entry.data)
        started = asyncio.Event()

        async def wait_for_cancel(*args):
            started.set()
            await asyncio.Event().wait()

        instance._qv_authorized_devices.side_effect = wait_for_cancel
        await instance.async_step_reconfigure({'qv_cloud_action': 'configure'})
        task = asyncio.create_task(self.enroll(instance))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(entry.data, previous)
        self.assertIsNone(instance._qv_cloud_login)
        restarted, _ = fixture()
        restarted._get_reconfigure_entry = lambda: entry
        account_form = await restarted.async_step_reconfigure({'qv_cloud_action': 'configure'})
        self.assertEqual(account_form['step_id'], 'qv_cloud_account')
        self.assertNotIn('ACCOUNT_SECRET', repr(account_form))

    async def test_restarting_configuration_discards_previous_authorized_selection(self):
        instance, entry = fixture()
        await instance.async_step_reconfigure({'qv_cloud_action': 'configure'})
        await self.enroll(instance)
        await instance.async_step_reconfigure({'qv_cloud_action': 'configure'})
        self.assertIsNone(instance._qv_cloud_devices)
        self.assertIsNone(instance._qv_cloud_login)
        result = await instance.async_step_qv_cloud_device({'device': '0', 'confirm_device': True})
        self.assertEqual(result['reason'], 'connect3_config_changed')
        self.assertNotIn('qv_cloud_account', entry.data)
