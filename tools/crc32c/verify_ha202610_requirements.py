"""Reproduce crc1's HA 2026.10 resolver failure, then install the local fix.

Run first in a clean HA 2026.10 container with the checkout at /audit and its
built derivative in /audit/out. Uses real HA installers and Core constraints.
Only package downloads and a temporary HomeAssistant fixture are involved;
the integration is never set up and no device or vendor connection is opened.
"""
import asyncio
from email.parser import BytesParser
from hashlib import sha256
import importlib.metadata
import inspect
import json
import logging
from pathlib import Path
import sys
import tempfile
from zipfile import ZipFile

from packaging.requirements import Requirement

from homeassistant.core import HomeAssistant
from homeassistant.requirements import async_process_requirements, pip_kwargs
from homeassistant.util.package import install_package


OLD_REQUIREMENT = (
    'aiortc@https://github.com/Mins95/WelcomeEye/releases/download/v0.4.2/'
    'aiortc-1.15.0%2Bwelcomeeye.crc1-py3-none-any.whl'
    '#sha256=75f7d14e598dfd2b3e97bd4b9342e9b675185b6a3d83d5a5083c43250b6eee9d'
)


class InstallLog(logging.Handler):
    """Observe the package helper's errors without altering its behavior."""

    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def distribution_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def local_requirement(root, av_version):
    wheels = list((root / 'out').glob('aiortc-*.whl'))
    assert len(wheels) == 1, f'Expected one newly built aiortc wheel, found {len(wheels)}'
    wheel = wheels[0].resolve()
    with ZipFile(wheel) as archive:
        metadata_names = [name for name in archive.namelist()
                          if name.endswith('.dist-info/METADATA')]
        assert len(metadata_names) == 1, metadata_names
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
    assert metadata['Name'] == 'aiortc', metadata['Name']
    version = metadata['Version']
    assert version and version != '1.15.0+welcomeeye.crc1', version
    dependencies = [Requirement(value) for value in metadata.get_all('Requires-Dist', [])]
    av_dependency = next(req for req in dependencies if req.name == 'av')
    assert av_version in av_dependency.specifier, str(av_dependency)
    assert any(req.name == 'crc32c' and str(req.specifier) == '==2.9.post0'
               for req in dependencies), 'The separate native CRC dependency must remain'
    digest = sha256(wheel.read_bytes()).hexdigest()
    return f'aiortc @ {wheel.as_uri()}#sha256={digest}', version, digest


async def main(root):
    core_version = distribution_version('homeassistant')
    assert core_version and core_version.startswith('2026.10.'), core_version
    av_before = distribution_version('av')
    assert av_before and av_before.startswith('19.'), av_before
    aiortc_before = distribution_version('aiortc')
    assert not aiortc_before or '+welcomeeye.' not in aiortc_before, (
        'Run this reproduction before installing either derivative', aiortc_before)
    assert 'aiortc' not in sys.modules, 'Avoid testing an already imported SCTP module'
    requirement, expected_version, digest = local_requirement(root, av_before)
    manifest = json.loads((root / 'custom_components/welcomeeye_local/manifest.json').read_text())
    assert sum(Requirement(req).name == 'aiortc' for req in manifest['requirements']) == 1
    requirements = [requirement if Requirement(req).name == 'aiortc' else req
                    for req in manifest['requirements']]

    capture = InstallLog()
    package_logger = logging.getLogger('homeassistant.util.package')
    package_logger.addHandler(capture)
    try:
        with tempfile.TemporaryDirectory(prefix='welcomeeye-old-requirement-') as config:
            old_installed = await asyncio.to_thread(
                install_package, OLD_REQUIREMENT, **pip_kwargs(config))
    finally:
        package_logger.removeHandler(capture)
    old_logs = '\n'.join(capture.messages)
    print(old_logs, flush=True)
    assert not old_installed, 'The crc1 requirement unexpectedly installed under Core constraints'
    assert distribution_version('av') == av_before, 'The failed install changed Core PyAV'
    assert distribution_version('aiortc') == aiortc_before, 'The failed install changed aiortc'
    # A download/network failure must not be mistaken for this resolver regression.
    assert 'av' in old_logs and '19.0' in old_logs and '<18' in old_logs, old_logs
    assert any(marker in old_logs.lower() for marker in (
        'no solution found', 'unsatisfiable', 'conflicting dependencies')), old_logs

    with tempfile.TemporaryDirectory(prefix='welcomeeye-fixed-requirements-') as config:
        hass = HomeAssistant(config)
        try:
            assert not hass.config.skip_pip_packages, 'Do not bypass declared requirements'
            await async_process_requirements(
                hass, 'welcomeeye_local', requirements, is_built_in=False)
            assert distribution_version('aiortc') == expected_version
            assert distribution_version('crc32c') == '2.9.post0'
            assert distribution_version('av') == av_before, 'The fix changed Core PyAV'
            import crc32c
            from aiortc.rtcsctptransport import crc32c as sctp_crc
            assert sctp_crc is crc32c.crc32c and inspect.isbuiltin(sctp_crc)
            assert sctp_crc(b'123456789') == 0xE3069283
        finally:
            await hass.async_stop(force=True)

    print(json.dumps({
        'homeassistant': core_version,
        'old_requirement_failed': True,
        'old_failure': 'Core PyAV 19 conflicts with crc1 av<18',
        'av_before': av_before,
        'av_after': distribution_version('av'),
        'new_aiortc': distribution_version('aiortc'),
        'new_wheel_sha256': digest,
        'full_ha_requirements_succeeded': True,
        'crc32c': distribution_version('crc32c'),
        'native_crc_verified': True,
    }, indent=2), flush=True)


if __name__ == '__main__':
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/audit')))
