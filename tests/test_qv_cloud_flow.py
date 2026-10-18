"""Account selection is transactional and never registers push in an open flow."""
from copy import deepcopy
import asyncio
import ast
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from test_connect3_config import flow
from load_integration import load, PACKAGE, ROOT, cap
from test_transport_lifecycle import load_source

enrollment = load('qv_cloud_enrollment')


def fixture(family='connect3_qv_experimental'):
    instance = flow()
    instance.hass.data = {}
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
            self.assertEqual(form['description_placeholders'], {})
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
        self.assertEqual(enrollment.snapshot(instance.hass, entry.entry_id)['status'], 'cancelled')
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

    async def test_real_pipeline_preserves_failed_phase_and_never_exposes_exception_data(self):
        api, discovery = load('qv_cloud_api'), load('qv_cloud_discovery')
        for phase, ui_error in (
                ('service_discovery', 'qv_cloud_discovery_failed'),
                ('account_login', 'qv_cloud_login_failed'),
                ('device_list', 'qv_cloud_device_list_failed')):
            with self.subTest(phase=phase):
                instance, entry = fixture()
                previous = deepcopy(entry.data)
                del instance._qv_authorized_devices
                instance._qv_authorized_devices.__func__.__globals__['__package__'] = PACKAGE
                services = SimpleNamespace(region_id=1)
                client = SimpleNamespace(services=services, async_login=AsyncMock(),
                    async_list_devices=AsyncMock(return_value=[]), async_close=AsyncMock())
                resolve = AsyncMock(return_value=services)
                failure = RuntimeError('https://private.invalid/?password=SECRET; UID; email@private.invalid')
                failure.code = 'invalid_response'
                {'service_discovery': resolve, 'account_login': client.async_login,
                 'device_list': client.async_list_devices}[phase].side_effect = failure
                with patch.object(api, 'QvCloudClient', return_value=client), \
                        patch.object(discovery, 'resolve_services', resolve):
                    await instance.async_step_reconfigure({'qv_cloud_action': 'configure'})
                    result = await self.enroll(instance)
                self.assertEqual(result['errors']['base'], ui_error)
                self.assertEqual(result['description_placeholders'], {
                    'cloud_stage': phase, 'cloud_error': 'invalid_response'})
                diagnostic = enrollment.snapshot(instance.hass, entry.entry_id)
                self.assertEqual(diagnostic, {'status': 'failed', 'stage': phase,
                                             'error_code': 'invalid_response'})
                self.assertNotIn('SECRET', repr(result) + repr(diagnostic))
                self.assertNotIn('private.invalid', repr(result) + repr(diagnostic))
                self.assertEqual(entry.data, previous)
                self.assertIsNone(instance._qv_cloud_login)
                if phase == 'service_discovery':
                    client.async_login.assert_not_awaited()
                    client.async_close.assert_not_awaited()
                else:
                    client.async_close.assert_awaited_once()

    async def test_auth_rejection_retry_and_success_reset_only_sanitized_diagnostics(self):
        instance, entry = fixture()
        previous = deepcopy(entry.data)
        await instance.async_step_reconfigure({'qv_cloud_action': 'configure'})

        async def rejected(*args):
            instance._record_qv_enrollment('running', 'account_login')
            raise load('qv_cloud_protocol').QvCloudError('authentication_failed')

        instance._qv_authorized_devices.side_effect = rejected
        result = await self.enroll(instance)
        self.assertEqual(result['errors']['base'], 'qv_cloud_invalid_auth')
        self.assertEqual(entry.data, previous)
        instance._qv_authorized_devices.side_effect = None
        await self.enroll(instance)
        self.assertEqual(enrollment.snapshot(instance.hass, entry.entry_id), {
            'status': 'awaiting_device', 'stage': 'device_selection', 'error_code': None})
        await instance.async_step_qv_cloud_device({'device': '0', 'confirm_device': True})
        self.assertEqual(enrollment.snapshot(instance.hass, entry.entry_id), {
            'status': 'configured', 'stage': 'complete', 'error_code': None})
        self.assertNotIn('ACCOUNT_SECRET', repr(instance.hass.data))
        self.assertEqual(enrollment.snapshot(SimpleNamespace(data={}), entry.entry_id)['status'], 'not_started')

    async def test_diagnostics_are_downloadable_for_both_families_and_isolated_per_entry(self):
        module = load_source('diagnostics', {'VERSION': 'test'})
        for connect3 in (True, False):
            instance, entry = fixture()
            entry.domain = 'welcomeeye_local'
            entry.runtime_data = SimpleNamespace(
                capabilities=SimpleNamespace(connect3_read=connect3, r002_probe=not connect3,
                                             as_dict=lambda: {}),
                protocol_family=SimpleNamespace(value='synthetic'),
                variant=SimpleNamespace(value='synthetic'), diagnostics=lambda: {})
            enrollment.record(instance.hass, entry.entry_id, 'failed', 'device_list', 'invalid_response')
            result = await module.async_get_config_entry_diagnostics(instance.hass, entry)
            self.assertEqual(result['qv_cloud_enrollment']['stage'], 'device_list')
            self.assertNotIn('SECRET', repr(result))
            result['qv_cloud_enrollment']['stage'] = 'mutated'
            self.assertEqual(enrollment.snapshot(instance.hass, entry.entry_id)['stage'], 'device_list')
            self.assertEqual(enrollment.snapshot(instance.hass, 'unrelated')['status'], 'not_started')

    async def test_unknown_error_fields_are_not_exported(self):
        instance, entry = fixture()
        await instance.async_step_reconfigure({'qv_cloud_action': 'configure'})
        error = RuntimeError('PRIVATE_PAYLOAD')
        error.code = 'PRIVATE_TOKEN'
        instance._qv_authorized_devices.side_effect = error
        result = await self.enroll(instance)
        self.assertEqual(result['description_placeholders']['cloud_error'], 'unexpected_error')
        self.assertNotIn('PRIVATE', repr(result) + repr(instance.hass.data))

    async def test_unload_keeps_diagnostic_and_removal_forgets_only_deleted_entry(self):
        instance, entry = fixture()
        entry.data = {'protocol_family': 'unknown'}
        entry.runtime_data = SimpleNamespace(stop=AsyncMock(),
            capabilities=SimpleNamespace(connect3_read=False, r002_probe=False))
        instance.hass.config_entries = SimpleNamespace(async_unload_platforms=AsyncMock(return_value=True))
        photo_library = SimpleNamespace(detach=AsyncMock())
        tree = ast.parse((ROOT / '__init__.py').read_text(encoding='utf-8'))
        tree.body = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                     and node.name in ('async_remove_entry', 'async_unload_entry')]
        scope = {'__name__': PACKAGE, '__package__': PACKAGE, 'HomeAssistant': object,
                 'ConfigEntry': object, 'variant_for': cap.variant_for, 'DeviceVariant': cap.DeviceVariant,
                 'PLATFORMS': [], 'photo_manager': lambda hass: photo_library,
                 'remove_panel_if_empty': lambda hass: None}
        exec(compile(tree, str(ROOT / '__init__.py'), 'exec'), scope)
        enrollment.record(instance.hass, entry.entry_id, 'failed', 'account_login', 'request_timeout')
        enrollment.record(instance.hass, 'other', 'configured', 'complete')
        self.assertTrue(await scope['async_unload_entry'](instance.hass, entry))
        self.assertEqual(enrollment.snapshot(instance.hass, entry.entry_id)['error_code'], 'request_timeout')
        await scope['async_remove_entry'](instance.hass, entry)
        self.assertEqual(enrollment.snapshot(instance.hass, entry.entry_id)['status'], 'not_started')
        self.assertEqual(enrollment.snapshot(instance.hass, 'other')['status'], 'configured')
        enrollment.forget(instance.hass, 'other')
        self.assertEqual(instance.hass.data, {})
