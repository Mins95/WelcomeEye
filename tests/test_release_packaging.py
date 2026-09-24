"""Verify distributable contents and fail-closed publication gates offline."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_builder', ROOT / 'tools/build_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / 'custom_components/welcomeeye_local'
        self.version = '0.4.2-beta.4'
        for name in release.REQUIRED_FILES:
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}\n' if path.suffix == '.json' else '# fixture\n', encoding='utf-8')
        (self.package / 'manifest.json').write_text(json.dumps({'domain': 'welcomeeye_local', 'version': self.version}))
        (self.package / 'const.py').write_text(f'VERSION = "{self.version}"\n')
        self.output = self.root / 'dist/welcomeeye_local.zip'

    def test_hacs_flat_root_and_digest_asset(self):
        result = release.build_archive(self.root, self.output)
        with ZipFile(self.output) as archive:
            self.assertEqual(set(archive.namelist()), release.REQUIRED_FILES)
            self.assertEqual(json.loads(archive.read('manifest.json'))['version'], self.version)
        expected = sha256(self.output.read_bytes()).hexdigest()
        self.assertEqual(result['sha256'], expected)
        self.assertEqual(self.output.with_suffix('.zip.sha256').read_text(), f'{expected}  welcomeeye_local.zip\n')

    def test_reproducible_across_timestamps_and_line_endings(self):
        release.build_archive(self.root, self.output)
        original = self.output.read_bytes()
        for path in self.package.rglob('*'):
            if path.is_file():
                data = path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
                path.write_bytes(data)
                os.utime(path, (1700000000, 1700000000))
        release.build_archive(self.root, self.output)
        self.assertEqual(self.output.read_bytes(), original)

    def test_cache_bytecode_and_secret_files_excluded(self):
        for name in ('__pycache__/client.pyc', '__pycache__/oops.py', '.env', 'debug.log', 'capture.jpeg'):
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'not releasable')
        release.build_archive(self.root, self.output)
        with ZipFile(self.output) as archive:
            self.assertEqual(set(archive.namelist()), release.REQUIRED_FILES)

    def test_missing_file_keeps_previous_archive_and_removes_temporary(self):
        release.build_archive(self.root, self.output)
        original = self.output.read_bytes()
        (self.package / 'camera.py').unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            release.build_archive(self.root, self.output)
        self.assertEqual(self.output.read_bytes(), original)
        self.assertEqual(sorted(path.name for path in self.output.parent.iterdir()),
                         ['welcomeeye_local.zip', 'welcomeeye_local.zip.sha256'])

    def test_version_mismatch_refuses_build(self):
        (self.package / 'const.py').write_text('VERSION = "0.4.2-beta.3"')
        with self.assertRaisesRegex(ValueError, 'disagree'):
            release.build_archive(self.root, self.output)

    def test_archive_traversal_refused(self):
        for name in ('../outside.py', '/absolute.py', 'C:/absolute.py'):
            with self.subTest(name=name):
                release.build_archive(self.root, self.output)
                with ZipFile(self.output, 'a') as archive:
                    archive.writestr(name, '# bad')
                with self.assertRaisesRegex(ValueError, 'Unsafe'):
                    release.validate_archive(self.output, self.version)


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.args = dict(
            version='0.4.2-beta.4', ref='refs/heads/main', sha='candidate-sha', main_sha='candidate-sha',
            repository='Mins95/WelcomeEye', hardware_validated=True,
            evidence='https://github.com/Mins95/WelcomeEye/issues/123#issuecomment-456',
            validation_runs=[{'head_sha': 'candidate-sha', 'head_branch': 'main',
                              'head_repository': {'full_name': 'Mins95/WelcomeEye'},
                              'event': 'push', 'status': 'completed', 'conclusion': 'success'}],
        )

    def test_current_beta_requires_all_evidence(self):
        release.validate_release_gate(**self.args)

    def test_claimed_hardware_validation_requires_evidence(self):
        for updates in ({'hardware_validated': 'true'},
                        {'evidence': ''}, {'evidence': 'tested in the past'}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                release.validate_release_gate(**(self.args | updates))

    def test_prerelease_for_user_hardware_testing_is_allowed(self):
        release.validate_release_gate(**(self.args | {'hardware_validated': False, 'evidence': ''}))

    def test_stable_other_version_and_non_main_are_refused(self):
        for updates in ({'version': '0.4.2'}, {'version': '0.4.3'}, {'version': '0.4.2-rc.1'},
                        {'ref': 'refs/heads/feature'}, {'main_sha': 'newer-sha'}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                release.validate_release_gate(**(self.args | updates))

    def test_old_failed_partial_or_foreign_ci_does_not_authorize_release(self):
        for update in ({'head_sha': 'old-sha'}, {'conclusion': 'failure'}, {'status': 'in_progress'},
                       {'event': 'pull_request'}, {'head_branch': 'feature'},
                       {'head_repository': {'full_name': 'someone/fork'}}):
            args = deepcopy(self.args)
            args['validation_runs'][0].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                release.validate_release_gate(**args)
        with self.assertRaises(ValueError):
            release.validate_release_gate(**(self.args | {'validation_runs': []}))

    def test_stable_promotion_requires_explicit_opt_in_evidence_and_exact_ci(self):
        args = self.args | {'version': '0.4.2', 'stable_promotion': True,
                            'hardware_validated': False}
        release.validate_release_gate(**args)
        for updates in ({'stable_promotion': False}, {'stable_promotion': 'true'},
                        {'evidence': ''}, {'evidence': 'http://example.org/report'},
                        {'version': '0.4.3'}, {'version': '0.4.2-beta.4'},
                        {'ref': 'refs/heads/feature'}, {'main_sha': 'newer-sha'},
                        {'validation_runs': []}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                release.validate_release_gate(**(args | updates))

    def test_stable_workflow_requires_manual_promotion_and_keeps_existing_assets(self):
        workflow = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
        self.assertIn('needs: channel', workflow)
        self.assertIn("if: needs.channel.outputs.enabled == 'true'", workflow)
        self.assertIn('"$STABLE_PROMOTION" == \'true\'', workflow)
        existing = workflow.split('      - name: Inspect existing package', 1)[1].split('      - name:', 1)[0]
        self.assertIn("steps.candidate.outputs.prerelease == 'true'", existing)
        self.assertIn('--draft=false --prerelease=false --latest', workflow)

    def test_workflow_publishes_only_ci_gated_immutable_verified_prerelease(self):
        workflow = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
        trigger = workflow.split('on:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertIn('workflow_dispatch:', trigger)
        self.assertIn('workflow_run:', trigger)
        self.assertIn('workflows: [Validate]', trigger)
        self.assertIn('branches: [main]', trigger)
        self.assertNotIn('push:', trigger)
        self.assertIn('default: false', trigger)
        self.assertIn('python tools/build_release.py --release-check', workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", workflow)
        self.assertIn('git tag "$TAG" "$CANDIDATE_SHA"', workflow)
        self.assertIn('Hardware validation of this beta candidate is PENDING', workflow)
        self.assertEqual(workflow.count('--force-with-lease='), 1)
        self.assertNotIn('--force ', workflow)
        self.assertEqual(workflow.count('--clobber'), 1)
        self.assertIn('publish=false', workflow)
        self.assertIn('--verify-tag --draft --prerelease', workflow)
        self.assertLess(workflow.index('run: python tools/build_release.py\n'), workflow.index('git tag "$TAG"'))
        self.assertLess(workflow.index('sha256sum --check'), workflow.index('--draft=false'))


if __name__ == '__main__':
    unittest.main()
