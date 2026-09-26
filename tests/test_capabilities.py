"""Capability/registry migration regressions; actual platform setup functions."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from load_integration import cap, ROOT


class CapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def entities(self, variant):
        capabilities = cap.MATRIX[variant]
        hub = SimpleNamespace(capabilities=capabilities)
        entry = SimpleNamespace(runtime_data=hub)
        result = []
        for platform in ('camera', 'button', 'binary_sensor', 'sensor', 'switch', 'image'):
            tree = ast.parse((ROOT/f'{platform}.py').read_text())
            classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
            scope = {name: (lambda *args, name=name: name) for name in classes}
            setup = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                         and node.name == 'async_setup_entry')
            exec(compile(ast.Module(body=[setup], type_ignores=[]), str(ROOT/f'{platform}.py'), 'exec'), scope)
            await scope['async_setup_entry'](None, entry, result.extend)
        return result

    async def test_r001_entities(self):
        entities = await self.entities(cap.DeviceVariant.R001)
        self.assertEqual(len(entities), 10)
        self.assertIn('WelcomeEyeRing', entities)
        self.assertIn('WelcomeEyeRingCaptureSwitch', entities)
        self.assertIn('WelcomeEyeRingImage', entities)
        self.assertEqual(entities.count('WelcomeEyeOpenButton'), 2)

    async def test_v1_supported_entities_only(self):
        entities = await self.entities(cap.DeviceVariant.V1)
        self.assertEqual(len(entities), 7)
        self.assertNotIn('WelcomeEyeRing', entities)
        self.assertNotIn('WelcomeEyeRingCaptureSwitch', entities)
        self.assertNotIn('WelcomeEyeRingImage', entities)
        self.assertIn('WelcomeEyeCamera', entities)
        self.assertIn('WelcomeEyeSnapshotImage', entities)
        self.assertEqual(entities.count('WelcomeEyeOpenButton'), 2)
        self.assertTrue(cap.MATRIX[cap.DeviceVariant.V1].talkback)

    async def test_r002_only_one_diagnostic_entity(self):
        self.assertEqual(await self.entities(cap.DeviceVariant.R002), ['WelcomeEyeProtocolStatus'])

    async def test_unknown_legacy_intersection(self):
        self.assertEqual(await self.entities(cap.DeviceVariant.LEGACY_UNKNOWN),
                         await self.entities(cap.DeviceVariant.V1))
        r001 = cap.MATRIX[cap.DeviceVariant.R001].as_dict()
        v1 = cap.MATRIX[cap.DeviceVariant.V1].as_dict()
        for name, supported in cap.MATRIX[cap.DeviceVariant.LEGACY_UNKNOWN].as_dict().items():
            self.assertEqual(supported, r001[name] and v1[name])

    def test_identity_priority_and_migration(self):
        self.assertEqual(cap.variant_for({'protocol_family': 'r002_experimental'}, 'WelcomeEye Connect 2'), cap.DeviceVariant.R002)
        self.assertEqual(cap.variant_for({'firmware': 'V401.R002.A302.00.G0058.B002'}), cap.DeviceVariant.R002)
        self.assertEqual(cap.variant_for({'firmware': 'V401.R001.XXX'}), cap.DeviceVariant.R001)
        self.assertEqual(cap.variant_for({'detected_model': 'WelcomeEye Connect V1'}), cap.DeviceVariant.V1)
        self.assertEqual(cap.variant_for({}), cap.DeviceVariant.LEGACY_UNKNOWN)

    def test_upgrade_removes_only_exact_unsupported_registry_identifiers(self):
        entries = [SimpleNamespace(entity_id=f'{domain}.renamed_{suffix}', domain=domain,
            config_entry_id='entry', platform='welcomeeye_local', unique_id=f'uid_{suffix}')
            for domain, suffix in cap.ENTITY_CAPABILITIES]
        # User renamed entities still match by unique_id. Other integrations,
        # entries, domains and invented suffixes are never migration targets.
        protected = [SimpleNamespace(entity_id='switch.user', domain='switch',
            config_entry_id='entry', platform='other', unique_id='uid_ring_image_capture'),
            SimpleNamespace(entity_id='image.other', domain='image', config_entry_id='other',
                platform='welcomeeye_local', unique_id='uid_last_ring'),
            SimpleNamespace(entity_id='sensor.user', domain='sensor', config_entry_id='entry',
                platform='welcomeeye_local', unique_id='uid_user_defined'),
            SimpleNamespace(entity_id='light.same_suffix', domain='light', config_entry_id='entry',
                platform='welcomeeye_local', unique_id='uid_ring')]
        result = set(cap.unsupported_entity_ids(entries+protected, 'entry', 'uid', cap.MATRIX[cap.DeviceVariant.V1]))
        self.assertEqual(result, {'binary_sensor.renamed_ring', 'switch.renamed_ring_image_capture',
                                 'image.renamed_last_ring', 'sensor.renamed_protocol_status'})

    def test_r001_restores_supported_ring_entities(self):
        entry = SimpleNamespace(entity_id='image.ring', domain='image', config_entry_id='entry',
                                platform='welcomeeye_local', unique_id='uid_last_ring')
        self.assertEqual(list(cap.unsupported_entity_ids([entry], 'entry', 'uid', cap.MATRIX[cap.DeviceVariant.R001])), [])

    def test_media_output_protocol_sources_unchanged(self):
        # Protocol code is checked against the stable tree in the audit report;
        # this structural invariant prevents R002 depending on output/media.
        for path in (ROOT/'r002').glob('*.py'):
            tree = ast.parse(path.read_text())
            imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            self.assertFalse(set(imports) & {'client', 'control', 'protected', 'ring', 'rtc', 'media', 'snapshot'})


class ModelReloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_identification_reloads_once_after_viewer_release_and_persists(self):
        import asyncio
        from unittest.mock import AsyncMock, patch
        from test_hub_lifecycle import Hub, scope
        from load_integration import CAP_IMPORTS
        hub=Hub.__new__(Hub)
        hub.device_model='WelcomeEye'
        hub.device_model_confidence='unknown'
        hub.entry=SimpleNamespace(data={},title='WelcomeEye',unique_id='fixture',entry_id='fixture')
        hub._capability_reload_pending=hub._capability_reload_scheduled=False
        hub.stopped=False
        hub.consumers={'viewer'}
        hub.lock=asyncio.Lock()
        hub._halt_media=AsyncMock()
        hub._notify=Mock()
        hub.ring_listener=SimpleNamespace(close=Mock())
        hub.ring_image=SimpleNamespace(set_enabled=Mock())
        hub.ring_timer=None
        tasks=[]
        def update(entry,**changes):
            for key,value in changes.items(): setattr(entry,key,value)
        def create_task(coro,name):
            task=asyncio.create_task(coro,name=name);tasks.append(task);return task
        reload=AsyncMock()
        hub.hass=SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update,async_reload=reload),async_create_task=create_task)
        registry=SimpleNamespace(async_get_device_by_identifier=Mock(return_value=None))
        with patch.dict(scope,dr=SimpleNamespace(async_get=lambda hass:registry)):
            hub._observe_device_model(SimpleNamespace(width=720,height=576),100)
            hub._observe_device_model(SimpleNamespace(width=720,height=576),100)
            reload.assert_not_called()
            self.assertEqual(hub.entry.data['device_variant'],'connect2_r001')
            await hub.release('viewer')
            await asyncio.gather(*tasks)
            hub._schedule_capability_reload()
            hub._observe_device_model(SimpleNamespace(width=720,height=576),100)
        reload.assert_awaited_once_with('fixture')
        self.assertEqual(cap.variant_for(hub.entry.data),cap.DeviceVariant.R001)
