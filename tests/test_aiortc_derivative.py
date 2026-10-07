"""Offline invariants for the narrowly scoped native-CRC/PyAV derivative."""
import base64
from contextlib import redirect_stdout
import csv
from email import message_from_bytes
from hashlib import sha256
import importlib.util
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load('aiortc_builder', ROOT / 'tools/crc32c/build_aiortc.py')
installer = load('aiortc_install_verifier', ROOT / 'tools/crc32c/verify_ha_install.py')


def source_wheel(files):
    data = BytesIO()
    with ZipFile(data, 'w') as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return data.getvalue()


class DerivativeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.files = {
            'aiortc/rtcsctptransport.py': b'from google_crc32c import value as crc32c\n# protocol sentinel\n',
            'aiortc/__init__.py': b'__version__ = "1.15.0"\n# public API sentinel\n',
            'aiortc/codecs/h264.py': b'# unchanged codec implementation\n',
            'aiortc/rtcpeerconnection.py': b'# unchanged peer connection implementation\n',
            builder.OLD_INFO + '/METADATA': (
                b'Metadata-Version: 2.4\nName: aiortc\nVersion: 1.15.0\n'
                b'Summary: An implementation of WebRTC and ORTC\n'
                b'Requires-Dist: av<18.0.0,>=14.0.0\n'
                b'Requires-Dist: google-crc32c>=1.1\n'
                b'Requires-Dist: pyee>=13.0.0\n'),
            builder.OLD_INFO + '/WHEEL': b'Wheel-Version: 1.0\nTag: py3-none-any\n',
            builder.OLD_INFO + '/licenses/LICENSE': b'BSD-3-Clause license sentinel\n',
            builder.OLD_INFO + '/RECORD': b'obsolete upstream record\n',
        }

    def build(self, files=None, output=None):
        source = source_wheel(self.files if files is None else files)
        with patch.object(builder, 'UPSTREAM_SHA256', sha256(source).hexdigest()), redirect_stdout(StringIO()):
            return builder.build(source, output or self.root / 'out')

    def test_official_source_is_hash_pinned_and_wrong_hash_is_refused(self):
        self.assertEqual(builder.UPSTREAM_SHA256,
                         '4e1e54bff31a9c2cb654c7b7edc068085a7df53365e5df24a5cb24168e3f95f7')
        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
            builder.build(source_wheel(self.files), self.root / 'out')
        self.assertFalse((self.root / 'out').exists())

    def test_every_expected_patch_must_occur_exactly_once(self):
        for name, replacements in builder.PATCHES.items():
            for before, _ in replacements:
                for count in (0, 2):
                    files = self.files | {name: self.files[name].replace(before, before * count)}
                    with self.subTest(name=name, before=before, count=count):
                        with self.assertRaisesRegex(ValueError, 'Expected source differs'):
                            self.build(files)
        self.assertFalse((self.root / 'out').exists())

    def test_only_crc_import_and_version_change_in_runtime_code(self):
        with ZipFile(self.build()) as wheel:
            runtime = {name for name in wheel.namelist() if name.startswith('aiortc/')}
            self.assertEqual(runtime, {name for name in self.files if name.startswith('aiortc/')})
            for name in runtime:
                expected = self.files[name]
                if name == 'aiortc/rtcsctptransport.py':
                    expected = expected.replace(b'from google_crc32c import value as crc32c',
                                                b'from crc32c import crc32c')
                elif name == 'aiortc/__init__.py':
                    expected = expected.replace(b'1.15.0', builder.VERSION.encode())
                self.assertEqual(wheel.read(name), expected, name)
            self.assertEqual(wheel.read(builder.NEW_INFO + '/licenses/LICENSE'),
                             self.files[builder.OLD_INFO + '/licenses/LICENSE'])
            self.assertFalse(any(name.endswith(('.so', '.dll', '.pyd')) for name in wheel.namelist()))

    def test_native_crc_dependency_and_bounded_pyav_compatibility_metadata(self):
        with ZipFile(self.build()) as wheel:
            metadata = message_from_bytes(wheel.read(builder.NEW_INFO + '/METADATA'))
        self.assertEqual(metadata['Name'], 'aiortc')
        self.assertEqual(metadata['Version'], '1.15.0+welcomeeye.crc2')
        dependencies = metadata.get_all('Requires-Dist')
        self.assertIn('crc32c==2.9.post0', dependencies)
        self.assertIn('av<20.0.0,>=14.0.0', dependencies)
        self.assertIn('pyee>=13.0.0', dependencies)
        self.assertFalse(any('google-crc32c' in dependency for dependency in dependencies))

    def test_record_hashes_and_sizes_verify_every_output_file(self):
        with ZipFile(self.build()) as wheel:
            rows = list(csv.reader(StringIO(wheel.read(builder.NEW_INFO + '/RECORD').decode())))
            self.assertEqual({row[0] for row in rows}, set(wheel.namelist()))
            self.assertEqual(len(rows), len(wheel.namelist()))
            for name, digest, size in rows:
                if name.endswith('/RECORD'):
                    self.assertEqual((digest, size), ('', ''))
                    continue
                content = wheel.read(name)
                expected = base64.urlsafe_b64encode(sha256(content).digest()).rstrip(b'=').decode()
                self.assertEqual(digest, 'sha256=' + expected, name)
                self.assertEqual(int(size), len(content), name)

    def test_output_is_reproducible_with_matching_checksum_asset(self):
        first = self.build(output=self.root / 'first')
        second = self.build(output=self.root / 'second')
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(first.with_suffix('.whl.sha256').read_text(),
                         f'{sha256(first.read_bytes()).hexdigest()}  {builder.FILENAME}\n')

    def test_local_install_expectation_comes_from_built_metadata(self):
        wheel = self.build()
        requirement, version = installer.select_requirement(self.root)
        self.assertEqual(requirement, 'aiortc @ ' + wheel.as_uri())
        self.assertEqual(version, builder.VERSION)

    def test_published_install_does_not_require_a_local_wheel(self):
        manifest = self.root / 'custom_components/welcomeeye_local/manifest.json'
        manifest.parent.mkdir(parents=True)
        requirement = ('aiortc@https://github.com/Mins95/WelcomeEye/releases/download/v0.4.2/'
                       'aiortc-1.15.0%2Bwelcomeeye.crc2-py3-none-any.whl#sha256=abc')
        manifest.write_text(json.dumps({'requirements': ['unrelated==1', requirement]}))
        self.assertFalse((self.root / 'out').exists())
        self.assertEqual(installer.select_requirement(self.root, published=True),
                         (requirement, builder.VERSION))

    def test_pyav_constraint_is_required_and_read_exactly(self):
        constraints = self.root / 'package_constraints.txt'
        for version in ('17.0.1', '19.0.0'):
            constraints.write_text(f'# HA constraints\nav=={version}\nother==1\n')
            self.assertEqual(installer.constrained_av_version(constraints), version)
        constraints.write_text('av>=14\n')
        with self.assertRaisesRegex(ValueError, 'exact PyAV constraint'):
            installer.constrained_av_version(constraints)


if __name__ == '__main__':
    unittest.main()
