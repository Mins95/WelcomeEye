"""Private, endpoint-bound observations; a QV channel is not a physical label."""
from hashlib import sha256
from hmac import compare_digest
from ipaddress import ip_address
import json


class ChannelBusyError(RuntimeError):
    reason = 'other_channel_busy'


def media_profile_binding(data):
    """Bind an observation to the selected endpoint and approved certificate pins."""
    try:
        host = str(ip_address(data['host']))
    except (KeyError, TypeError, ValueError):
        return None
    transport = data.get('media_transport', 'tls')
    cgi_port = data.get('cgi_port', 443)
    media_port = 34567 if transport == 'connect3_tcp' else data.get('media_port', 8443)
    pins = (data.get('certificate_sha256', ''),
            data.get('media_certificate_sha256') or data.get('certificate_sha256', ''))
    if (transport not in ('tls', 'connect3_tcp')
            or any(type(port) is not int or not 1 <= port <= 65535 for port in (cgi_port, media_port))
            or any(type(pin) is not str for pin in pins)):
        return None
    profile = {'host': host, 'cgi_port': cgi_port, 'media_port': media_port,
        'media_transport': transport, 'certificate_sha256': pins[0],
        'media_certificate_sha256': pins[1]}
    return sha256(json.dumps(profile, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def valid_observed_channels(data):
    record = data.get('observed_media_channels')
    binding = media_profile_binding(data)
    if (binding is None or type(record) is not dict or set(record) != {'binding', 'channels'}
            or type(record.get('binding')) is not str or len(record['binding']) != 64
            or any(character not in '0123456789abcdef' for character in record['binding'])
            or not compare_digest(binding, record['binding'])
            or type(record.get('channels')) is not list
            or not 1 <= len(record['channels']) <= 2
            or any(type(channel) is not int or channel not in (1, 2) for channel in record['channels'])):
        return frozenset()
    return frozenset(record['channels'])


def channel2_enabled(data):
    """Explicit disable wins; unsigned discovery alone never enables a camera."""
    if 'second_channel_enabled' in data:
        return data['second_channel_enabled'] is True
    return 2 in valid_observed_channels(data)
