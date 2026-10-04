"""Real saved files, restart adoption and URI confinement without a device."""
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import AsyncMock

from test_fresh_snapshot import capture_module, storage_namespace
from test_media_storage import jpeg_bytes


class Source:
    def __init__(self, domain):
        self.domain = domain


class Node(SimpleNamespace):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.media_content_id = 'media-source://' + self.domain
        if self.identifier:
            self.media_content_id += '/' + self.identifier


class Item(SimpleNamespace):
    def __init__(self, hass, domain, identifier, target_media_player=None):
        super().__init__(hass=hass, domain=domain, identifier=identifier,
                         target_media_player=target_media_player)


class MediaError(Exception):
    pass


ns = capture_module('media_source.py', {
    'DOMAIN': 'welcomeeye_local', 'MEDIA_DIRECTORY': 'WelcomeEye',
    '_resolved_path': storage_namespace['_resolved_path'],
    'MediaSource': Source, 'BrowseMediaSource': Node, 'MediaSourceItem': Item,
    'PlayMedia': lambda url, mime_type, **kwargs: SimpleNamespace(
        url=url, mime_type=mime_type, **kwargs),
    'MediaClass': SimpleNamespace(DIRECTORY='directory', IMAGE='image'),
    'MediaSourceError': MediaError, 'Unresolvable': MediaError,
    'LocalSource': lambda hass, *args: hass.data['local-source'],
})


class WelcomeEyeSourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'media'
        self.config = SimpleNamespace(media_dirs={'local': str(self.root)}, time_zone='Europe/Paris')
        self.local = SimpleNamespace(async_resolve_media=AsyncMock(
            return_value=SimpleNamespace(url='/media/local/WelcomeEye/photo.jpg')))
        self.hass = SimpleNamespace(config=self.config, async_add_executor_job=asyncio.to_thread,
                                    data={'local-source': self.local})
        self.source = await ns['async_get_media_source'](self.hass)

    def item(self, identifier=''):
        return Item(self.hass, 'welcomeeye_local', identifier)

    def legacy(self, relative='welcomeeye_abc/2026-10-04/photo_manual.jpg'):
        path = self.root / 'WelcomeEye' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(jpeg_bytes())
        return path

    async def test_beta5_adopted_without_modifying_bytes_names_or_timestamps(self):
        path = self.legacy()
        before = (path.stat().st_mtime_ns, sha256(path.read_bytes()).hexdigest())
        unrelated = self.root / 'unrelated.jpg'
        unrelated.write_bytes(jpeg_bytes())
        for restart in range(2):
            source = await ns['async_get_media_source'](self.hass)
            node = await source.async_browse_media(self.item())
            self.assertEqual(node.title, 'WelcomeEye')
            self.assertEqual(node.thumbnail, ns['BRAND_LOGO'])
            self.assertEqual([child.title for child in node.children], ['welcomeeye_abc'])
            node = await source.async_browse_media(self.item(node.children[0].identifier))
            node = await source.async_browse_media(self.item(node.children[0].identifier))
            self.assertEqual([child.title for child in node.children], [path.name])
            self.assertTrue(node.children[0].can_play)
            result = await source.async_resolve_media(self.item(node.children[0].identifier))
            self.assertEqual(result.path, path)
            self.assertEqual(result.mime_type, 'image/jpeg')
        self.assertEqual((path.stat().st_mtime_ns, sha256(path.read_bytes()).hexdigest()), before)
        self.assertTrue(unrelated.is_file())
        self.assertEqual(len(list(self.root.rglob('*.jpg'))), 2)

    async def test_new_capture_uses_same_folder_and_native_source(self):
        hub = SimpleNamespace(hass=self.hass, entry=SimpleNamespace(entry_id='opaque'))
        storage = storage_namespace['CaptureMediaStorage'](hub)
        result = await storage.save(jpeg_bytes(), datetime.now(timezone.utc), 'manual')
        self.assertTrue(result['media_content_id'].startswith('media-source://welcomeeye_local/'))
        resolved = await self.source.async_resolve_media(
            self.item(result['media_content_id'].split('welcomeeye_local/', 1)[1]))
        self.assertEqual(resolved.path.read_bytes(), jpeg_bytes())
        item = self.local.async_resolve_media.await_args.args[0]
        self.assertEqual(item.domain, 'media_source')
        self.assertEqual(item.identifier, 'local/' + result['filename'])

    async def test_empty_library_works_without_creating_files_or_network(self):
        self.assertEqual((await self.source.async_browse_media(self.item())).children, [])
        self.config.media_dirs = {}
        self.assertEqual((await self.source.async_browse_media(self.item())).children, [])
        self.assertFalse(self.root.exists())
        self.local.async_resolve_media.assert_not_awaited()

    async def test_multiple_roots_include_previous_custom_root(self):
        previous = Path(self.temp.name) / 'previous'
        folder = previous / 'WelcomeEye' / 'welcomeeye_old'
        folder.mkdir(parents=True)
        (folder / 'old.jpg').write_bytes(jpeg_bytes())
        self.config.media_dirs = {'archive pictures': str(previous), 'local': str(self.root)}
        root = await self.source.async_browse_media(self.item())
        self.assertEqual([child.title for child in root.children], ['archive pictures', 'local'])
        old = await self.source.async_browse_media(self.item(root.children[0].identifier))
        self.assertEqual(old.children[0].title, 'welcomeeye_old')
        old = await self.source.async_browse_media(self.item(old.children[0].identifier))
        result = await self.source.async_resolve_media(self.item(old.children[0].identifier))
        self.assertEqual(result.path.read_bytes(), jpeg_bytes())

    async def test_escaped_names_round_trip_and_encoded_url(self):
        path = self.legacy('appareil été/2026-10-04/été 100%.jpg')
        node = await self.source.async_browse_media(self.item('local/WelcomeEye/appareil%20%C3%A9t%C3%A9/2026-10-04'))
        self.local.async_resolve_media.return_value.url = '/media/local/WelcomeEye/appareil été/2026-10-04/été 100%.jpg'
        resolved = await self.source.async_resolve_media(self.item(node.children[0].identifier))
        self.assertEqual(resolved.path, path)
        self.assertIn('%C3%A9t%C3%A9%20100%25.jpg', resolved.url)

    async def test_directories_first_newest_dates_and_files_first(self):
        self.legacy('welcomeeye_abc/2026-10-03/old.jpg')
        self.legacy('welcomeeye_abc/2026-10-04/2026-10-04_10-00-00_manual.jpg')
        self.legacy('welcomeeye_abc/2026-10-04/2026-10-04_11-00-00_manual.jpg')
        node = await self.source.async_browse_media(self.item('local/WelcomeEye/welcomeeye_abc'))
        self.assertEqual([child.title for child in node.children], ['2026-10-04', '2026-10-03'])
        node = await self.source.async_browse_media(self.item(node.children[0].identifier))
        self.assertIn('11-00-00', node.children[0].title)

    async def test_invalid_paths_never_reach_local_resolver(self):
        self.legacy()
        for identifier in ('local/other/photo.jpg', 'local/WelcomeEye/../other.jpg',
                           'local/WelcomeEye/%2e%2e/other.jpg', 'local/WelcomeEye/%2Ftmp/x.jpg',
                           'local/WelcomeEye/%5csecret.jpg', 'local/WelcomeEye//photo.jpg',
                           'local/WelcomeEye/.hidden.jpg', 'local/WelcomeEye/C%3A/photo.jpg',
                           'local/WelcomeEye/photo%00.jpg', 'unknown/WelcomeEye/photo.jpg',
                           'local/WelcomeEye', 'local/WelcomeEye/missing.jpg', ''):
            with self.subTest(identifier=identifier), self.assertRaises(MediaError):
                await self.source.async_resolve_media(self.item(identifier))
        self.local.async_resolve_media.assert_not_awaited()

    async def test_hidden_temporary_and_non_jpeg_files_are_not_exposed(self):
        path = self.legacy()
        for name in ('.unfinished.jpg', 'secret.json', 'other.png', '.welcomeeye-1.tmp'):
            (path.parent / name).write_bytes(b'private')
        node = await self.source.async_browse_media(self.item('local/WelcomeEye/welcomeeye_abc/2026-10-04'))
        self.assertEqual([child.title for child in node.children], [path.name])
        with self.assertRaises(MediaError):
            await self.source.async_resolve_media(self.item('local/WelcomeEye/welcomeeye_abc/2026-10-04/secret.json'))

    async def test_symlinks_are_not_browsed_or_resolved(self):
        self.legacy()
        outside = self.root / 'outside.jpg'
        outside.write_bytes(jpeg_bytes())
        linked = self.root / 'WelcomeEye' / 'linked.jpg'
        try:
            linked.symlink_to(outside)
        except OSError:
            self.skipTest('OS does not permit symlinks')
        node = await self.source.async_browse_media(self.item())
        self.assertNotIn('linked.jpg', [child.title for child in node.children])
        with self.assertRaises(MediaError):
            await self.source.async_resolve_media(self.item('local/WelcomeEye/linked.jpg'))
        linked.unlink()
        with self.assertRaises(MediaError):
            (self.root / 'WelcomeEye' / 'linked').symlink_to(self.root, target_is_directory=True)
            await self.source.async_browse_media(self.item('local/WelcomeEye/linked'))

    async def test_filesystem_checks_run_outside_event_loop(self):
        self.legacy()
        thread_ids = []
        original = self.hass.async_add_executor_job
        async def observed(function, *args):
            def call():
                thread_ids.append(threading.get_ident())
                return function(*args)
            return await original(call)
        self.hass.async_add_executor_job = observed
        await self.source.async_browse_media(self.item())
        await self.source.async_resolve_media(self.item('local/WelcomeEye/welcomeeye_abc/2026-10-04/photo_manual.jpg'))
        self.assertEqual(len(thread_ids), 2)
        self.assertNotIn(threading.get_ident(), thread_ids)
