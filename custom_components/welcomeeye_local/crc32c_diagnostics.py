"""Payload-free native CRC32C diagnostics; call from an executor."""
import importlib
import importlib.metadata
import importlib.util
import inspect
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
        'crc32c_backend': None,
        'crc32c_package': None,
        'crc32c_hardware_accelerated': None,
        'crc32c_version': None,
        'crc32c_native_available': False,
        'crc32c_import_error_type': None,
    }
    try:
        # Inspect the callable actually loaded by SCTP, not merely an installed
        # distribution: a dependency may have been upgraded since process start.
        transport = importlib.import_module('aiortc.rtcsctptransport')
        checksum = transport.crc32c
        root = checksum.__module__.split('.')[0]
        if root not in ('crc32c', 'google_crc32c'):
            result['failure_kind'] = 'unknown_sctp_backend'
            return result
    except Exception as exc:
        result['failure_kind'] = 'sctp_import_failed'
        result['crc32c_import_error_type'] = type(exc).__name__
        return result
    package = 'crc32c' if root == 'crc32c' else 'google-crc32c'
    result['crc32c_package'] = package
    try:
        dist = importlib.metadata.distribution(package)
    except importlib.metadata.PackageNotFoundError:
        result['failure_kind'] = 'package_missing'
        return result
    result['package_version'] = dist.version
    result['crc32c_version'] = dist.version
    installer = (dist.read_text('INSTALLER') or '').strip()
    result['installer'] = installer if installer in ('pip', 'uv') else 'other'
    result['wheel_tags'] = [line[5:].strip() for line in
                            (dist.read_text('WHEEL') or '').splitlines()
                            if line.startswith('Tag: ')]
    result['native_binary_packaged'] = any(
        str(path).replace('\\', '/').startswith(root + '/_crc32c')
        and str(path).endswith(('.so', '.pyd')) for path in dist.files or ()
    )
    try:
        module = importlib.import_module(root)
        implementation = (('c' if inspect.isbuiltin(checksum) else 'python')
                          if root == 'crc32c' else module.implementation)
        result['implementation'] = implementation
        result['crc32c_backend'] = implementation
        result['reference_checksum_ok'] = checksum(b'123456789') == 0xE3069283
        result['native_spec_available'] = (
            importlib.util.find_spec(root + '._crc32c') is not None
        )
        native = importlib.import_module(root + ('._crc32c' if root == 'crc32c' else '.cext'))
        result['crc32c_native_available'] = implementation == 'c'
        if root == 'crc32c':
            result['crc32c_native_available'] &= checksum is native.crc32c
            result['crc32c_hardware_accelerated'] = bool(module.hardware_based)
    except Exception as exc:
        result['cext_import_error_type'] = type(exc).__name__
        result['crc32c_import_error_type'] = type(exc).__name__
        if isinstance(exc, ModuleNotFoundError) and exc.name == root + '._crc32c':
            result['failure_kind'] = 'native_binary_missing'
        else:
            result['failure_kind'] = 'native_import_failed'
    return result
