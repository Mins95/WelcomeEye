"""Payload-free diagnostics for the native google-crc32c extension."""
import importlib.metadata
import importlib.util
import platform
import sys


def get_crc32c_diagnostics():
    """Report the installed package and native-loader result without secrets."""
    try:
        cext_spec_available = importlib.util.find_spec("google_crc32c.cext") is not None
    except (ImportError, ModuleNotFoundError, AttributeError):
        cext_spec_available = False
    result = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "architecture": platform.machine(),
        "platform": sys.platform,
        "package_version": None,
        "module_path": None,
        "implementation": None,
        "cext_spec_available": cext_spec_available,
        "cext_import_error_type": None,
    }
    try:
        result["package_version"] = importlib.metadata.version("google-crc32c")
    except importlib.metadata.PackageNotFoundError:
        return result
    try:
        import google_crc32c
        result["module_path"] = google_crc32c.__file__
        result["implementation"] = google_crc32c.implementation
    except Exception as exc:
        result["cext_import_error_type"] = type(exc).__name__
        return result
    if result["implementation"] != "c":
        try:
            import google_crc32c.cext  # noqa: F401
        except Exception as exc:
            result["cext_import_error_type"] = type(exc).__name__
    return result
