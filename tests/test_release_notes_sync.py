"""Notes-only beta updates never change artifacts or historical evidence."""
from copy import deepcopy
import importlib.util
import sys
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import test_release_packaging as packaging

ROOT, builder = packaging.ROOT, packaging.release

spec = importlib.util.spec_from_file_location('notes_sync_under_test', ROOT / 'tools/sync_release_notes.py')
notes = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, build_release=builder):
    spec.loader.exec_module(notes)


class ReleaseNotesSyncTests(unittest.TestCase):
    def setUp(self):
        fixture = packaging.PackagingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.package, self.archive = fixture.root, fixture.package, fixture.output
        self.version = fixture.version
        result = builder.build_archive(self.root, self.archive)
        self.published = self.root / 'published.zip'
        self.published.write_bytes(self.archive.read_bytes())
        self.evidence = ('### Validation evidence\n\n'
                         '- Hardware still pending for original commit `original-sha`.\n'
                         '- Existing maintainer evidence: https://example.org/report\n')
        self.release = {
            'id': 42, 'tag_name': f'v{self.version}', 'name': f'v{self.version}',
            'target_commitish': 'original-sha', 'draft': False, 'prerelease': True,
            'published_at': '2026-09-24T17:00:00Z',
            'body': f'## v{self.version}\n\n- Previous description.\n\n{self.evidence}',
            'assets': [{'id': 17, 'name': 'welcomeeye_local.zip', 'state': 'uploaded',
                        'size': result['size'], 'digest': f'sha256:{result["sha256"]}'}],
        }
        (self.root / 'CHANGELOG.md').write_text(
            f'# Changelog\n\n## {self.version} - 2026-09-24\n\n'
            '- V1 gate physically confirmed by the maintainer.\n\n'
            '## 0.4.2-beta.3\n\n- Historical notes must stay outside this release.\n', encoding='utf-8',
        )

    def prepare(self, **changes):
        return notes.prepare_notes_update(
            self.root, changes.get('release', self.release), self.archive,
            self.published, changes.get('tag', f'v{self.version}'),
        )

    def test_only_generated_notes_change_evidence_tag_and_assets_preserved(self):
        before = deepcopy(self.release)
        payload = self.prepare()
        self.assertEqual(set(payload), {'body'})
        self.assertIn('V1 gate physically confirmed', payload['body'])
        self.assertNotIn('Historical notes', payload['body'])
        self.assertTrue(payload['body'].endswith(self.evidence))
        self.assertEqual(self.release, before)
        self.assertEqual(self.archive.read_bytes(), self.published.read_bytes())
        notes.verify_notes_update(before, before | payload, payload)

    def test_idempotent_notes_skip_update(self):
        self.release.update(self.prepare())
        self.assertIsNone(self.prepare())

    def test_original_evidence_whitespace_is_preserved_verbatim(self):
        evidence = self.evidence.replace('\n', '\r\n') + '\r\n'
        self.release['body'] = self.release['body'].replace(self.evidence, evidence)
        self.assertTrue(self.prepare()['body'].endswith(evidence))

    def test_stable_draft_unpublished_or_other_tag_refused(self):
        for change in ({'prerelease': False}, {'draft': True}, {'published_at': None},
                       {'id': 0}, {'id': True}, {'tag_name': 'v0.4.2-beta.5'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.prepare(release=self.release | change)
        with self.assertRaises(ValueError):
            self.prepare(tag='v0.4.2')
        with self.assertRaises(ValueError):
            notes.validate_existing_release(self.release | {'tag_name': 'v0.4.2'}, '0.4.2')

    def test_changed_component_requires_new_beta(self):
        (self.package / 'camera.py').write_text('# Different component code\n')
        builder.build_archive(self.root, self.archive)
        with self.assertRaisesRegex(ValueError, 'Package differs'):
            self.prepare()

    def test_even_different_zip_metadata_refused(self):
        with ZipFile(self.published, 'a') as archive:
            archive.comment = b'different archive'
        with self.assertRaisesRegex(ValueError, 'Package differs'):
            self.prepare()

    def test_asset_metadata_must_match_download(self):
        for changes in ({'state': 'new'}, {'size': 1}, {'digest': 'sha256:wrong'}, {'name': 'other.zip'}):
            release = deepcopy(self.release)
            release['assets'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.prepare(release=release)
        for assets in ([], self.release['assets'] * 2):
            with self.subTest(assets=assets), self.assertRaises(ValueError):
                self.prepare(release=self.release | {'assets': assets})

    def test_absent_optional_api_digest_still_requires_exact_download_bytes(self):
        self.release['assets'][0].pop('digest')
        self.assertIsNotNone(self.prepare())

    def test_unknown_body_or_missing_or_duplicate_evidence_never_overwritten(self):
        for body in ('handwritten release notes', f'## v{self.version}\n\nNo evidence section.',
                     self.release['body'] + self.evidence):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.prepare(release=self.release | {'body': body})

    def test_exact_changelog_section_required_and_cannot_replace_evidence(self):
        for changelog in ('## 0.4.2-beta.3\n- old', f'## {self.version}\n\n',
                          f'## {self.version}\n- one\n## {self.version}\n- duplicate',
                          f'## {self.version}\n### Validation evidence\nFake new evidence'):
            (self.root / 'CHANGELOG.md').write_text(changelog, encoding='utf-8')
            with self.subTest(changelog=changelog), self.assertRaises(ValueError):
                self.prepare()

    def test_verification_rejects_state_assets_or_body_changes(self):
        payload = self.prepare()
        for change in ({'body': 'different body'}, {'tag_name': 'v0.4.2-beta.5'},
                       {'target_commitish': 'new-sha'}, {'prerelease': False}, {'assets': []}):
            updated = self.release | payload | change
            with self.subTest(change=change), self.assertRaises(ValueError):
                notes.verify_notes_update(self.release, updated, payload)

    def test_workflow_existing_tag_path_is_gated_and_body_only(self):
        workflow = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
        sync = workflow.split('      - name: Synchronize existing beta notes', 1)[1]
        self.assertIn("if: steps.candidate.outputs.publish == 'false'", sync)
        self.assertIn("steps.existing.outputs.mode == 'notes'", sync)
        self.assertIn('--published-archive notes-sync-published/welcomeeye_local.zip', sync)
        self.assertIn('test "$MAIN_SHA" = "$CANDIDATE_SHA"', sync)
        self.assertIn('--input release-notes-update.json', sync)
        self.assertIn('--verify-response updated-release.json', sync)
        for mutation in ('git tag', 'git push', 'release upload', 'release create', 'release delete', '--draft=', '--prerelease'):
            self.assertNotIn(mutation, sync)


if __name__ == '__main__':
    unittest.main()
