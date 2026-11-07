"""Real HA private-storage restart/cleanup regression, with no network traffic."""
import asyncio
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from homeassistant import core as ha_core
from homeassistant.core import HomeAssistant


async def main(root):
    # Load the unmodified production modules without starting the integration,
    # its platform imports or its media/cloud workers.
    package = ModuleType('welcomeeye_discovery_runtime_fixture')
    package.__path__ = [str(root / 'custom_components/welcomeeye_local')]
    sys.modules[package.__name__] = package
    storage = importlib.import_module(package.__name__ + '.discovery_store')
    client = importlib.import_module(package.__name__ + '.client')
    protocol = importlib.import_module(package.__name__ + '.protected')
    entry = SimpleNamespace(entry_id='synthetic-discovery', unique_id='SYNTHETIC_DEVICE',
        data={'host': '192.0.2.1', 'username': 'SYNTHETIC_USER',
              'password': 'SYNTHETIC_PASSWORD', 'protocol_family': 'legacy_owsp',
              'device_variant': 'connect2_r001'})
    info = protocol.DiscoveryInfo(entry.unique_id, 12345, 12346, entry.data['host'], True)
    original_hass = ha_core._hass.hass
    hass = None
    try:
        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            manager = storage.DiscoveryStore(hass, entry)
            with patch.object(client.socket, 'socket', side_effect=AssertionError('Network forbidden')):
                await manager.start()
                assert not client._DISCOVERY_HINTS
                # Simulate only the post-authentication observation callback.
                # The offline Session tests exercise the login itself.
                client._authenticated_discovery(entry.data['host'], info, manager._binding)
                await asyncio.sleep(0)
                await manager.close()
            assert not client._DISCOVERY_BINDINGS
            files = list((Path(directory) / '.storage').glob('welcomeeye_local.discovery_endpoint.*'))
            assert len(files) == 1, files
            saved = await hass.async_add_executor_job(files[0].read_text)
            assert 'SYNTHETIC_PASSWORD' not in saved and 'SYNTHETIC_USER' not in saved
            json.loads(saved)
            await hass.async_stop(force=True)

            # A second HA instance has a fresh Store cache and no in-memory
            # endpoint observation, as after a real restart.
            client._DISCOVERY_CACHE.clear()
            client._DISCOVERY_HINTS.clear()
            hass = HomeAssistant(directory)
            manager = storage.DiscoveryStore(hass, entry)
            with patch.object(client.socket, 'socket', side_effect=AssertionError('Network forbidden')):
                await manager.start()
                diagnostic = {}
                restored = client.discover(entry.data['host'], diagnostics=diagnostic)
                assert restored == info
                assert diagnostic['endpoint_source'] == 'persisted_hint', diagnostic
                await manager.close()

            # An edited password must not reuse the previous approval/hint.
            changed = deepcopy(entry)
            changed.data['password'] = 'NEW_SYNTHETIC_PASSWORD'
            client._DISCOVERY_CACHE.clear()
            manager = storage.DiscoveryStore(hass, changed)
            with patch.object(client.socket, 'socket', side_effect=AssertionError('Network forbidden')):
                await manager.start()
                assert not client._DISCOVERY_HINTS
                await manager.close()
            await storage.async_remove_discovery_store(hass, entry.entry_id)
            assert not files[0].exists()
            assert not client._DISCOVERY_BINDINGS
            await hass.async_stop(force=True)
            hass = None
    finally:
        if hass is not None:
            await hass.async_stop(force=True)
        ha_core._hass.hass = original_hass
    print('Actual HA discovery storage: authenticated save, restart, private credentials, changed settings and removal PASS')


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(30):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
