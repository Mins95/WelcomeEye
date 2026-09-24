"""Install the derivative through HA's actual requirement installation function."""
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import sys

from homeassistant.util.package import install_package, is_installed
from homeassistant.requirements import pip_kwargs

wheel = next(Path('/audit/out').glob('aiortc-*.whl'))
manifest = Path('/audit/custom_components/welcomeeye_local/manifest.json')
requirement = (json.loads(manifest.read_text())['requirements'][0]
               if '--published' in sys.argv else 'aiortc @ ' + wheel.as_uri())
assert not is_installed(requirement), 'HA must ask the package manager to verify URL requirements'
assert install_package(requirement, **pip_kwargs('/config')), 'HA requirement installation failed'
assert importlib.metadata.version('aiortc') == '1.15.0+welcomeeye.crc1'
assert importlib.metadata.version('crc32c') == '2.9.post0'
print('HA package manager installed the derivative and native CRC dependency')
if '--published' in sys.argv:
    spec = importlib.util.spec_from_file_location(
        'crc_diagnostics', manifest.parent / 'crc32c_diagnostics.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    diagnostics = module.get_crc32c_diagnostics()
    assert diagnostics['crc32c_package'] == 'crc32c', diagnostics
    assert diagnostics['crc32c_backend'] == 'c', diagnostics
    assert diagnostics['crc32c_native_available'], diagnostics
    assert diagnostics['reference_checksum_ok'], diagnostics
    print(json.dumps(diagnostics, sort_keys=True))
