"""Payload-free native CRC32C diagnostics; call from an executor."""
import importlib
import importlib.metadata
import importlib.util
import platform
import sys
import sysconfig


def get_crc32c_diagnostics():
    """Distinguish a missing binary from a present binary that cannot load.

    Never export arbitrary exception messages, installation URLs or user paths.
    A system administrator can collect the exact loader error separately.
    """
    result = {
        'python_version': platform.python_version(),
        'python_implementation': platform.python_implementation(),
        'architecture': platform.machine(),
        'platform': sys.platform,
        'libc': list(platform.libc_ver()),
        'abi': sysconfig.get_config_var('SOABI'),
        'package_version': None,
        'installer': None,
        'wheel_tags': [],
        'native_binary_packaged': False,
        'native_spec_available': False,
        'implementation': None,
        'cext_import_error_type': None,
        'failure_kind': None,
        'reference_checksum_ok': None,
    }
    try:
        dist = importlib.metadata.distribution('google-crc32c')
    except importlib.metadata.PackageNotFoundError:
        result['failure_kind'] = 'package_missing'
        return result
    result['package_version'] = dist.version
    installer = (dist.read_text('INSTALLER') or '').strip()
    result['installer'] = installer if installer in ('pip', 'uv') else 'other'
    result['wheel_tags'] = [line[5:].strip() for line in
                            (dist.read_text('WHEEL') or '').splitlines()
                            if line.startswith('Tag: ')]
    result['native_binary_packaged'] = any(
        str(path).replace('\\', '/').startswith('google_crc32c/_crc32c')
        and str(path).endswith(('.so', '.pyd')) for path in dist.files or ()
    )
    try:
        module = importlib.import_module('google_crc32c')
        result['implementation'] = module.implementation
        result['reference_checksum_ok'] = module.value(b'123456789') == 0xE3069283
        result['native_spec_available'] = (
            importlib.util.find_spec('google_crc32c._crc32c') is not None
        )
        importlib.import_module('google_crc32c.cext')
    except Exception as exc:
        result['cext_import_error_type'] = type(exc).__name__
        if isinstance(exc, ModuleNotFoundError) and exc.name == 'google_crc32c._crc32c':
            result['failure_kind'] = 'native_binary_missing'
        else:
            result['failure_kind'] = 'native_import_failed'
    return result
