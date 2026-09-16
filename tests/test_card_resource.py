"""Resource migration tests using the HA 2026.9 collection API contract.

No Home Assistant install or device connection is needed. The storage fixture
starts unloaded, persists writes and exposes the same module/type translation.
"""
import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


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


def hass_for(resources):
    return SimpleNamespace(data={"lovelace": SimpleNamespace(resources=resources)})


def card(item_id="manual", version="0.4.1", kind="module"):
    return {"id": item_id, "url": f"{subject.CARD_PATH}?v={version}", "type": kind}


class CardResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_install_persists_one_module(self):
        resources = StorageCollection()
        self.assertTrue(await subject.async_register_card_resource(hass_for(resources)))
        self.assertEqual(resources.disk, [{"id": "created", "url": subject.CARD_URL, "type": "module"}])

    async def test_existing_manual_resource_loaded_before_update(self):
        resources = StorageCollection([card()])
        await subject.async_register_card_resource(hass_for(resources))
        self.assertEqual(resources.writes, ["update"])
        self.assertEqual(resources.disk[0]["id"], "manual")
        self.assertEqual(resources.disk[0]["url"], subject.CARD_URL)

    async def test_restart_and_repeat_are_idempotent(self):
        disk = []
        first = StorageCollection(disk)
        hass = hass_for(first)
        await subject.async_register_card_resource(hass)
        await subject.async_register_card_resource(hass)
        second = StorageCollection(disk)
        await subject.async_register_card_resource(hass_for(second))
        self.assertEqual(first.writes, ["create"])
        self.assertEqual(second.writes, [])
        self.assertEqual(len(disk), 1)

    async def test_upgrade_changes_url_without_changing_id(self):
        resources = StorageCollection([card()])
        with patch.object(subject, "CARD_URL", f"{subject.CARD_PATH}?v=next"):
            await subject.async_register_card_resource(hass_for(resources))
        self.assertEqual(resources.disk, [card(version="next")])

    async def test_concurrent_calls_create_only_once(self):
        resources = StorageCollection()
        hass = hass_for(resources)
        await asyncio.gather(*(subject.async_register_card_resource(hass) for _ in range(5)))
        self.assertEqual(resources.writes, ["create"])

    async def test_duplicates_removed_and_wrong_type_corrected(self):
        resources = StorageCollection([card(kind="js"), card("duplicate", "older")])
        await subject.async_register_card_resource(hass_for(resources))
        self.assertEqual(resources.writes, ["update", "delete"])
        self.assertEqual(resources.disk, [card(version=subject.VERSION)])

    async def test_other_resources_and_other_origins_untouched(self):
        others = [
            {"id": "local", "url": "/local/another-card.js", "type": "module"},
            {"id": "external", "url": "https://example.invalid" + subject.CARD_PATH, "type": "module"},
            {"id": "relative", "url": "//example.invalid" + subject.CARD_PATH, "type": "module"},
            {"id": "invalid", "url": "http://[", "type": "module"},
        ]
        resources = StorageCollection(deepcopy(others) + [card()])
        await subject.async_register_card_resource(hass_for(resources))
        self.assertEqual(resources.disk[:4], others)
        self.assertEqual(resources.writes, ["update"])

    async def test_yaml_left_unchanged(self):
        yaml = SimpleNamespace(data=[card()])
        before = deepcopy(yaml.data)
        self.assertFalse(await subject.async_register_card_resource(hass_for(yaml)))
        self.assertEqual(yaml.data, before)

    async def test_failure_is_nonfatal_and_does_not_log_details(self):
        class BrokenStorage(StorageCollection):
            async def async_update_item(self, item_id, data):
                raise OSError("secret-storage-content")
        resources = BrokenStorage([card(), card("duplicate")])
        with self.assertLogs(subject._LOGGER, level="WARNING") as logs:
            self.assertFalse(await subject.async_register_card_resource(hass_for(resources)))
        self.assertIn("OSError", str(logs.output))
        self.assertNotIn("secret-storage-content", str(logs.output))
        self.assertEqual(resources.writes, [])
        self.assertEqual(len(resources.disk), 2)

    async def test_cancelled_setup_is_not_swallowed(self):
        class CancelledStorage(StorageCollection):
            async def async_get_info(self):
                raise asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await subject.async_register_card_resource(hass_for(CancelledStorage()))

    async def test_legacy_lovelace_container(self):
        resources = StorageCollection([card()])
        hass = SimpleNamespace(data={"lovelace": {"resources": resources}})
        self.assertTrue(await subject.async_register_card_resource(hass))
        self.assertEqual(resources.writes, ["update"])


if __name__ == "__main__":
    unittest.main()
