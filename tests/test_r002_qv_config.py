"""R002 opts into QV in place; configuration never authenticates or sends media."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import cap
from test_connect3_config import flow


class R002ConfigTests(unittest.IsolatedAsyncioTestCase):
    def configured(self, **data):
        instance = flow()
        entry = SimpleNamespace(entry_id='existing-r002', unique_id='r002-original',
            title='My R002', data={
                'host': '192.0.2.1', 'protocol_family': cap.ProtocolFamily.R002,
                'device_variant': cap.DeviceVariant.R002,
                'identity_source': 'provisional_host_hash', 'fingerprint': {'detected': True},
                **data})
        instance._get_reconfigure_entry = lambda: entry
        instance._async_current_entries = lambda: [entry]
        return instance, entry

    async def test_in_place_upgrade_preserves_identity_family_and_diagnostics(self):
        instance, entry = self.configured()
        result = await instance.async_step_reconfigure({'host': '192.0.2.2',
            'auth_code': 'LOCAL_PASSWORD', 'opening_code': 'SEPARATE_OPENING_CODE',
            'certificate_sha256': ':'.join(['AB'] * 32), 'experimental_video': True,
            'experimental_outputs': True})
        self.assertEqual(result['reason'], 'reconfigure_successful')
        self.assertEqual(entry.entry_id, 'existing-r002')
        self.assertEqual(entry.unique_id, 'r002-original')
        self.assertEqual(entry.title, 'My R002')
        self.assertEqual(entry.data['protocol_family'], cap.ProtocolFamily.R002)
        self.assertEqual(entry.data['device_variant'], cap.DeviceVariant.R002)
        self.assertEqual(entry.data['identity_source'], 'provisional_host_hash')
        self.assertEqual(entry.data['fingerprint'], {'detected': True})
        self.assertEqual(entry.data['auth_code'], 'LOCAL_PASSWORD')
        self.assertEqual(entry.data['opening_code'], 'SEPARATE_OPENING_CODE')
        self.assertEqual(entry.data['certificate_sha256'], 'ab' * 32)
        self.assertTrue(entry.data['experimental_video'])
        self.assertTrue(entry.data['experimental_outputs'])
        instance.hass.async_add_executor_job.assert_not_called()

    async def test_old_entries_default_to_diagnostics_until_opted_in(self):
        instance, entry = self.configured()
        await instance.async_step_reconfigure({'host': '192.0.2.1'})
        self.assertFalse(entry.data['experimental_video'])
        self.assertFalse(entry.data['experimental_outputs'])
        self.assertNotIn('auth_code', entry.data)
        self.assertNotIn('opening_code', entry.data)

    async def test_secrets_not_prefilled_blank_keeps_and_clear_disables(self):
        instance, entry = self.configured(auth_code='PRIVATE_PASSWORD', opening_code='PRIVATE_OPENING',
            certificate_sha256='a' * 64, experimental_video=True, experimental_outputs=True)
        result = await instance.async_step_reconfigure()
        self.assertEqual(result['step_id'], 'reconfigure')
        self.assertNotIn('PRIVATE', repr(result))
        self.assertNotIn('a' * 64, repr(result))
        self.assertEqual(set(result['data_schema']), {'host', 'auth_code', 'opening_code',
            'certificate_sha256', 'experimental_video', 'experimental_outputs', 'clear_credentials'})
        await instance.async_step_reconfigure({'host': '192.0.2.1', 'auth_code': '',
            'opening_code': '', 'certificate_sha256': ''})
        self.assertEqual(entry.data['auth_code'], 'PRIVATE_PASSWORD')
        self.assertEqual(entry.data['opening_code'], 'PRIVATE_OPENING')
        self.assertEqual(entry.data['certificate_sha256'], 'a' * 64)
        await instance.async_step_reconfigure({'host': '192.0.2.1', 'clear_credentials': True})
        for key in ('auth_code', 'opening_code', 'certificate_sha256'):
            self.assertEqual(entry.data[key], '')
        self.assertFalse(entry.data['experimental_video'])
        self.assertFalse(entry.data['experimental_outputs'])
        instance.hass.async_add_executor_job.assert_not_called()

    async def test_outputs_never_reuse_local_password_as_opening_code(self):
        instance, entry = self.configured(auth_code='CONNECTION_ONLY')
        before = dict(entry.data)
        result = await instance.async_step_reconfigure({'host': '192.0.2.1',
            'experimental_video': True, 'experimental_outputs': True})
        self.assertEqual(result['errors']['base'], 'r002_opening_code_required')
        self.assertEqual(entry.data, before)

    async def test_invalid_updates_are_atomic_and_do_not_contact_device(self):
        for update in ({'host': '224.0.0.1'}, {'host': '0.0.0.0'}, {'host': '255.255.255.255'},
                       {'certificate_sha256': 'invalid'}, {'auth_code': 'bad\npassword'},
                       {'opening_code': 'bad\ncode'}, {'experimental_video': 'yes'},
                       {'experimental_outputs': 1}):
            with self.subTest(update=update):
                instance, entry = self.configured()
                before = dict(entry.data)
                result = await instance.async_step_reconfigure({'host': '192.0.2.1', **update})
                self.assertEqual(result['errors']['base'], 'invalid_r002_config')
                self.assertEqual(entry.data, before)
                instance.hass.async_add_executor_job.assert_not_called()

    async def test_r002_never_routes_to_connect3_or_imports_its_qr(self):
        instance, entry = self.configured()
        instance._connect3_form = AsyncMock(side_effect=AssertionError('Connect 3 route forbidden'))
        parser = Mock(side_effect=AssertionError('Connect 3 QR parsing forbidden'))
        with patch.dict(instance.async_step_reconfigure.__func__.__globals__, parse_installation_qr=parser):
            result = await instance.async_step_reconfigure({'host': '192.0.2.1',
                'installation_qr': 'PRIVATE_AP UID PASSWORD IDS94E6SW', 'cgi_port': 8443,
                'media_port': 8443, 'media_certificate_sha256': 'b' * 64})
        self.assertEqual(result['reason'], 'reconfigure_successful')
        for key in ('installation_qr', 'cgi_port', 'media_port', 'media_certificate_sha256',
                    'credential_device_uid', 'auth_code'):
            self.assertNotIn(key, entry.data)
        parser.assert_not_called()
        instance._connect3_form.assert_not_called()
        instance.hass.async_add_executor_job.assert_not_called()

    async def test_duplicate_host_and_conflicting_family_rejected_in_place(self):
        instance, entry = self.configured()
        other = SimpleNamespace(entry_id='other', data={'host': '192.0.2.2'})
        instance._async_current_entries = lambda: [entry, other]
        result = await instance.async_step_reconfigure({'host': '192.0.2.2'})
        self.assertEqual(result['reason'], 'already_configured')
        self.assertEqual(entry.data['host'], '192.0.2.1')
        entry.data['device_variant'] = cap.DeviceVariant.CONNECT3
        result = await instance.async_step_reconfigure({'host': '192.0.2.1'})
        self.assertEqual(result['reason'], 'unsupported_family')
        instance.hass.async_add_executor_job.assert_not_called()
