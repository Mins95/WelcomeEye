"""Bounded, read-only QV history. Historical records never become live rings.

Wire fields: AlarmListQueryReqContent / AlarmListQueryResp in the vendor SDK.
Event labels: QvAlarmStatus. No badge identifier or media URL leaves this module.
"""
from dataclasses import dataclass, field
from datetime import datetime
import re
from xml.etree import ElementTree as ET

from .qv_cloud_protocol import (
    QvCloudDevice, QvCloudError, _child, _envelope, _identifier, _serialize,
    parse_envelope,
)

PAGE_SIZE = 15
MAX_PAGES = 4
MAX_RECORDS = PAGE_SIZE * MAX_PAGES
HISTORY_TIMEOUT = 30
EVENT_NAMES = {7: 'doorbell', 19: 'call', 20: 'call_accepted',
               38: 'indoor_call', 39: 'indoor_answered', 45: 'manager_call',
               46: 'manager_answered', 63: 'unlock', 71: 'unlock_failed'}
UNLOCK_METHODS = {0: 'unknown', 1: 'qr_code', 2: 'keypad', 3: 'indoor_monitor',
                  4: 'application', 5: 'rf_card', 6: 'exit_button', 7: 'dtmf',
                  8: 'google', 9: 'alexa', 10: 'face'}


def build_history_request(session_id, device, page=0, cursor='0'):
    if (not isinstance(device, QvCloudDevice) or device.cloud_type != 0
            or not device.channels):
        raise QvCloudError('unauthorized_device')
    if type(page) is not int or not 0 <= page < MAX_PAGES:
        raise QvCloudError('invalid_input')
    if not isinstance(cursor, str) or not re.fullmatch(r'[0-9]{1,64}', cursor, re.ASCII):
        raise QvCloudError('invalid_input')
    root, content = _envelope('client-query-recordlist', session_id)
    for key, value in (('maxid', cursor), ('pageno', page), ('pagelinenum', PAGE_SIZE)):
        _child(content, key, value)
    scope = ET.SubElement(content, 'filter')
    _child(ET.SubElement(scope, 'devid'), 'id', _identifier(device.uid))
    # The server-side filter is not the authorization boundary: every returned
    # row is checked again against device UID and authorized zero-based channels.
    return _serialize(root)


def _single(parent, name):
    nodes = parent.findall(name)
    if len(nodes) != 1:
        raise QvCloudError('invalid_response')
    return nodes[0]


def _text(parent, name, maximum=256):
    node = _single(parent, name)
    value = node.text or ''
    if len(node) or not value or len(value) > maximum:
        raise QvCloudError('invalid_response')
    return value


def _number(parent, name, maximum=2147483647):
    text = _text(parent, name, 10)
    if not re.fullmatch(r'[0-9]{1,10}', text, re.ASCII) or int(text) > maximum:
        raise QvCloudError('invalid_response')
    return int(text)


def _time(value):
    # Keep the vendor's wall time; no invented timezone conversion. Unknown
    # formats are omitted rather than exposing arbitrary remote text.
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}[ Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:[Zz]|[+-][0-9]{2}:[0-9]{2})?', value):
        return None
    try:
        datetime.fromisoformat(value.replace('t', 'T').replace('z', 'Z'))
    except ValueError:
        return None
    return value


@dataclass(repr=False)
class HistoryPage:
    cursor: str = field(repr=False)
    rows: list = field(repr=False)
    received: int
    rejected: int


def parse_history_page(payload, device):
    root = parse_envelope(payload)
    header = _single(root, 'header')
    if _number(header, 'result') != 0:
        raise QvCloudError('provider_rejected')
    commands = header.findall('command')
    if commands and _text(header, 'command') != 'client-query-recordlist':
        raise QvCloudError('invalid_response')
    content = _single(root, 'content')
    cursor = _text(content, 'maxid', 64)
    if not re.fullmatch(r'[0-9]{1,64}', cursor, re.ASCII):
        raise QvCloudError('invalid_response')
    record = _single(content, 'record')
    items = record.findall('data')
    if len(items) > PAGE_SIZE or len(items) != len(record):
        raise QvCloudError('invalid_response')
    rows, rejected = [], 0
    for item in items:
        uid = _text(item, 'devid')
        channel = _number(item, 'chno', 65535)
        if uid != device.uid or channel not in device.channels:
            rejected += 1
            continue
        identity = _text(item, 'id')
        event, state = _number(item, 'event'), _number(item, 'alarmstate')
        when = _time(_text(item, 'time', 40))
        safe = {'channel': channel + 1, 'event_code': event, 'state_code': state,
                'kind': EVENT_NAMES.get(event, 'unknown'), 'reported_time': when,
                'historical': True, 'physical_result_verified': False}
        if event in (63, 71):
            safe['reported_method'] = UNLOCK_METHODS.get(state, 'unknown')
        if event == 63:
            # AlarmInfoAdapter selects the lock icon from alarminfo=1/2.
            # It is not a badge number and never identifies a badge owner.
            info = item.findall('alarminfo')
            safe['reported_output'] = (int(info[0].text) if len(info) == 1
                and not len(info[0]) and info[0].text in ('1', '2') else None)
        rows.append((identity, safe))
    return HistoryPage(cursor, rows, len(items), rejected)
