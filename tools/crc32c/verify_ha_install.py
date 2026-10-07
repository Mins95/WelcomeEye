"""Install the derivative through HA's actual constrained requirement installer."""
from email import message_from_bytes
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile


def select_requirement(audit, *, published=False):
    """Published verification needs the manifest, not a local build directory."""
    if published:
        manifest = audit / 'custom_components/welcomeeye_local/manifest.json'
        requirement = next(req for req in json.loads(manifest.read_text())['requirements']
                           if re.match(r'^aiortc\s*@', req))
        filename = unquote(Path(urlsplit(requirement.split('@', 1)[1].strip()).path).name)
        match = re.fullmatch(r'aiortc-(.+)-py3-none-any\.whl', filename)
        if match is None:
            raise ValueError('Published requirement is not the expected aiortc wheel')
        return requirement, match.group(1)
    wheels = list((audit / 'out').glob('aiortc-*.whl'))
    if len(wheels) != 1:
        raise ValueError('Expected exactly one local aiortc derivative wheel')
    wheel = wheels[0]
    with ZipFile(wheel) as bundle:
        metadata_files = [name for name in bundle.namelist()
                          if re.fullmatch(r'aiortc-.+\.dist-info/METADATA', name)]
        if len(metadata_files) != 1:
            raise ValueError('Expected one aiortc METADATA file')
        metadata = message_from_bytes(bundle.read(metadata_files[0]))
        if metadata['Name'] != 'aiortc' or not metadata['Version']:
            raise ValueError('Invalid local aiortc metadata')
    return 'aiortc @ ' + wheel.as_uri(), metadata['Version']


def constrained_av_version(constraints):
    match = re.search(r'^av==([^\s]+)$', constraints.read_text(), re.M)
    if match is None:
        raise ValueError('HA must provide an exact PyAV constraint')
    return match.group(1)


def main():
    import homeassistant
    from homeassistant.util.package import install_package, is_installed
    from homeassistant.requirements import pip_kwargs

    audit = Path('/audit')
    requirement, expected_version = select_requirement(audit, published='--published' in sys.argv)
    expected_av = constrained_av_version(Path(homeassistant.__file__).parent / 'package_constraints.txt')
    initial_av = importlib.metadata.version('av')
    assert initial_av == expected_av, (initial_av, expected_av)
    assert not is_installed(requirement), 'HA must ask the package manager to verify URL requirements'
    assert install_package(requirement, **pip_kwargs('/config')), 'HA requirement installation failed'
    assert importlib.metadata.version('aiortc') == expected_version
    assert importlib.metadata.version('crc32c') == '2.9.post0'
    assert importlib.metadata.version('av') == initial_av, 'The derivative must preserve HA PyAV'

    manifest = audit / 'custom_components/welcomeeye_local/manifest.json'
    spec = importlib.util.spec_from_file_location(
        'crc_diagnostics', manifest.parent / 'crc32c_diagnostics.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    diagnostics = module.get_crc32c_diagnostics()
    assert diagnostics['crc32c_package'] == 'crc32c', diagnostics
    assert diagnostics['crc32c_backend'] == 'c', diagnostics
    assert diagnostics['crc32c_native_available'], diagnostics
    assert diagnostics['reference_checksum_ok'], diagnostics
    print(json.dumps({'aiortc': expected_version, 'preserved_ha_av': initial_av,
                      'crc_diagnostics': diagnostics}, sort_keys=True))


if __name__ == '__main__':
    main()
