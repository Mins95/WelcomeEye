"""Build a reproducible, verified HACS archive without importing the integration."""
import argparse
import ast
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

DOMAIN = 'welcomeeye_local'
REQUIRED_FILES = {'manifest.json', '__init__.py', 'const.py', 'camera.py',
                  'frontend/welcomeeye-card.js', 'translations/en.json', 'translations/fr.json'}
SOURCE_EXTENSIONS = {'.py', '.json', '.js', '.yaml', '.yml', '.png', '.jpg', '.svg', '.md'}
TEXT_EXTENSIONS = SOURCE_EXTENSIONS - {'.png', '.jpg'}


def constant_version(source):
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'VERSION'
                                                for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('const.py has no literal VERSION')


def read_version(root):
    package = root / 'custom_components' / DOMAIN
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    version = manifest['version']
    if manifest.get('domain') != DOMAIN or not isinstance(version, str):
        raise ValueError('Invalid integration manifest')
    if constant_version((package / 'const.py').read_text(encoding='utf-8')) != version:
        raise ValueError('manifest.json and const.py versions disagree')
    return version


def validate_archive(archive, version):
    with ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)) or not REQUIRED_FILES.issubset(names):
            raise ValueError('HACS ZIP has duplicate or missing integration files')
        for name in names:
            parts = name.split('/')
            if (name.startswith('/') or '\\' in name or ':' in parts[0] or '..' in parts or '__pycache__' in parts
                    or Path(name).suffix.lower() not in SOURCE_EXTENSIONS):
                raise ValueError('Unsafe or non-source file in HACS ZIP')
        manifest = json.loads(bundle.read('manifest.json'))
        if manifest.get('domain') != DOMAIN or manifest.get('version') != version:
            raise ValueError('HACS ZIP manifest version/domain mismatch')
        if constant_version(bundle.read('const.py').decode('utf-8')) != version:
            raise ValueError('HACS ZIP code version mismatch')
        if bundle.testzip() is not None:
            raise ValueError('HACS ZIP failed CRC verification')
    return sha256(Path(archive).read_bytes()).hexdigest()


def build_archive(root, output):
    version = read_version(root)
    package = root / 'custom_components' / DOMAIN
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = sorted(path for path in package.rglob('*') if path.is_file()
                   and '__pycache__' not in path.parts
                   and path.suffix.lower() in SOURCE_EXTENSIONS)
    if any(path.is_symlink() or not path.resolve().is_relative_to(package.resolve()) for path in paths):
        raise ValueError('Symlink outside package is not a release input')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.zip', delete=False) as handle:
            temporary = Path(handle.name)
        with ZipFile(temporary, 'w', compression=ZIP_DEFLATED, compresslevel=9) as bundle:
            for path in paths:
                entry = ZipInfo(path.relative_to(package).as_posix(), (1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                entry.compress_type = ZIP_DEFLATED
                data = path.read_bytes()
                if path.suffix.lower() in TEXT_EXTENSIONS:
                    data = data.replace(b'\r\n', b'\n')
                bundle.writestr(entry, data, compresslevel=9)
        digest = validate_archive(temporary, version)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    output.with_suffix(output.suffix + '.sha256').write_text(
        f'{digest}  {output.name}\n', encoding='ascii', newline='\n',
    )
    return {'version': version, 'sha256': digest, 'filename': output.name,
            'size': output.stat().st_size}


def validate_release_gate(*, version, ref, sha, main_sha, repository,
                          hardware_validated, evidence, validation_runs):
    """Beta-only gate; incomplete hardware checks must remain explicit in notes."""
    if re.fullmatch(r'0\.4\.2-beta\.[1-9][0-9]*', version) is None:
        raise ValueError('This workflow only publishes 0.4.2 beta prereleases')
    if ref != 'refs/heads/main' or sha != main_sha:
        raise ValueError('Release must target the current main commit')
    if type(hardware_validated) is not bool:
        raise ValueError('Hardware validation must be an explicit boolean')
    if hardware_validated:
        evidence_url = urlsplit(evidence.strip())
        if evidence_url.scheme != 'https' or not evidence_url.netloc:
            raise ValueError('Claimed hardware validation requires an HTTPS evidence link')
    passed = any(
        run.get('head_sha') == sha and run.get('head_branch') == 'main'
        and run.get('head_repository', {}).get('full_name') == repository
        and run.get('event') in ('push', 'workflow_dispatch')
        and run.get('status') == 'completed' and run.get('conclusion') == 'success'
        for run in validation_runs
    )
    if not passed:
        raise ValueError('Validate must succeed for this exact main commit before release')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, default=Path('dist/welcomeeye_local.zip'))
    parser.add_argument('--release-check', type=Path, help='Validate workflow runs JSON, using release environment')
    args = parser.parse_args()
    if args.release_check is not None:
        validate_release_gate(
            version=read_version(args.root), ref=os.environ['GITHUB_REF'],
            sha=os.environ.get('CANDIDATE_SHA', os.environ['GITHUB_SHA']),
            main_sha=os.environ['MAIN_SHA'], repository=os.environ['GITHUB_REPOSITORY'],
            hardware_validated=os.environ.get('HARDWARE_VALIDATED') == 'true',
            evidence=os.environ.get('HARDWARE_EVIDENCE', ''),
            validation_runs=json.loads(args.release_check.read_text(encoding='utf-8'))['workflow_runs'],
        )
        print('Beta release gates passed; unverified hardware status must be disclosed in notes.')
    else:
        print(json.dumps(build_archive(args.root, args.output), sort_keys=True))


if __name__ == '__main__':
    main()
