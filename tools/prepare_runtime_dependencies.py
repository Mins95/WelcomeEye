"""Install manifest dependencies before starting the actual HA test interpreter.

Replacing native packages in an interpreter that has already imported HA can
leave old extension objects alive. Preparation and assertions use separate
processes; an installation failure remains a failing validation.
"""
import asyncio
import argparse
from importlib.util import find_spec
import json
from pathlib import Path
import tempfile

from homeassistant.requirements import pip_kwargs
from homeassistant.util.package import install_package


async def prepare(*, homekit=False):
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'custom_components/welcomeeye_local/manifest.json').read_text())
    requirements = list(manifest['requirements'])
    if homekit:
        # Read installed HA's official manifests without importing HomeKit
        # before its optional dependencies have been prepared.
        ha_root = Path(find_spec('homeassistant').origin).parent
        for domain in ('ffmpeg', 'homekit'):
            declared = json.loads((ha_root / 'components' / domain / 'manifest.json').read_text())
            requirements.extend(declared.get('requirements', ()))
    with tempfile.TemporaryDirectory() as configuration:
        for requirement in dict.fromkeys(requirements):
            if not await asyncio.to_thread(install_package, requirement, **pip_kwargs(configuration)):
                raise RuntimeError('Could not prepare the declared test dependency')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--homekit', action='store_true',
        help='Prepare the installed HA version\'s official HomeKit/FFmpeg requirements')
    asyncio.run(prepare(homekit=parser.parse_args().homekit))
