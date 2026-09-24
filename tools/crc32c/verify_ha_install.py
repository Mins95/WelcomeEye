"""Install the derivative through HA's actual requirement installation function."""
import importlib.metadata
from pathlib import Path

from homeassistant.util.package import install_package, is_installed

wheel = next(Path('/audit/out').glob('aiortc-*.whl'))
requirement = 'aiortc @ ' + wheel.as_uri()
assert not is_installed(requirement), 'HA must ask the package manager to verify URL requirements'
assert install_package(requirement), 'HA requirement installation failed'
assert importlib.metadata.version('aiortc') == '1.15.0+welcomeeye.crc1'
assert importlib.metadata.version('crc32c') == '2.9.post0'
print('HA package manager installed the derivative and native CRC dependency')
