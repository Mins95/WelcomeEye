"""Execute production HA setup/unload functions with HA boundary substitutes."""
import ast
import asyncio
from pathlib import Path
import threading
import types
from unittest.mock import patch

import pytest

from beta8_helpers import entry, load_hub


def functions(namespace):
    path = Path(__file__).parents[1] / 'custom_components/welcomeeye_local/__init__.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    selected = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)]
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['async_setup_entry'], namespace['async_unload_entry']


@pytest.mark.parametrize('fail_stage', [None, 'start', 'platforms'])
def test_repeated_setup_unload_and_partial_start_failure(fail_stage):
    async def run():
        module = load_hub()
        servers, hubs, preload_threads = [], [], []
        class Server:
            closed = False
            sockets = [types.SimpleNamespace(getsockname=lambda: ('127.0.0.1', 1))]
            def close(self): self.closed = True
            async def wait_closed(self): pass
        async def start_server(*args, **kwargs):
            server = Server()
            servers.append(server)
            return server
        def factory(hass, config):
            hub = module.WelcomeEyeHub(hass, config)
            def ring_start():
                if fail_stage == 'start': raise RuntimeError('start failed')
            hub.ring_listener.start = ring_start
            hubs.append(hub)
            return hub
        async def forward(*args):
            if fail_stage == 'platforms': raise RuntimeError('platform failed')
        async def unload(*args): return True
        hass = types.SimpleNamespace(async_add_executor_job=asyncio.to_thread,
            config_entries=types.SimpleNamespace(async_forward_entry_setups=forward,
                async_unload_platforms=unload),
            bus=types.SimpleNamespace(async_listen_once=lambda *args: lambda: None))
        setup, unload_entry = functions(dict(HomeAssistant=object, ConfigEntry=object,
            _preload_dns_types=lambda: preload_threads.append(threading.get_ident()),
            WelcomeEyeHub=factory, AuthenticationError=module.AuthenticationError,
            ConfigEntryAuthFailed=RuntimeError, ConfigEntryNotReady=RuntimeError,
            PLATFORMS=[], EVENT_HOMEASSISTANT_STOP='stop'))
        baseline = asyncio.all_tasks()
        with patch.object(module.asyncio, 'start_server', start_server):
            for _ in range(5):
                config = entry()
                config.async_on_unload = lambda callback: None
                if fail_stage:
                    with pytest.raises(RuntimeError): await setup(hass, config)
                else:
                    assert await setup(hass, config)
                    assert await unload_entry(hass, config)
                hub = hubs[-1]
                assert hub.stopped and hub.thread is None and hub.session is None
                assert not hub.consumers and not hub.handlers
                assert servers[-1].closed
        await asyncio.sleep(0)
        assert asyncio.all_tasks() == baseline
        assert len(preload_threads) == 5
        assert all(t != threading.get_ident() for t in preload_threads)
    asyncio.run(run())
