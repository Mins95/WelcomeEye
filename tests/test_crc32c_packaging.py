"""Native loader classification without hiding warnings or changing CRCs."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('crc_diag_under_test', ROOT / 'custom_components/welcomeeye_local/crc32c_diagnostics.py')
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)


class Crc32cPackagingTests(unittest.TestCase):
    def check(self, *, binary=False, error=None, checksum=0xE3069283, package='google_crc32c', missing=False):
        dist = SimpleNamespace(version='2.9.post0' if package == 'crc32c' else '1.8.0', files=[package + '/_crc32c.cpython-314-x86_64-linux-musl.so'] if binary else [package + '/cext.py'],
                               read_text=lambda f: 'uv' if f == 'INSTALLER' else 'Tag: py3-none-any\n')
        def value(data):
            return checksum
        value.__module__ = package + ('._crc32c' if binary else '.python')
        module = SimpleNamespace(implementation='c' if binary else 'python', value=value,
                                 crc32c=value, hardware_based=binary)
        def load(name):
            if name.endswith(('.cext', '._crc32c')) and error:
                raise error
            return module
        with patch.object(diag.importlib.metadata, 'distribution', return_value=dist,
                          side_effect=diag.importlib.metadata.PackageNotFoundError if missing else None), \
             patch.object(diag.importlib, 'import_module', side_effect=load), \
             patch.object(diag.inspect, 'isbuiltin', return_value=binary), \
             patch.object(diag.importlib.util, 'find_spec', return_value=object() if binary else None):
            return diag.get_crc32c_diagnostics()

    def test_python_wheel_is_not_a_native_extension(self):
        d = self.check(error=ModuleNotFoundError('secret URL must not appear', name='google_crc32c._crc32c'))
        self.assertEqual(d['failure_kind'], 'native_binary_missing')
        self.assertFalse(d['native_binary_packaged'])
        self.assertFalse(d['native_spec_available'])
        self.assertTrue(d['reference_checksum_ok'])
        self.assertNotIn('secret', json.dumps(d))

    def test_present_but_unloadable_extension_is_distinct(self):
        d = self.check(binary=True, error=ImportError('private/path missing dependency'))
        self.assertTrue(d['native_binary_packaged'])
        self.assertEqual(d['failure_kind'], 'native_import_failed')
        self.assertEqual(d['cext_import_error_type'], 'ImportError')
        self.assertNotIn('private/path', json.dumps(d))

    def test_native_success_and_reference_crc(self):
        d = self.check(binary=True)
        self.assertIsNone(d['failure_kind'])
        self.assertEqual(d['implementation'], 'c')
        self.assertTrue(d['reference_checksum_ok'])
        self.assertFalse(self.check(binary=True, checksum=0)['reference_checksum_ok'])

    def test_missing_package(self):
        self.assertEqual(self.check(missing=True)['failure_kind'], 'package_missing')

    def test_actual_native_crc32c_backend_is_reported(self):
        d = self.check(binary=True, package='crc32c')
        self.assertEqual(d['crc32c_package'], 'crc32c')
        self.assertEqual(d['crc32c_version'], '2.9.post0')
        self.assertEqual(d['crc32c_backend'], 'c')
        self.assertTrue(d['crc32c_native_available'])
        self.assertTrue(d['crc32c_hardware_accelerated'])
        self.assertTrue(d['reference_checksum_ok'])

    def test_sctp_import_failure_does_not_leak_private_paths(self):
        with patch.object(diag.importlib, 'import_module', side_effect=ImportError('secret/path')):
            d = diag.get_crc32c_diagnostics()
        self.assertEqual(d['failure_kind'], 'sctp_import_failed')
        self.assertFalse(d['crc32c_native_available'])
        self.assertNotIn('secret', json.dumps(d))

    def test_manifest_pins_verified_derivative_not_unavailable_google_binary(self):
        manifest = json.loads((ROOT / 'custom_components/welcomeeye_local/manifest.json').read_text())
        self.assertEqual(manifest['requirements'], [
            'aiortc @ https://github.com/Mins95/WelcomeEye/releases/download/v0.4.2/'
            'aiortc-1.15.0%2Bwelcomeeye.crc1-py3-none-any.whl'
            '#sha256=75f7d14e598dfd2b3e97bd4b9342e9b675185b6a3d83d5a5083c43250b6eee9d'])

    def test_integration_does_not_suppress_crc_warnings(self):
        source = '\n'.join(p.read_text(encoding='utf-8') for p in (ROOT / 'custom_components/welcomeeye_local').glob('*.py'))
        self.assertNotIn('filterwarnings', source)
        self.assertNotIn('warnings.filter', source)


if __name__ == '__main__':
    unittest.main()
