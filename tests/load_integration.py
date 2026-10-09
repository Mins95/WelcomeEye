"""Import dependency-free production modules without HA's package initializer."""
import importlib
import importlib.util
import ast
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1] / 'custom_components/welcomeeye_local'
PACKAGE = 'welcomeeye_offline_fixture'
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = package

# Manual capture is shared with QV cameras. Offline CI needs only HA's exception
# boundary, not its full dependency tree; execute the unmodified capture logic.
if importlib.util.find_spec('homeassistant') is None and f'{PACKAGE}.manual_snapshot' not in sys.modules:
    capture = types.ModuleType(f'{PACKAGE}.manual_snapshot')
    capture.__package__ = PACKAGE
    capture.__file__ = str(ROOT / 'manual_snapshot.py')
    capture.HomeAssistantError = type('HomeAssistantError', (Exception,), {})
    tree = ast.parse((ROOT / 'manual_snapshot.py').read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and node.module == 'homeassistant.exceptions')]
    sys.modules[capture.__name__] = capture
    exec(compile(tree, capture.__file__, 'exec'), capture.__dict__)


def load(name):
    return importlib.import_module(f'{PACKAGE}.{name}')


cap = load('capabilities')
CAP_IMPORTS = {name: getattr(cap, name) for name in (
    'DeviceVariant', 'ProtocolFamily', 'MATRIX', 'variant_for', 'family_for',
)}
