"""Offline installation-QR import, derived from Door Connect DeviceHelper.

No QR URL is followed. The original QR and AP information are never retained.
Import is not proof that an installation code is still the current authCode.
"""
from dataclasses import dataclass, field
import json

from .cgi import encode_auth_code

MAX_QR_BYTES = 2048


class CredentialImportError(ValueError):
    """Fixed local error code, never the QR or any of its fields."""


@dataclass(frozen=True, repr=False)
class InstallationCredential:
    uid: str = field(repr=False)
    auth_code: str = field(repr=False)
    format: str


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CredentialImportError('duplicate_qr_field')
        result[key] = value
    return result


def _token(value, limit):
    if (not isinstance(value, str) or not 0 < len(value) <= limit
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)):
        raise CredentialImportError('invalid_qr_field')
    value.encode('utf-8', errors='strict')
    return value


def parse_installation_qr(text):
    """Only the two demonstrated formats; never a cloud/share/reset QR."""
    try:
        if not isinstance(text, str) or not 0 < len(text.encode('utf-8')) <= MAX_QR_BYTES:
            raise CredentialImportError('invalid_qr_size')
        text = text.strip()
        if text.startswith('{'):
            # The app uses Gson DeviceQrCodeV2: u=UID, c=authCode, m=model.
            data = json.loads(text, object_pairs_hook=_pairs)
            if (not isinstance(data, dict) or set(data) - {'u', 'c', 'm', 'a', 'd', 'v'}
                    or not {'u', 'c', 'm'} <= data.keys()
                    or any(not isinstance(v, str) for k, v in data.items() if k != 'v')
                    or ('v' in data and (type(data['v']) is not int or not 0 <= data['v'] <= 255))):
                raise CredentialImportError('unsupported_qr_format')
            uid, auth_code, model = data['u'], data['c'], data['m']
            format_name = 'apk_json'
        else:
            # DeviceQrCodeInfo constructor: AP name, UID, authCode, model.
            # Deliberately reject extra tokens instead of silently ignoring them.
            parts = [p for p in text.split(' ') if p]
            if len(parts) != 4:
                raise CredentialImportError('unsupported_qr_format')
            ap_name, uid, auth_code, model = parts
            _token(ap_name, 128)
            format_name = 'apk_space'
        _token(uid, 64)
        _token(auth_code, 256)
        # This import belongs only to the model reported by the Connect 3 tester.
        # Generic SDK QR support must not enable another device family here.
        if model != 'IDS94E6SW':
            raise CredentialImportError('unsupported_qr_model')
        encode_auth_code(auth_code)
        return InstallationCredential(uid, auth_code, format_name)
    except CredentialImportError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise CredentialImportError('invalid_installation_qr') from None
