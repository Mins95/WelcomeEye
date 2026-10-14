"""Per-device native alarm TCP consent; synthetic configuration only."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest

from load_integration import ROOT, cap
from test_connect3_config import flow


def entry(*, approved=False, transport='connect3_tcp'):
    data = dict(host='192.0.2.1', protocol_family=cap.ProtocolFamily.CONNECT3,
        device_variant=cap.DeviceVariant.CONNECT3, auth_code='SYNTHETIC_ALARM_AUTH',
        opening_code='SYNTHETIC_OPENING_CODE', credential_source='manual',
        credential_device_uid='', cgi_port=443, media_port=8443,
        certificate_sha256='a' * 64, media_certificate_sha256='b' * 64,
        media_transport=transport, media_tcp_approved=transport == 'connect3_tcp',
        experimental_video=False, experimental_outputs=False,
        experimental_tcp_controls=False,
        trust_endpoint=dict(host='192.0.2.1', cgi_port=443,
            media_port=34567 if transport == 'connect3_tcp' else 8443,
            media_transport=transport))
    if approved:
        data['legacy_alarm_tcp_approved'] = True
    return SimpleNamespace(entry_id='synthetic-alarm-entry',
        unique_id='connect3-synthetic-alarm', data=data)


def configured(existing):
    instance = flow()
    instance._get_reconfigure_entry = lambda: existing
    return instance


async def accept_transport(instance, result):
    if result.get('step_id') in ('connect3_tls_confirm', 'connect3_tls_changed',
                                'connect3_tcp_confirm', 'connect3_tcp_tls_confirm'):
        return await instance.async_step_connect3_tls_confirm({'trust': True})
    return result


class AlarmTCPConsentFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_disabled_and_only_in_reconfiguration_advanced_options(self):
        instance = flow()
        initial = await instance.async_step_connect3()
        self.assertNotIn('legacy_alarm_tcp_approved', initial['data_schema']['advanced'])
        existing = entry()
        instance = configured(existing)
        markers = []
        def optional(key, **options):
            markers.append((key, options))
            return key
        instance.test_module.vol.Optional = optional
        shown = await instance.async_step_reconfigure()
        self.assertIn('legacy_alarm_tcp_approved', shown['data_schema']['advanced'])
        self.assertEqual([options['default'] for key, options in markers
            if key == 'legacy_alarm_tcp_approved'], [False])
        self.assertNotIn('SYNTHETIC', repr(shown))
        instance.test_module.inspect_trust.assert_not_awaited()

    async def test_existing_media_tcp_consent_never_grants_alarm_trial(self):
        instance = flow()
        pending = await instance.async_step_connect3(dict(host='192.0.2.1',
            auth_code='SYNTHETIC_LOCAL_AUTH', media_transport='connect3_tcp'))
        created = await instance.async_step_connect3_tcp_confirm({'trust': True})
        self.assertTrue(created['data']['media_tcp_approved'])
        self.assertFalse(created['data']['legacy_alarm_tcp_approved'])
        self.assertNotIn('legacy_alarm_tcp_approved', pending['data_schema'])

    async def test_alarm_opt_in_requires_its_own_confirmation_without_network_inspection(self):
        existing = entry()
        original = deepcopy(existing.data)
        instance = configured(existing)
        markers = []
        def required(key, **options):
            markers.append((key, options))
            return key
        instance.test_module.vol.Required = required
        pending = await instance.async_step_reconfigure({
            'advanced': {'legacy_alarm_tcp_approved': True}})
        self.assertEqual(pending['step_id'], 'connect3_legacy_alarm_tcp_confirm')
        self.assertEqual(pending['data_schema'], {'confirm': bool})
        self.assertEqual(markers[-1], ('confirm', {'default': False}))
        self.assertEqual(pending['description_placeholders'], {'target': '192.0.2.1:34567'})
        self.assertEqual(existing.data, original)
        for value in (original['auth_code'], original['opening_code'],
                      original['certificate_sha256'], original['media_certificate_sha256']):
            self.assertNotIn(value, repr(pending))
        saved = await instance.async_step_connect3_legacy_alarm_tcp_confirm({'confirm': True})
        self.assertEqual(saved['reason'], 'reconfigure_successful')
        self.assertTrue(existing.data['legacy_alarm_tcp_approved'])
        self.assertEqual(existing.unique_id, 'connect3-synthetic-alarm')
        instance.test_module.inspect_trust.assert_not_awaited()
        instance.test_module.probe_tcp_setup.assert_not_awaited()
        instance.hass.async_add_executor_job.assert_not_called()

    async def test_declined_or_truthy_confirmation_does_not_save_any_proposed_changes(self):
        for answer in ({}, {'confirm': False}, {'confirm': 1}, {'confirm': 'yes'}):
            existing = entry()
            original = deepcopy(existing.data)
            instance = configured(existing)
            await instance.async_step_reconfigure({
                'legacy_alarm_tcp_approved': True, 'language': 'fr'})
            result = await instance.async_step_connect3_legacy_alarm_tcp_confirm(answer)
            self.assertEqual(result['reason'], 'connect3_legacy_alarm_tcp_declined')
            self.assertEqual(existing.data, original)
            instance.test_module.inspect_trust.assert_not_awaited()

    async def test_concurrent_reconfiguration_invalidates_pending_confirmation(self):
        existing = entry()
        instance = configured(existing)
        await instance.async_step_reconfigure({'legacy_alarm_tcp_approved': True})
        existing.data['auth_code'] = 'SYNTHETIC_CHANGED_AUTH'
        result = await instance.async_step_connect3_legacy_alarm_tcp_confirm({'confirm': True})
        self.assertEqual(result['reason'], 'connect3_config_changed')
        self.assertNotIn('legacy_alarm_tcp_approved', existing.data)
        self.assertEqual(existing.data['auth_code'], 'SYNTHETIC_CHANGED_AUTH')

    async def test_unchanged_target_retains_prior_consent_and_explicit_false_revokes(self):
        existing = entry(approved=True)
        instance = configured(existing)
        saved = await instance.async_step_reconfigure({'language': 'fr'})
        self.assertEqual(saved['reason'], 'reconfigure_successful')
        self.assertTrue(existing.data['legacy_alarm_tcp_approved'])
        revoked = await instance.async_step_reconfigure({'legacy_alarm_tcp_approved': False})
        self.assertEqual(revoked['reason'], 'reconfigure_successful')
        self.assertFalse(existing.data['legacy_alarm_tcp_approved'])
        self.assertTrue(existing.data['media_tcp_approved'])
        instance.test_module.inspect_trust.assert_not_awaited()

    async def test_unrelated_presentation_change_preserves_absent_consent(self):
        existing = entry()
        original = deepcopy(existing.data)
        instance = configured(existing)
        saved = await instance.async_step_reconfigure({'second_channel_enabled': True})
        self.assertEqual(saved['reason'], 'reconfigure_successful')
        self.assertNotIn('legacy_alarm_tcp_approved', existing.data)
        for key, value in original.items():
            self.assertEqual(existing.data[key], value, key)
        instance.test_module.inspect_trust.assert_not_awaited()

    async def test_changed_binding_revokes_prior_consent_without_implicit_reapproval(self):
        changes = (
            {'host': '192.0.2.2'}, {'cgi_port': 444}, {'media_port': 8444},
            {'media_transport': 'tls'}, {'auth_code': 'SYNTHETIC_CHANGED_AUTH'},
            {'opening_code': 'SYNTHETIC_CHANGED_OPENING'},
            {'certificate_sha256': 'c' * 64}, {'media_certificate_sha256': 'd' * 64},
            {'installation_qr': 'AP SYNTHETIC_UID SYNTHETIC_QR_AUTH IDS94E6SW'},
        )
        for change in changes:
            with self.subTest(field=next(iter(change))):
                existing = entry(approved=True)
                instance = configured(existing)
                result = await accept_transport(instance,
                    await instance.async_step_reconfigure(change))
                self.assertEqual(result['reason'], 'reconfigure_successful')
                self.assertFalse(existing.data['legacy_alarm_tcp_approved'])

    async def test_new_target_requires_fresh_distinct_consent_even_if_checkbox_remains_true(self):
        existing = entry(approved=True)
        original = deepcopy(existing.data)
        instance = configured(existing)
        pending = await accept_transport(instance, await instance.async_step_reconfigure({
            'host': '192.0.2.2', 'legacy_alarm_tcp_approved': True}))
        self.assertEqual(pending['step_id'], 'connect3_legacy_alarm_tcp_confirm')
        self.assertEqual(pending['description_placeholders'], {'target': '192.0.2.2:34567'})
        self.assertEqual(existing.data, original)
        inspections = instance.test_module.inspect_trust.await_count
        await instance.async_step_connect3_legacy_alarm_tcp_confirm({'confirm': True})
        self.assertEqual(existing.data['host'], '192.0.2.2')
        self.assertTrue(existing.data['legacy_alarm_tcp_approved'])
        self.assertEqual(instance.test_module.inspect_trust.await_count, inspections)

    async def test_tls_transport_and_removed_credentials_cannot_keep_tcp_trial_consent(self):
        for change in ({'media_transport': 'tls', 'legacy_alarm_tcp_approved': True},
                       {'clear_credentials': True, 'legacy_alarm_tcp_approved': True}):
            existing = entry(approved=True)
            instance = configured(existing)
            result = await accept_transport(instance, await instance.async_step_reconfigure(change))
            self.assertEqual(result['reason'], 'reconfigure_successful')
            self.assertFalse(existing.data['legacy_alarm_tcp_approved'])

    async def test_non_boolean_opt_in_and_initial_hidden_field_are_rejected(self):
        for value in (1, 'yes', None):
            existing = entry()
            instance = configured(existing)
            result = await instance.async_step_reconfigure({'legacy_alarm_tcp_approved': value})
            self.assertEqual(result['errors']['base'], 'invalid_connect3_config')
            self.assertNotIn('legacy_alarm_tcp_approved', existing.data)
            instance.test_module.inspect_trust.assert_not_awaited()
        instance = flow()
        result = await instance.async_step_connect3(dict(host='192.0.2.1',
            auth_code='SYNTHETIC_AUTH', legacy_alarm_tcp_approved=True))
        self.assertEqual(result['errors']['base'], 'invalid_connect3_config')

    def test_all_translations_have_the_distinct_warning_and_confirmation(self):
        for path in (ROOT / 'strings.json', *(ROOT / 'translations').glob('*.json')):
            with self.subTest(language=path.name):
                data = json.loads(path.read_text(encoding='utf-8'))
                advanced = data['config']['step']['connect3_reconfigure']['sections']['advanced']
                self.assertIn('legacy_alarm_tcp_approved', advanced['data'])
                warning = advanced['data_description']['legacy_alarm_tcp_approved']
                for detail in ('90', '34567', 'TLS', 'HTTPS', 'hash'):
                    self.assertIn(detail, warning)
                step = data['config']['step']['connect3_legacy_alarm_tcp_confirm']
                self.assertEqual(set(step['data']), {'confirm'})
                self.assertIn('{target}', step['description'])
                self.assertIn('connect3_legacy_alarm_tcp_declined', data['config']['abort'])


if __name__ == '__main__':
    unittest.main()
