"""Verify the one explicitly authorized replacement of the published beta.4."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re

from build_release import validate_archive
from sync_release_notes import validate_existing_release

REPOSITORY = 'Mins95/WelcomeEye'
VERSION = '0.4.2-beta.4'
TAG = f'v{VERSION}'
OLD_TAG_SHA = 'ef599f64e4917c293deed38d1a2c16022c3e751b'
OLD_ZIP_SHA256 = '4344443d21d5a9d4b5894c12ff956b8935ed96134088d0047c21af89775e0de2'
ZIP_NAME = 'welcomeeye_local.zip'
CHECKSUM_NAME = f'{ZIP_NAME}.sha256'


def validate_plan(plan, repository):
    """A plan cannot extend this authorization to another release or old state."""
    fixed = {'schema': 1, 'repository': REPOSITORY, 'tag': TAG,
             'old_tag_sha': OLD_TAG_SHA, 'old_zip_sha256': OLD_ZIP_SHA256}
    if (repository != REPOSITORY or set(plan) != set(fixed) | {'new_zip_sha256'}
            or type(plan.get('schema')) is not int
            or any(plan.get(key) != value for key, value in fixed.items())):
        raise ValueError('Plan does not match the sole authorized beta.4 replacement')
    digest = plan['new_zip_sha256']
    if not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None:
        raise ValueError('Plan must pin the final new ZIP SHA256 before publication')
    if digest == OLD_ZIP_SHA256:
        raise ValueError('An identical package does not authorize a tag replacement')
    return digest


def validate_candidate(candidate_sha):
    if (not isinstance(candidate_sha, str) or re.fullmatch('[0-9a-f]{40}', candidate_sha) is None
            or candidate_sha == OLD_TAG_SHA):
        raise ValueError('Replacement requires a new full candidate commit SHA')


def validate_zip_files(archive, checksum, expected_digest):
    if validate_archive(archive, VERSION) != expected_digest:
        raise ValueError('Downloaded ZIP differs from the pinned digest')
    if checksum.read_bytes() != f'{expected_digest}  {ZIP_NAME}\n'.encode('ascii'):
        raise ValueError('Downloaded checksum does not match the pinned ZIP')


def validate_assets(release, archive, checksum, expected_digest):
    """Check both downloaded assets and their API metadata before any write."""
    validate_existing_release(release, VERSION)
    validate_zip_files(archive, checksum, expected_digest)
    assets = release.get('assets', [])
    if (len(assets) != 2 or {asset.get('name') for asset in assets} != {ZIP_NAME, CHECKSUM_NAME}
            or len({asset.get('id') for asset in assets}) != 2):
        raise ValueError('Replacement requires exactly the ZIP and checksum assets')
    for asset in assets:
        path = archive if asset['name'] == ZIP_NAME else checksum
        digest = sha256(path.read_bytes()).hexdigest()
        if (type(asset.get('id')) is not int or asset['id'] <= 0
                or asset.get('state') != 'uploaded' or asset.get('size') != path.stat().st_size
                or asset.get('digest') not in (None, f'sha256:{digest}')):
            raise ValueError('Published asset metadata differs from the downloaded bytes')


def prepare_replacement(*, plan, repository, release, archive, published_archive,
                        published_checksum, tag_sha, candidate_sha, notes):
    digest = validate_plan(plan, repository)
    validate_candidate(candidate_sha)
    if tag_sha != OLD_TAG_SHA:
        raise ValueError('Old tag has changed; replacement authorization cannot be replayed')
    if validate_archive(archive, VERSION) != digest:
        raise ValueError('Built ZIP differs from the new digest pinned in the plan')
    validate_zip_files(archive, archive.with_suffix('.zip.sha256'), digest)
    validate_assets(release, published_archive, published_checksum, OLD_ZIP_SHA256)
    if (not notes.startswith(f'## {TAG}\n') or notes.count('### Validation evidence\n') != 1
            or '### Authorized beta.4 replacement' in notes):
        raise ValueError('Replacement notes must include the current generated validation evidence')
    body = (notes.rstrip() + '\n\n### Authorized beta.4 replacement\n\n'
            'The maintainer explicitly requested replacement of this existing prerelease '
            'on 2026-09-24.\n\n'
            f'- Previous tag commit: `{OLD_TAG_SHA}`.\n'
            f'- Replacement commit: `{candidate_sha}`.\n'
            f'- Previous ZIP SHA256: `{OLD_ZIP_SHA256}`.\n'
            f'- Replacement ZIP SHA256: `{digest}`.\n')
    return {'body': body}


def verify_replacement(*, plan, repository, previous, updated, payload, archive,
                       published_archive, published_checksum, tag_sha, candidate_sha):
    digest = validate_plan(plan, repository)
    validate_candidate(candidate_sha)
    if tag_sha != candidate_sha:
        raise ValueError('Replaced remote tag does not target the verified candidate')
    if validate_archive(archive, VERSION) != digest:
        raise ValueError('Built ZIP no longer matches the replacement plan')
    validate_zip_files(archive, archive.with_suffix('.zip.sha256'), digest)
    validate_assets(updated, published_archive, published_checksum, digest)
    for key in ('id', 'tag_name', 'target_commitish', 'name', 'draft', 'prerelease', 'published_at'):
        if updated.get(key) != previous.get(key):
            raise ValueError(f'Release metadata unexpectedly changed: {key}')
    if set(payload) != {'body'} or updated.get('body') != payload['body']:
        raise ValueError('Replacement notes differ from the verified body-only update')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--published-archive', type=Path, required=True)
    parser.add_argument('--published-checksum', type=Path, required=True)
    parser.add_argument('--tag-sha', required=True)
    parser.add_argument('--candidate-sha', required=True)
    parser.add_argument('--notes', type=Path)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--verify-response', type=Path)
    args = parser.parse_args()
    try:
        common = dict(plan=json.loads(args.plan.read_text(encoding='utf-8')),
                      repository=args.repository, archive=args.archive,
                      published_archive=args.published_archive,
                      published_checksum=args.published_checksum,
                      tag_sha=args.tag_sha, candidate_sha=args.candidate_sha)
        previous = json.loads(args.release.read_text(encoding='utf-8'))
        if args.verify_response:
            verify_replacement(
                **common, previous=previous,
                updated=json.loads(args.verify_response.read_text(encoding='utf-8')),
                payload=json.loads(args.payload.read_text(encoding='utf-8')),
            )
            print('Authorized beta.4 replacement verified: tag, ZIP, checksum, notes and release state.')
        else:
            if args.notes is None:
                parser.error('Preparing replacement requires --notes')
            payload = prepare_replacement(
                **common, release=previous, notes=args.notes.read_text(encoding='utf-8'),
            )
            args.payload.write_text(json.dumps(payload, ensure_ascii=False) + '\n', encoding='utf-8')
            print(previous['id'])
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise SystemExit(f'Beta.4 replacement refused: {exc}') from exc


if __name__ == '__main__':
    main()
