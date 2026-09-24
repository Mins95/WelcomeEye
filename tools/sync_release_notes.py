"""Prepare a notes-only update for an unchanged, published beta package."""
import argparse
import json
from pathlib import Path
import re
import sys

from build_release import read_version, validate_archive


def validate_existing_release(release, version):
    """Reject every target outside the exact published beta already selected."""
    if re.fullmatch(r'0\.4\.2-beta\.[1-9][0-9]*', version) is None:
        raise ValueError('Notes sync only supports 0.4.2 beta prereleases')
    if release.get('tag_name') != f'v{version}':
        raise ValueError('Existing release tag does not match the package version')
    if (release.get('draft') is not False or release.get('prerelease') is not True
            or not release.get('published_at')):
        raise ValueError('Notes sync requires an already published, non-draft prerelease')
    if type(release.get('id')) is not int or release['id'] <= 0:
        raise ValueError('Existing release has no valid release ID')


def replace_generated_notes(body, changelog, version):
    """Retain the original validation evidence verbatim, including its SHA."""
    if not isinstance(body, str) or not body.startswith(f'## v{version}\n'):
        raise ValueError('Existing notes do not have the known generated version heading')
    markers = list(re.finditer(r'^### Validation evidence\r?$', body, re.M))
    if len(markers) != 1:
        raise ValueError('Existing notes must have exactly one Validation evidence suffix')
    sections = list(re.finditer(
        r'^## ' + re.escape(version) + r'(?:[ \t][^\n]*)?\n(.*?)(?=^## |\Z)',
        changelog, re.M | re.S,
    ))
    if len(sections) != 1 or not sections[0].group(1).strip():
        raise ValueError('Changelog must have exactly one nonempty exact-version section')
    generated = sections[0].group(1).strip()
    if re.search(r'^### Validation evidence\s*$', generated, re.M):
        raise ValueError('Changelog may not replace the reserved validation evidence suffix')
    return f'## v{version}\n\n{generated}\n\n' + body[markers[0].start():]


def prepare_notes_update(root, release, archive, published_archive, tag):
    """Only identical bytes permit documentation to amend existing notes."""
    version = read_version(root)
    if tag != f'v{version}':
        raise ValueError('Selected tag does not match the current package version')
    validate_existing_release(release, version)
    digest = validate_archive(archive, version)
    validate_archive(published_archive, version)
    if archive.read_bytes() != published_archive.read_bytes():
        raise ValueError('Package differs from the published asset; select a new beta version')
    assets = [asset for asset in release.get('assets', [])
              if asset.get('name') == 'welcomeeye_local.zip']
    if (len(assets) != 1 or assets[0].get('state') != 'uploaded'
            or assets[0].get('size') != published_archive.stat().st_size):
        raise ValueError('Published ZIP asset metadata is missing or inconsistent')
    if assets[0].get('digest') not in (None, f'sha256:{digest}'):
        raise ValueError('Published asset digest does not match the downloaded ZIP')
    body = replace_generated_notes(
        release.get('body'), (root / 'CHANGELOG.md').read_text(encoding='utf-8'), version,
    )
    return None if body == release['body'] else {'body': body}


def verify_notes_update(previous, updated, payload):
    """Verify the response changed only the intended text, never release state."""
    version = previous['tag_name'].removeprefix('v')
    validate_existing_release(updated, version)
    for key in ('id', 'tag_name', 'target_commitish', 'name', 'draft', 'prerelease', 'published_at'):
        if updated.get(key) != previous.get(key):
            raise ValueError(f'Release metadata unexpectedly changed: {key}')
    def assets(release):
        return sorted((item['id'], item['name'], item['size'], item.get('digest'))
                      for item in release.get('assets', []))
    if assets(previous) != assets(updated):
        raise ValueError('Release assets unexpectedly changed during notes sync')
    if updated.get('body') != payload['body']:
        raise ValueError('Release body does not match the verified notes update')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--published-archive', type=Path)
    parser.add_argument('--tag')
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--verify-response', type=Path)
    args = parser.parse_args()
    try:
        release = json.loads(args.release.read_text(encoding='utf-8'))
        if args.verify_response is not None:
            verify_notes_update(
                release, json.loads(args.verify_response.read_text(encoding='utf-8')),
                json.loads(args.payload.read_text(encoding='utf-8')),
            )
            print('Prerelease notes synchronized; tag, assets and validation evidence preserved.')
            return
        if args.archive is None or args.published_archive is None or args.tag is None:
            parser.error('Preparing notes requires --archive, --published-archive and --tag')
        payload = prepare_notes_update(
            args.root, release, args.archive, args.published_archive, args.tag,
        )
        if payload is None:
            print('Prerelease notes already match; no update needed.', file=sys.stderr)
            return
        args.payload.write_text(json.dumps(payload, ensure_ascii=False) + '\n', encoding='utf-8')
        # The workflow uses only this validated integer as the PATCH endpoint.
        print(release['id'])
    except (KeyError, OSError, ValueError) as exc:
        raise SystemExit(f'Release notes sync refused: {exc}') from exc


if __name__ == '__main__':
    main()
