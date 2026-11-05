"""Dependency bootstrap compatibility without Home Assistant or package installs."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


class PrepareDependenciesTests(unittest.TestCase):
    def setUp(self):
        modules = {name: ModuleType(name) for name in (
            'homeassistant', 'homeassistant.requirements',
            'homeassistant.util', 'homeassistant.util.package')}
        modules['homeassistant.requirements'].pip_kwargs = lambda: {}
        modules['homeassistant.util.package'].install_package = Mock(return_value=True)
        path = Path(__file__).resolve().parents[1] / 'tools/prepare_runtime_dependencies.py'
        spec = importlib.util.spec_from_file_location('dependency_bootstrap_fixture', path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(self.module)

    def test_old_signature_keeps_configuration_and_constraints(self):
        expected = {'constraints': 'official.txt', 'timeout': 60, 'target': '/fixture/deps'}
        def previous(config_dir):
            self.assertEqual(config_dir, '/fixture')
            return expected
        self.module.ha_pip_kwargs = previous
        self.assertIs(self.module.pip_kwargs('/fixture'), expected)

    def test_new_signature_keeps_constraints_without_argument(self):
        expected = {'constraints': 'official.txt', 'timeout': 60}
        def current():
            return expected
        self.module.ha_pip_kwargs = current
        self.assertIs(self.module.pip_kwargs('/fixture'), expected)

    def test_internal_type_error_is_not_retried(self):
        calls = []
        def broken(config_dir):
            calls.append(config_dir)
            raise TypeError('internal helper failure')
        self.module.ha_pip_kwargs = broken
        with self.assertRaisesRegex(TypeError, 'internal helper failure'):
            self.module.pip_kwargs('/fixture')
        self.assertEqual(calls, ['/fixture'])

    def test_install_failure_remains_fatal(self):
        self.module.install_package.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'Could not prepare'):
            asyncio.run(self.module.prepare())
        self.module.install_package.assert_called_once()


if __name__ == '__main__':
    unittest.main()
