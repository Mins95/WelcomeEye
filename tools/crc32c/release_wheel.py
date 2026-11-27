"""Verify the dependency asset against the production manifest before CI or release."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit


def verify(manifest, directory):
    requirements = manifest['requirements']
    requirement, = (item for item in requirements if item.startswith('aiortc@'))
    url = urlsplit(requirement.split('@', 1)[1])
    filename = unquote(url.path.rsplit('/', 1)[-1])
    if Path(filename).name != filename or not filename.endswith('-py3-none-any.whl'):
        raise ValueError('Invalid aiortc release asset name')
    wheel = directory / filename
    digest = sha256(wheel.read_bytes()).hexdigest()
    if url.fragment != f'sha256={digest}':
        raise ValueError('Rebuilt aiortc wheel does not match the production manifest')
    return requirement, filename, digest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--runtime-manifest', type=Path,
                        help='Write a test manifest targeting the same bytes mounted at /audit/out')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / 'custom_components/welcomeeye_local/manifest.json').read_text())
    remote, name, digest = verify(manifest, args.directory)
    if args.runtime_manifest:
        i = manifest['requirements'].index(remote)
        manifest['requirements'][i] = f'aiortc @ file:///audit/out/{quote(name)}#sha256={digest}'
        args.runtime_manifest.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f'{digest}  {name}')
