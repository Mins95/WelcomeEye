"""HA boundary stubs only; protocol, PyAV and aiortc remain real."""
import importlib
import sys
import types
from contextlib import contextmanager

from test_control_coordination import PACKAGE


@contextmanager
def ha_modules(overrides):
    """Restore only HA stubs, not newly imported C-extension dependencies.

    patch.dict(sys.modules) would unload cryptography Python classes while its
    Rust extension retains them, causing mismatched EC types on the next import.
    """
    missing = object()
    previous = {key: sys.modules.get(key, missing) for key in overrides}
    sys.modules.update(overrides)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is missing:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


def load_hub():
    helpers = types.ModuleType('homeassistant.helpers')
    registry = types.ModuleType('homeassistant.helpers.device_registry')
    helpers.device_registry = registry
    with ha_modules({
        'homeassistant': types.ModuleType('homeassistant'),
        'homeassistant.helpers': helpers,
        'homeassistant.helpers.device_registry': registry,
    }):
        return importlib.import_module(PACKAGE + '.hub')


def load_rtc():
    camera = types.ModuleType('homeassistant.components.camera.webrtc')
    camera.WebRTCAnswer = lambda **kw: types.SimpleNamespace(**kw)
    camera.WebRTCError = lambda **kw: types.SimpleNamespace(**kw)
    web = types.ModuleType('homeassistant.components.web_rtc')
    web.async_get_ice_servers = lambda hass: hass.ice_servers
    with ha_modules({
        'homeassistant.components.camera.webrtc': camera,
        'homeassistant.components.web_rtc': web,
    }):
        return importlib.import_module(PACKAGE + '.rtc')


def entry(model='WelcomeEye Connect 2'):
    return types.SimpleNamespace(unique_id='TESTUID000', title=model,
        domain='welcomeeye_local', data={'host': 'unused', 'username': 'unused',
        'password': 'test-code', 'detected_model': model})
