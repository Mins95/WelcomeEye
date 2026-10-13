"""Install manifest dependencies before starting the actual HA test interpreter.

Replacing native packages in an interpreter that has already imported HA can
leave old extension objects alive. Preparation and assertions use separate
processes; an installation failure remains a failing validation.
"""
import asyncio
import json
from pathlib import Path
import tempfile

from homeassistant.requirements import pip_kwargs
from homeassistant.util.package import install_package


async def prepare():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'custom_components/welcomeeye_local/manifest.json').read_text())
    with tempfile.TemporaryDirectory() as configuration:
        for requirement in manifest['requirements']:
            if not await asyncio.to_thread(install_package, requirement, **pip_kwargs(configuration)):
                raise RuntimeError('Could not prepare the declared test dependency')


if __name__ == '__main__':
    asyncio.run(prepare())
