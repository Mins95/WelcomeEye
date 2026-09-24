"""Offline rejection checks for the maintainer-authorized beta.4 replacement."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import sys
import unittest
from unittest.mock import patch

import test_release_notes_sync as notes_tests
import test_release_packaging as packaging

ROOT, builder = packaging.ROOT, packaging.release
spec = importlib.util.spec_from_file_location('replacement_under_test', ROOT / 'tools/replace_beta4.py')
replacement = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, build_release=builder, sync_release_notes=notes_tests.notes):
    spec.loader.exec_module(replacement)


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        fixture = packaging.PackagingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.package, self.archive = fixture.root, fixture.package, fixture.output
        self.old_archive = self.root / 'old/welcomeeye_local.zip'
        self.old_digest = builder.build_archive(self.root, self.old_archive)['sha256']
        self.old_checksum = self.old_archive.with_suffix('.zip.sha256')
        (self.package / 'camera.py').write_text('# Reviewed replacement\n')
        self.new_digest = builder.build_archive(self.root, self.archive)['sha256']
        # Tiny synthetic packages exercise the real digest and ZIP checks offline.
        self.digest_patch = patch.object(replacement, 'OLD_ZIP_SHA256', self.old_digest)
        self.digest_patch.start()
        self.addCleanup(self.digest_patch.stop)
        self.plan = dict(schema=1, repository=replacement.REPOSITORY, tag=replacement.TAG,
                         old_tag_sha=replacement.OLD_TAG_SHA, old_zip_sha256=self.old_digest,
                         new_zip_sha256=self.new_digest)
        self.release = dict(id=42, tag_name=replacement.TAG, name=replacement.TAG,
                            target_commitish='main', draft=False, prerelease=True,
                            published_at='2026-09-24T17:00:00Z', body='Previous notes',
                            assets=self.assets(self.old_archive, self.old_checksum))
        self.args = dict(plan=self.plan, repository=replacement.REPOSITORY, release=self.release,
                         archive=self.archive, published_archive=self.old_archive,
                         published_checksum=self.old_checksum, tag_sha=replacement.OLD_TAG_SHA,
                         candidate_sha='a' * 40,
                         notes=f'## {replacement.TAG}\n\nNew notes\n\n### Validation evidence\n\n'
                               '- Hardware validation of this beta candidate is PENDING.\n')

    @staticmethod
    def assets(archive, checksum):
        return [dict(id=index, name=path.name, state='uploaded', size=path.stat().st_size,
                     digest=f'sha256:{sha256(path.read_bytes()).hexdigest()}')
                for index, path in enumerate((archive, checksum), 1)]

    def prepare(self, **changes):
        return replacement.prepare_replacement(**(self.args | changes))

    def test_authorized_exact_old_and_new_packages_prepare_body_only(self):
        before = deepcopy(self.release)
        payload = self.prepare()
        self.assertEqual(set(payload), {'body'})
        self.assertIn(self.old_digest, payload['body'])
        self.assertIn(self.new_digest, payload['body'])
        self.assertIn('PENDING', payload['body'])
        self.assertEqual(self.release, before)

    def test_other_repository_tag_old_commit_digest_or_extra_plan_field_refused(self):
        for change in ({'repository': 'some/fork'}, {'tag': 'v0.4.2-beta.5'}, {'tag': 'v0.4.2'},
                       {'old_tag_sha': 'b' * 40}, {'old_zip_sha256': 'b' * 64}, {'schema': True},
                       {'extra_authorization': 'any release'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.prepare(plan=self.plan | change)
        with self.assertRaises(ValueError):
            self.prepare(repository='some/fork')

    def test_missing_unpinned_or_identical_new_digest_refused(self):
        for digest in (None, '', 'FILL_ME', 'B' * 64, self.old_digest, 'b' * 64):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                self.prepare(plan=self.plan | {'new_zip_sha256': digest})
        plan = self.plan.copy()
        del plan['new_zip_sha256']
        with self.assertRaises(ValueError):
            self.prepare(plan=plan)

    def test_already_moved_or_replayed_tag_refused(self):
        for tag_sha in ('a' * 40, 'b' * 40, '', replacement.OLD_TAG_SHA + '\nother'):
            with self.subTest(tag_sha=tag_sha), self.assertRaisesRegex(ValueError, 'cannot be replayed'):
                self.prepare(tag_sha=tag_sha)

    def test_candidate_must_be_new_full_commit(self):
        for candidate in ('main', 'abcdef', '', replacement.OLD_TAG_SHA):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                self.prepare(candidate_sha=candidate)

    def test_draft_stable_other_tag_or_unpublished_release_refused(self):
        for change in ({'draft': True}, {'prerelease': False}, {'published_at': None},
                       {'tag_name': 'v0.4.2-beta.5'}, {'id': True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.prepare(release=self.release | change)

    def test_both_assets_must_be_present_unique_and_uploaded(self):
        for assets in ([], self.release['assets'][:1], self.release['assets'] * 2):
            with self.subTest(assets=assets), self.assertRaises(ValueError):
                self.prepare(release=self.release | {'assets': assets})
        for index in (0, 1):
            for change in ({'id': True}, {'id': 0}, {'state': 'new'}, {'size': 1},
                           {'digest': 'sha256:incorrect'}, {'name': 'other.zip'}):
                release = deepcopy(self.release)
                release['assets'][index].update(change)
                with self.subTest(index=index, change=change), self.assertRaises(ValueError):
                    self.prepare(release=release)

    def test_wrong_old_archive_and_tampered_old_or_new_checksum_refused(self):
        with self.assertRaises(ValueError):
            self.prepare(published_archive=self.archive)
        for path in (self.old_checksum, self.archive.with_suffix('.zip.sha256')):
            original = path.read_bytes()
            path.write_bytes(b'incorrect checksum\n')
            try:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    self.prepare()
            finally:
                path.write_bytes(original)

    def test_absent_optional_github_digests_still_verify_actual_bytes(self):
        for asset in self.release['assets']:
            asset.pop('digest')
        self.prepare()

    def test_notes_require_correct_version_and_current_evidence(self):
        for notes in ('handwritten notes', '## v0.4.2-beta.5\n\n### Validation evidence\n',
                      self.args['notes'] + '### Validation evidence\n'):
            with self.subTest(notes=notes), self.assertRaises(ValueError):
                self.prepare(notes=notes)

    def verification_args(self):
        payload = self.prepare()
        return dict(plan=self.plan, repository=replacement.REPOSITORY, previous=self.release,
                    updated=self.release | payload | {
                        'assets': self.assets(self.archive, self.archive.with_suffix('.zip.sha256'))},
                    payload=payload, archive=self.archive, published_archive=self.archive,
                    published_checksum=self.archive.with_suffix('.zip.sha256'),
                    tag_sha='a' * 40, candidate_sha='a' * 40)

    def test_replacement_verifies_assets_tag_and_preserved_release_state(self):
        replacement.verify_replacement(**self.verification_args())

    def test_postcheck_rejects_old_tag_asset_or_changed_release_status(self):
        args = self.verification_args()
        for change in ({'tag_sha': replacement.OLD_TAG_SHA}, {'published_archive': self.old_archive},
                       {'published_checksum': self.old_checksum}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replacement.verify_replacement(**(args | change))
        for change in ({'draft': True}, {'prerelease': False}, {'published_at': 'new-date'},
                       {'name': 'New title'}, {'target_commitish': 'new-target'},
                       {'id': 99}, {'body': 'wrong notes'}, {'assets': []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replacement.verify_replacement(**(args | {'updated': args['updated'] | change}))


class ReplacementWorkflowTests(unittest.TestCase):
    def test_checked_in_plan_pins_explicit_authorization(self):
        plan = json.loads((ROOT / '.github/release-replacement-beta4.json').read_text())
        self.assertEqual(plan['old_tag_sha'], 'ef599f64e4917c293deed38d1a2c16022c3e751b')
        self.assertEqual(plan['old_zip_sha256'], '4344443d21d5a9d4b5894c12ff956b8935ed96134088d0047c21af89775e0de2')
        replacement.validate_plan(plan, 'Mins95/WelcomeEye')

    def test_only_explicit_beta4_path_can_lease_tag_and_replace_assets(self):
        workflow = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
        section = workflow.split('      - name: Apply the one explicitly authorized beta.4 replacement\n')[1]
        section = section.split('      - name: Synchronize existing beta notes', 1)[0]
        self.assertIn("steps.existing.outputs.mode == 'replace'", section)
        self.assertIn('test "$TAG" = \'v0.4.2-beta.4\'', section)
        lease = '--force-with-lease=refs/tags/v0.4.2-beta.4:ef599f64e4917c293deed38d1a2c16022c3e751b'
        self.assertEqual(workflow.count(lease), 1)
        self.assertLess(section.index('python tools/replace_beta4.py'), section.index('git push'))
        self.assertLess(section.index('test "$MAIN_SHA" = "$CANDIDATE_SHA"'), section.index('git push'))
        self.assertIn('gh release upload v0.4.2-beta.4 --clobber', section)
        self.assertIn('--published-checksum notes-sync-published/welcomeeye_local.zip.sha256', section)
        self.assertIn('--input replacement-notes-update.json', section)
        self.assertIn('--verify-response replaced-release.json', section)
        self.assertLess(section.index('sha256sum --check'), section.index('--method PATCH'))
        for forbidden in ('git tag', '--draft', '--prerelease', '--latest', 'release delete'):
            self.assertNotIn(forbidden, section)


if __name__ == '__main__':
    unittest.main()
