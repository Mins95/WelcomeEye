"""Bounded, read-only QV status projection; reported features are not grants.

DeviceAllInfoResp and TfCardInfoResp define the XML fields. QvDeviceAbility
decodes Base64, skips two header bytes and reads capability bits least-first.
Native SD capacity units are deliberately not labelled bytes, MB or GiB.
"""
import base64
import binascii
from hashlib import sha256
import json
import re

from .cgi import CGIError, _text, parse_response

MAX_DISKS = 16
SD_STATES = ('normal', 'notformatted', 'error', 'sleep', 'nodisk',
             'formatting', 'mixed', 'unknown')
# QvDeviceAbility constants only. Never feed these observations into MATRIX,
# endpoint selection, channel enablement or physical-control authorization.
REPORTED_CAPABILITIES = {
    'video_input': 0, 'audio_output': 1, 'audio_input': 2, 'sd_card': 5,
    'motion_detection': 8, 'picture': 9, 'recording': 10,
    'record_schedule': 13, 'multi_channel': 19, 'call': 21,
    'record_download': 83, 'rfid_management': 116,
}


def configuration_binding(data):
    """Private in-memory comparison token; never export or persist this hash."""
    return sha256(json.dumps(dict(data), sort_keys=True, separators=(',', ':'),
                             ensure_ascii=True).encode()).digest()


def _optional(parent, name):
    nodes = parent.findall(name)
    if len(nodes) > 1:
        raise CGIError('invalid_device_info')
    return nodes[0] if nodes else None


def _value(parent, name):
    value = _text(parent, name, optional=True)
    if len(value) > 128 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise CGIError('invalid_device_info')
    return value or None


def _number(parent, name):
    value = _value(parent, name)
    if value is None:
        return None
    if not re.fullmatch(r'[0-9]{1,10}', value) or int(value) > 2147483647:
        raise CGIError('invalid_device_info')
    return int(value)


def _boolean(parent, name):
    value = _value(parent, name)
    if value not in (None, 'true', 'false'):
        raise CGIError('invalid_device_info')
    return None if value is None else value == 'true'


def _capacity(parent, total_name, free_name):
    total, free = _number(parent, total_name), _number(parent, free_name)
    if total is not None and free is not None and free > total:
        raise CGIError('invalid_device_info')
    return {'total_native': total, 'free_native': free, 'capacity_unit': 'unknown',
            'used_percent': round(100 * (total - free) / total, 1)
            if total and free is not None else None}


def reported_capabilities(value):
    if not value:
        return {}
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise CGIError('invalid_device_info') from None
    if not 5 <= len(raw) <= 21:
        raise CGIError('invalid_device_info')
    # Header semantics are not assumed. Absent trailing bits stay unknown;
    # unlike the app's zero-filled array they do not become false reports.
    payload = raw[2:]
    return {name: bool(payload[index // 8] & (1 << (index % 8)))
            for name, index in REPORTED_CAPABILITIES.items() if index < len(payload) * 8}


def _sd_card(node):
    formatting = _boolean(node, 'formatting')
    result = {'state': 'unknown', 'formatting': formatting,
              **_capacity(node, 'totalsum', 'freesum'), 'disks': []}
    rows = _optional(node, 'datalist')
    if rows is not None:
        rows = rows.findall('data')
        if len(rows) > MAX_DISKS:
            raise CGIError('device_info_disk_limit')
        seen = set()
        for row in rows:
            disk_id = _number(row, 'diskid')
            if disk_id is not None:
                if disk_id in seen:
                    raise CGIError('invalid_device_info')
                seen.add(disk_id)
            status = _value(row, 'status')
            disk = {'disk_id': disk_id, 'exists': _boolean(row, 'exist'),
                    'state': status if status in SD_STATES[:5] else 'unknown',
                    **_capacity(row, 'total', 'free')}
            result['disks'].append(disk)
    states = {disk['state'] for disk in result['disks']}
    if formatting is True:
        result.update(state='formatting', used_percent=None, disks=[])
    elif len(states) == 1:
        result['state'] = states.pop()
    elif states:
        result['state'] = 'mixed'
    return result


def parse_device_status(data):
    content = parse_response(data)
    info = _optional(content, 'info')
    result = {'model': None, 'firmware': None, 'firmware_release_date': None,
              'reported_capabilities': reported_capabilities(_value(content, 'devability')),
              'capabilities_source': 'device_reported', 'reported_capabilities_verified': False,
              'sd_card': None}
    if info is not None:
        for key, field in (('model', 'model'), ('firmware', 'version'),
                           ('firmware_release_date', 'releasedate')):
            result[key] = _value(info, field)
    card = _optional(content, 'tfcard')
    if card is not None:
        result['sd_card'] = _sd_card(card)
    return result


def parse_tfcard_info(data):
    return _sd_card(parse_response(data))
