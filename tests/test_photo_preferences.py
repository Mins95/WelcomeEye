"""Photo preferences apply to every family without touching credentials/media."""
import json
from types import SimpleNamespace
import unittest

from load_integration import load, ROOT
from test_connect3_config import flow

settings = load('photo_settings')


class PreferenceValidationTests(unittest.TestCase):
    def test_defaults_and_invalid_persisted_values_preserve_safe_defaults(self):
        self.assertTrue(settings.show_photo_library({}))
        self.assertFalse(settings.show_photo_library({'show_photo_library': False}))
        self.assertEqual(settings.ring_image_delay_seconds({}), 4)
        for bad in (True, False, '15', None, -1, 121, 0.5, float('nan'), float('inf'), 10**400):
            with self.subTest(value=repr(bad)):
                with self.assertRaises(ValueError):
                    settings.validated_ring_image_delay(bad)
                self.assertEqual(settings.ring_image_delay_seconds({'ring_image_delay_seconds': bad}), 4)
        for good in (0, 4, 15, 30, 120, 15.0):
            self.assertEqual(settings.validated_ring_image_delay(good), int(good))

    def test_all_photo_forms_have_translated_fields(self):
        for filename in ('strings.json', 'translations/en.json', 'translations/fr.json', 'translations/es.json'):
            data = json.loads((ROOT / filename).read_text(encoding='utf-8'))
            for step in ('reconfigure', 'legacy_reconfigure', 'v1_cloud_reconfigure',
                         'connect3', 'connect3_reconfigure', 'r002', 'r002_connection'):
                section = data['config']['step'][step]['sections']['photo_management']
                for field in ('show_photo_library', 'ring_image_delay_seconds'):
                    self.assertTrue(section['data'][field])
                    self.assertTrue(section['data_description'][field])


class PhotoPreferenceFlowTests(unittest.IsolatedAsyncioTestCase):
    def entry(self, variant, *, manual=False):
        data = dict(device_variant=variant, host='192.0.2.1', password='PRIVATE',
            auth_code='PRIVATE_AUTH', certificate_sha256='a'*64, media_certificate_sha256='b'*64,
            opening_code='PRIVATE_OPENING', photo_retention_days=30,
            media_transport='connect3_tcp', media_tcp_approved=True,
            experimental_video=True, experimental_outputs=False, experimental_tcp_controls=False,
            cgi_port=443, media_port=8443,
            protocol_family='connect3_qv_experimental' if variant == 'connect3' else
                'r002_experimental' if variant == 'connect2_r002' else 'legacy_owsp')
        if manual:
            data['r002_connection_mode'] = 'manual'
        return SimpleNamespace(entry_id='fixture', unique_id='original', data=data)

    async def test_reconfigure_all_families_preserves_secrets_and_does_not_connect(self):
        for variant, manual in (('connect_v1', False), ('connect2_r001', False),
                                ('connect2_r002', False), ('connect2_r002', True), ('connect3', False)):
            with self.subTest(variant=variant, manual=manual):
                instance = flow()
                entry = self.entry(variant, manual=manual)
                instance._get_reconfigure_entry = lambda: entry
                form = await instance.async_step_reconfigure()
                photo = form['data_schema']['photo_management']
                self.assertIn('show_photo_library', photo)
                self.assertIn('ring_image_delay_seconds', photo)
                result = await instance.async_step_reconfigure({'photo_management': {
                    'show_photo_library': False, 'ring_image_delay_seconds': 30.0}})
                self.assertEqual(result.get('reason'), 'reconfigure_successful', result)
                self.assertEqual(entry.unique_id, 'original')
                self.assertIs(entry.data['show_photo_library'], False)
                self.assertEqual(entry.data['ring_image_delay_seconds'], 30)
                self.assertIs(type(entry.data['ring_image_delay_seconds']), int)
                self.assertEqual(entry.data['photo_retention_days'], 30)
                self.assertEqual(entry.data['password'], 'PRIVATE')
                self.assertEqual(entry.data['auth_code'], 'PRIVATE_AUTH')
                self.assertEqual(entry.data['opening_code'], 'PRIVATE_OPENING')
                self.assertEqual(entry.data['certificate_sha256'], 'a'*64)
                instance.hass.async_add_executor_job.assert_not_called()
                instance.test_module.inspect_trust.assert_not_awaited()
                instance.test_module.read_device.assert_not_awaited()
                # A later unrelated change/re-created flow preserves both preferences.
                next_flow = flow()
                next_flow._get_reconfigure_entry = lambda: entry
                result = await next_flow.async_step_reconfigure({'photo_management': {'photo_retention_days': '7'}})
                self.assertEqual(result.get('reason'), 'reconfigure_successful', result)
                self.assertIs(entry.data['show_photo_library'], False)
                self.assertEqual(entry.data['ring_image_delay_seconds'], 30)

    async def test_invalid_preferences_do_not_update_entries(self):
        for variant in ('connect_v1', 'connect2_r001', 'connect2_r002', 'connect3'):
            for bad in ({'show_photo_library': 'false'}, {'ring_image_delay_seconds': -1},
                        {'ring_image_delay_seconds': 121}, {'ring_image_delay_seconds': True}):
                with self.subTest(variant=variant, bad=bad):
                    instance = flow()
                    entry = self.entry(variant)
                    original = dict(entry.data)
                    instance._get_reconfigure_entry = lambda: entry
                    result = await instance.async_step_reconfigure({'photo_management': bad})
                    self.assertEqual(result['type'], 'form')
                    self.assertTrue(result['errors'])
                    self.assertEqual(entry.data, original)
                    instance.test_module.inspect_trust.assert_not_awaited()
