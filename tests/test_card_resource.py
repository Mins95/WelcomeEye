"""Resource migration tests using the HA 2026.9 collection API contract.

No Home Assistant install or device connection is needed. The storage fixture
starts unloaded, persists writes and exposes the same module/type translation.
"""
import asyncio
import ast
from copy import deepcopy
from hashlib import sha256
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


class StorageCollection:
    def __init__(self, disk=None):
        self.disk = disk if disk is not None else []
        self.items = None
        self.writes = []

    async def async_get_info(self):
        await asyncio.sleep(0)
        if self.items is None:
            self.items = deepcopy(self.disk)
        return {"resources": len(self.items)}

    def async_items(self):
        return self.items or []

    def save(self, operation):
        self.disk[:] = deepcopy(self.items)
        self.writes.append(operation)

    async def async_create_item(self, data):
        assert set(data) == {"url", "res_type"}
        self.items.append({"id": "created", "url": data["url"], "type": data["res_type"]})
        self.save("create")

    async def async_update_item(self, item_id, data):
        assert set(data) == {"url", "res_type"}
        item = next(item for item in self.items if item["id"] == item_id)
        item.update(url=data["url"], type=data["res_type"])
        self.save("update")

    async def async_delete_item(self, item_id):
        self.items[:] = [item for item in self.items if item["id"] != item_id]
        self.save("delete")


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "custom_components/welcomeeye_local"


def load_subject():
    names = ["homeassistant", "homeassistant.components", "homeassistant.components.lovelace",
             "homeassistant.components.lovelace.const", "homeassistant.components.lovelace.resources",
             "_welcomeeye_resource_tests"]
    modules = {name: ModuleType(name) for name in names}
    modules[names[-1]].__path__ = [str(SOURCE)]
    modules[names[-3]].LOVELACE_DATA = "lovelace"
    modules[names[-2]].ResourceStorageCollection = StorageCollection
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location(
            "_welcomeeye_resource_tests.card_resource", SOURCE / "card_resource.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


subject = load_subject()
CARD_URL, LOADER_URL = subject.get_asset_urls()


def hass_for(resources):
    return SimpleNamespace(data={"lovelace": SimpleNamespace(resources=resources)})


def card(item_id="manual", version="0.4.1", kind="module"):
    return {"id": item_id, "url": f"{subject.CARD_PATH}?v={version}", "type": kind}


class CardResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_install_persists_one_module(self):
        resources = StorageCollection()
        self.assertTrue(await subject.async_register_card_resource(hass_for(resources), CARD_URL))
        self.assertEqual(resources.disk, [{"id": "created", "url": CARD_URL, "type": "module"}])

    async def test_existing_manual_resource_loaded_before_update(self):
        resources = StorageCollection([card()])
        await subject.async_register_card_resource(hass_for(resources), CARD_URL)
        self.assertEqual(resources.writes, ["update"])
        self.assertEqual(resources.disk[0]["id"], "manual")
        self.assertEqual(resources.disk[0]["url"], CARD_URL)

    async def test_restart_and_repeat_are_idempotent(self):
        disk = []
        first = StorageCollection(disk)
        hass = hass_for(first)
        await subject.async_register_card_resource(hass, CARD_URL)
        await subject.async_register_card_resource(hass, CARD_URL)
        second = StorageCollection(disk)
        await subject.async_register_card_resource(hass_for(second), CARD_URL)
        self.assertEqual(first.writes, ["create"])
        self.assertEqual(second.writes, [])
        self.assertEqual(len(disk), 1)

    async def test_upgrade_changes_url_without_changing_id(self):
        resources = StorageCollection([card()])
        await subject.async_register_card_resource(hass_for(resources), f"{subject.CARD_PATH}?v=next")
        self.assertEqual(resources.disk, [card(version="next")])

    async def test_concurrent_calls_create_only_once(self):
        resources = StorageCollection()
        hass = hass_for(resources)
        await asyncio.gather(*(subject.async_register_card_resource(hass, CARD_URL) for _ in range(5)))
        self.assertEqual(resources.writes, ["create"])

    async def test_duplicates_removed_and_wrong_type_corrected(self):
        resources = StorageCollection([card(kind="js"), card("duplicate", "older")])
        await subject.async_register_card_resource(hass_for(resources), CARD_URL)
        self.assertEqual(resources.writes, ["update", "delete"])
        self.assertEqual(resources.disk, [{**card(), 'url': CARD_URL}])

    async def test_same_release_editor_fix_updates_resource_without_changing_identity(self):
        resources = StorageCollection([card(version=subject.VERSION)])
        await subject.async_register_card_resource(hass_for(resources), CARD_URL)
        self.assertEqual(resources.writes, ['update'])
        self.assertEqual(resources.disk, [{**card(), 'url': CARD_URL}])

    async def test_other_resources_and_other_origins_untouched(self):
        others = [
            {"id": "local", "url": "/local/another-card.js", "type": "module"},
            {"id": "external", "url": "https://example.invalid" + subject.CARD_PATH, "type": "module"},
            {"id": "relative", "url": "//example.invalid" + subject.CARD_PATH, "type": "module"},
            {"id": "invalid", "url": "http://[", "type": "module"},
        ]
        resources = StorageCollection(deepcopy(others) + [card()])
        await subject.async_register_card_resource(hass_for(resources), CARD_URL)
        self.assertEqual(resources.disk[:4], others)
        self.assertEqual(resources.writes, ["update"])

    async def test_yaml_left_unchanged(self):
        yaml = SimpleNamespace(data=[card()])
        before = deepcopy(yaml.data)
        self.assertFalse(await subject.async_register_card_resource(hass_for(yaml), CARD_URL))
        self.assertEqual(yaml.data, before)

    async def test_failure_is_nonfatal_and_does_not_log_details(self):
        class BrokenStorage(StorageCollection):
            async def async_update_item(self, item_id, data):
                raise OSError("secret-storage-content")
        resources = BrokenStorage([card(), card("duplicate")])
        with self.assertLogs(subject._LOGGER, level="WARNING") as logs:
            self.assertFalse(await subject.async_register_card_resource(hass_for(resources), CARD_URL))
        self.assertIn("OSError", str(logs.output))
        self.assertNotIn("secret-storage-content", str(logs.output))
        self.assertEqual(resources.writes, [])
        self.assertEqual(len(resources.disk), 2)

    async def test_cancelled_setup_is_not_swallowed(self):
        class CancelledStorage(StorageCollection):
            async def async_get_info(self):
                raise asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await subject.async_register_card_resource(hass_for(CancelledStorage()), CARD_URL)

    async def test_legacy_lovelace_container(self):
        resources = StorageCollection([card()])
        hass = SimpleNamespace(data={"lovelace": {"resources": resources}})
        self.assertTrue(await subject.async_register_card_resource(hass, CARD_URL))
        self.assertEqual(resources.writes, ["update"])

    async def test_import_performs_no_frontend_file_reads_in_event_loop(self):
        with patch.object(Path, "read_bytes", side_effect=AssertionError("blocking file read")):
            imported = load_subject()
        self.assertEqual(imported.CARD_PATH, subject.CARD_PATH)

    async def test_content_revision_invalidates_card_and_loader_for_same_release(self):
        with patch.object(Path, "read_bytes", side_effect=[b"card", b"loader"]):
            first = subject.get_asset_urls()
        with patch.object(Path, "read_bytes", side_effect=[b"corrected card", b"loader"]):
            changed_card = subject.get_asset_urls()
        with patch.object(Path, "read_bytes", side_effect=[b"card", b"corrected loader"]):
            changed_loader = subject.get_asset_urls()
        revision = sha256(b"card\0loader").hexdigest()[:12]
        self.assertEqual(first, (
            f"{subject.CARD_PATH}?v={subject.VERSION}&card={revision}",
            f"{subject.LOADER_PATH}?v={subject.VERSION}&card={revision}",
        ))
        for index in (0, 1):
            self.assertNotEqual(first[index], changed_card[index])
            self.assertNotEqual(first[index], changed_loader[index])

    async def test_loader_and_commands_registered_before_resource_storage_wait(self):
        tree = ast.parse((SOURCE / "player.py").read_text(encoding="utf-8"))
        tree.body = [node for node in tree.body
                     if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_setup_player"]
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def register_resource(hass, card_url):
            self.assertEqual(card_url, CARD_URL)
            entered.set()
            await release.wait()

        def static_path(url, filename, cache):
            self.assertTrue(Path(filename).is_file())
            return (url, filename, cache)

        commands = [object(), object(), object(), object()]
        namespace = {"Path": Path, "__file__": str(SOURCE / "player.py"),
                     "StaticPathConfig": static_path, "CARD_PATH": subject.CARD_PATH,
                     "LOADER_PATH": subject.LOADER_PATH, "get_asset_urls": subject.get_asset_urls,
                     "frontend": SimpleNamespace(add_extra_js_url=lambda *args: calls.append(args)),
                     "websocket_api": SimpleNamespace(async_register_command=Mock()),
                     "async_register_card_resource": register_resource,
                     "player_config": commands[0], "player_offer": commands[1], "player_stop": commands[2],
                     "player_photos": commands[3]}
        exec(compile(tree, str(SOURCE / "player.py"), "exec"), namespace)
        hass = SimpleNamespace(
            http=SimpleNamespace(async_register_static_paths=AsyncMock()),
            async_add_executor_job=AsyncMock(side_effect=asyncio.to_thread),
        )
        loop_thread = threading.get_ident()
        read_bytes = Path.read_bytes
        reads = []

        def checked_read(path):
            self.assertNotEqual(threading.get_ident(), loop_thread, "blocking read in event loop")
            reads.append(path.name)
            return read_bytes(path)

        with patch.object(Path, "read_bytes", checked_read):
            task = asyncio.create_task(namespace["async_setup_player"](hass))
            waiter = asyncio.create_task(entered.wait())
            try:
                # Also surface setup failures instead of hanging at entered.wait().
                done, _ = await asyncio.wait((task, waiter), timeout=5,
                                             return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    await task
                self.assertTrue(entered.is_set())
                self.assertEqual(reads, ["welcomeeye-card.js", "welcomeeye-loader.js"])
                hass.async_add_executor_job.assert_awaited_once_with(subject.get_asset_urls)
                self.assertEqual(calls, [(hass, LOADER_URL)])
                self.assertEqual(namespace["websocket_api"].async_register_command.call_count, 4)
                namespace["websocket_api"].async_register_command.assert_any_call(hass, commands[3])
                paths = hass.http.async_register_static_paths.call_args.args[0]
                self.assertEqual([path[0] for path in paths], [subject.CARD_PATH, subject.LOADER_PATH])
                self.assertTrue(all(path[2] is False for path in paths))
            finally:
                release.set()
                waiter.cancel()
                await asyncio.gather(task, waiter, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
