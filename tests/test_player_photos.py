"""Saved-history endpoint: permissions, channel isolation and no live media."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from load_integration import load
from test_player_multichannel import cameras, player


def photo(index, channel=1, kind='ring'):
    name = f'2026-01-01_12-00-{index:02d}_{kind}_1.jpg'
    return SimpleNamespace(name=name, public=lambda: {
        'id': str(index), 'name': name, 'day': '2026-01-01', 'channel': channel,
        'media_content_id': f'media-source://welcomeeye_local/local/WelcomeEye/example/{name}'})


class SavedPhotoTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, admin=True):
        devices = cameras()
        module, connection, registry = player(devices)
        connection.user.is_admin = admin
        library = SimpleNamespace(list=AsyncMock(return_value=[photo(1), photo(2, 2)]))
        manager = SimpleNamespace(library=Mock(return_value=library))
        module.photo_manager = Mock(return_value=manager)
        module.PhotoError = load('photo_files').PhotoError
        return module, connection, registry, manager, library, devices

    async def test_only_selected_device_channel_and_never_media(self):
        module, connection, _, manager, library, devices = self.fixture()
        await module.player_photos(None, connection, {'id': 1, 'entity_id': 'camera.arbitrary_secondary'})
        data = connection.send_result.call_args.args[1]
        self.assertEqual([item['channel'] for item in data['photos']], [2])
        self.assertEqual(data['channel'], 2)
        manager.library.assert_called_once_with('same-entry')
        library.list.assert_awaited_once_with()
        for camera in devices.values():
            camera.rtc.offer.assert_not_called()
            camera.rtc.close.assert_not_called()

    async def test_camera_permission_refused_before_disk_access(self):
        module, connection, _, manager, _, _ = self.fixture()
        connection.user.permissions.check_entity.return_value = False
        connection.user.permissions.check_entity.side_effect = None
        await module.player_photos(None, connection, {'id': 1, 'entity_id': 'camera.arbitrary_secondary'})
        manager.library.assert_not_called()
        connection.send_error.assert_called_once()

    async def test_non_admin_needs_read_access_to_capture_images(self):
        module, connection, _, manager, _, _ = self.fixture(admin=False)
        await module.player_photos(None, connection, {'id': 1, 'entity_id': 'camera.renamed_primary'})
        manager.library.assert_not_called()
        connection.send_error.assert_called_once()

    async def test_image_permissions_filter_kinds_and_registry_renames(self):
        module, connection, registry, _, library, _ = self.fixture(admin=False)
        registry.async_get_entity_id.side_effect = lambda domain, integration, unique: 'image.custom_name' if unique == 'primary_last_snapshot' else None
        connection.user.permissions.check_entity.side_effect = lambda entity, policy: entity in ('camera.renamed_primary', 'image.custom_name')
        library.list.return_value = [photo(1), photo(2, kind='manual'), photo(3, 2, 'manual')]
        await module.player_photos(None, connection, {'id': 1, 'entity_id': 'camera.renamed_primary'})
        data = connection.send_result.call_args.args[1]
        self.assertEqual([item['id'] for item in data['photos']], ['2'])

    async def test_permission_revoked_during_inventory_does_not_return_photos(self):
        module, connection, _, _, library, _ = self.fixture()
        async def listing():
            connection.user.permissions.check_entity.side_effect = lambda *args: False
            return [photo(1)]
        library.list.side_effect = listing
        await module.player_photos(None, connection, {'id': 1, 'entity_id': 'camera.renamed_primary'})
        connection.send_result.assert_not_called()
        connection.send_error.assert_called_once()

    async def test_result_is_bounded_and_marks_more(self):
        module, connection, _, _, library, _ = self.fixture()
        library.list.return_value = [photo(index) for index in range(30)]
        await module.player_photos(None, connection, {'id': 1, 'entity_id': 'camera.renamed_primary'})
        data = connection.send_result.call_args.args[1]
        self.assertEqual(len(data['photos']), 12)
        self.assertTrue(data['more'])

    async def test_storage_failure_and_timeout_are_sanitized(self):
        for error in (OSError('private/path'), TimeoutError('private/path')):
            module, connection, _, _, library, _ = self.fixture()
            library.list.side_effect = error
            await module.player_photos(None, connection, {'id': 1, 'entity_id': 'camera.renamed_primary'})
            self.assertNotIn('private/path', str(connection.send_error.call_args))
            self.assertEqual(connection.send_error.call_args.args[1], 'photo_history_unavailable')

    async def test_legacy_primary_without_channel_attribute(self):
        module, connection, _, _, _, devices = self.fixture()
        del devices['camera.renamed_primary']._welcomeeye_channel
        await module.player_photos(None, connection, {'id': 1, 'entity_id': 'camera.renamed_primary'})
        self.assertEqual(connection.send_result.call_args.args[1]['channel'], 1)


if __name__ == '__main__':
    unittest.main()
