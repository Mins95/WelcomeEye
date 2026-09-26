"""Import dependency-free production modules without HA's package initializer."""
import importlib
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1] / 'custom_components/welcomeeye_local'
PACKAGE = 'welcomeeye_offline_fixture'
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = package


def load(name):
    return importlib.import_module(f'{PACKAGE}.{name}')


cap = load('capabilities')
CAP_IMPORTS = {name: getattr(cap, name) for name in (
    'DeviceVariant', 'ProtocolFamily', 'MATRIX', 'variant_for', 'family_for',
)}
