"""Shared photo sidebar preferences; no HA installation or device required."""
import ast
import asyncio
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from load_integration import ROOT, load


class PhotoSidebarTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.panels = {}
        self.libraries = {}
        self.manager = SimpleNamespace(libraries=self.libraries)
        self.hass = SimpleNamespace(data={},
            http=SimpleNamespace(async_register_static_paths=AsyncMock()),
            async_add_executor_job=AsyncMock(side_effect=lambda function: function()))
        async def register(hass, path, name, **kwargs):
            self.assertNotIn(path, self.panels)
            self.panels[path] = kwargs
        self.register = AsyncMock(side_effect=register)
        self.remove = Mock(side_effect=lambda hass, path: self.panels.pop(path))
        self.ws_register = Mock()
        path = ROOT / 'photo_api.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        tree.body = [node for node in tree.body
            if isinstance(node, (ast.Assign, ast.FunctionDef, ast.AsyncFunctionDef))
            and getattr(node, 'name', None) != 'photos_command']
        self.scope = dict(__file__=str(path), asyncio=asyncio, Path=Path, sha256=sha256,
            DOMAIN='welcomeeye_local', manager=lambda hass: self.manager,
            show_photo_library=load('photo_settings').show_photo_library,
            frontend=SimpleNamespace(async_remove_panel=self.remove),
            panel_custom=SimpleNamespace(async_register_panel=self.register),
            websocket_api=SimpleNamespace(async_register_command=self.ws_register),
            StaticPathConfig=lambda *args: args, photos_command=object())
        exec(compile(tree, str(path), 'exec'), self.scope)

    def attach(self, identifier, data=None):
        self.libraries[identifier] = SimpleNamespace(entry=SimpleNamespace(
            entry_id=identifier, data={} if data is None else data))

    async def setup(self):
        await self.scope['async_setup_photos'](self.hass)

    async def refresh(self):
        await self.scope['ensure_panel'](self.hass)

    def remove_unused(self):
        self.scope['remove_panel_if_empty'](self.hass)

    async def test_setup_registers_api_once_without_flashing_sidebar(self):
        await asyncio.gather(self.setup(), self.setup())
        self.assertFalse(self.panels)
        self.ws_register.assert_called_once()
        self.hass.http.async_register_static_paths.assert_awaited_once()
        self.attach('hidden', {'show_photo_library': False})
        await self.refresh()
        self.assertFalse(self.panels)

    async def test_default_visible_for_all_models_and_old_entries(self):
        await self.setup()
        for model in ('v1', 'connect2_r001', 'connect2_r002', 'connect3'):
            with self.subTest(model=model):
                self.attach(model, {'device_variant': model})
                await self.refresh()
                panel = self.panels['welcomeeye-photos']
                self.assertTrue(panel['require_admin'])
                self.assertTrue(panel['module_url'].startswith('/welcomeeye_local/photos.js?v='))
                self.libraries.pop(model)
                self.remove_unused()
                self.assertFalse(self.panels)

    async def test_all_disabled_stays_hidden_and_preserves_libraries(self):
        await self.setup()
        self.attach('one', {'show_photo_library': False})
        self.attach('two', {'show_photo_library': False})
        original = dict(self.libraries)
        await self.refresh()
        self.assertFalse(self.panels)
        self.assertEqual(self.libraries, original)
        self.assertTrue(self.hass.data['welcomeeye_local_photos_registered'])
        self.register.assert_not_awaited()

    async def test_mixed_preferences_and_last_visible_device_unload(self):
        await self.setup()
        self.attach('hidden', {'show_photo_library': False})
        self.attach('visible', {'show_photo_library': True})
        await self.refresh()
        self.assertTrue(self.panels)
        self.libraries.pop('visible')
        self.remove_unused()
        self.assertFalse(self.panels)
        self.assertIn('hidden', self.libraries)

    async def test_hide_reload_and_reenable_without_duplicate_api(self):
        await self.setup()
        self.attach('device')
        await self.refresh()
        self.libraries.pop('device')
        self.remove_unused()
        self.attach('device', {'show_photo_library': False})
        await self.refresh()
        self.assertFalse(self.panels)
        self.libraries['device'].entry.data = {'show_photo_library': True}
        await self.refresh()
        await self.setup()
        self.assertTrue(self.panels)
        self.assertEqual(self.register.await_count, 2)
        self.ws_register.assert_called_once()
        self.hass.http.async_register_static_paths.assert_awaited_once()

    async def test_concurrent_refresh_registers_only_once(self):
        await self.setup()
        self.attach('first'); self.attach('second')
        await asyncio.gather(*(self.refresh() for _ in range(4)))
        self.register.assert_awaited_once()

    async def test_unload_during_registration_leaves_no_orphan_panel(self):
        await self.setup()
        self.attach('device')
        began, finish = asyncio.Event(), asyncio.Event()
        async def delayed(hass, path, name, **kwargs):
            began.set()
            await finish.wait()
            self.panels[path] = kwargs
        self.register.side_effect = delayed
        task = asyncio.create_task(self.refresh())
        await began.wait()
        self.libraries.pop('device')
        self.remove_unused()
        finish.set()
        await task
        self.assertFalse(self.panels)
        self.assertFalse(self.hass.data['welcomeeye_local_photos_panel_visible'])

    async def test_registration_error_does_not_claim_visible_and_can_retry(self):
        await self.setup()
        self.attach('device')
        real_register = self.register.side_effect
        self.register.side_effect = RuntimeError('synthetic panel failure')
        with self.assertRaises(RuntimeError):
            await self.refresh()
        self.assertFalse(self.hass.data['welcomeeye_local_photos_panel_visible'])
        self.register.side_effect = real_register
        await self.refresh()
        self.assertTrue(self.panels)


if __name__ == '__main__':
    unittest.main()
