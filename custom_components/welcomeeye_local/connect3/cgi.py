"""Read-only QV CGI subset proven in Door Connect; no media or physical opcode.

Credentials are supplied by the owner, never obtained from the manufacturer.
TLS uses normal trust or an explicitly supplied SHA256 certificate fingerprint.
"""
import asyncio
from datetime import datetime
from hashlib import sha256
from ipaddress import IPv4Address
import re
from xml.etree import ElementTree as ET

import aiohttp

MAX_XML = 256 * 1024
MAX_NODES = 4096
MAX_DEPTH = 12
MAX_PAGES = 4
MAX_RECORDS = 128
TIMEOUT = 12.0
COMMANDS = frozenset(('get.device.streamkey', 'get.record.session', 'get.record.message'))


class CGIError(ValueError):
    """Fixed local error code only; no remote text or secret in exceptions."""


def encode_auth_code(value):
    if not isinstance(value, str) or not value or len(value) > 256:
        raise CGIError('invalid_auth_code')
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise CGIError('invalid_auth_code')
    try:
        units = len(value.encode('utf-16-le')) // 2
        raw = value.encode('utf-8')
    except UnicodeError:
        raise CGIError('invalid_auth_code') from None
    return sha256(raw).hexdigest() if units < 64 else value


def envelope(command, auth_code, fields=None):
    if command not in COMMANDS:
        raise CGIError('unsupported_command')
    fields = fields or {}
    expected = {'get.device.streamkey': set(), 'get.record.message': {'id'},
                'get.record.session': {'channel', 'filetype', 'occurtype', 'starttime', 'endtime', 'stream'}}
    if set(fields) != expected[command]:
        raise CGIError('invalid_fields')
    root = ET.Element('envelope')
    header = ET.SubElement(root, 'header')
    for key, value in (('security', 'username'), ('username', 'adminapp2'),
                       ('password', encode_auth_code(auth_code)), ('passwordencode', '1')):
        ET.SubElement(header, key).text = value
    body = ET.SubElement(root, 'body')
    ET.SubElement(body, 'command').text = command
    content = ET.SubElement(body, 'content')
    if fields:
        record = ET.SubElement(content, 'record')
        for key, value in fields.items():
            if not isinstance(value, str) or len(value) > 256 or any(ord(c) < 32 for c in value):
                raise CGIError('invalid_fields')
            ET.SubElement(record, key).text = value
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def _one(parent, name):
    items = parent.findall(name)
    if len(items) != 1:
        raise CGIError('invalid_xml_structure')
    return items[0]


def _text(parent, name, *, optional=False):
    items = parent.findall(name)
    if not items and optional:
        return ''
    if len(items) != 1 or len(items[0]):
        raise CGIError('invalid_xml_structure')
    value = items[0].text or ''
    if len(value) > 1024 or any(ord(c) < 32 and c not in '\n\r\t' for c in value):
        raise CGIError('invalid_xml_value')
    return value


def parse_response(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_XML:
        raise CGIError('xml_size')
    # Only UTF-8 is supported. Reject DTD/entities before feeding Expat; this
    # also excludes their UTF-16/32 forms rather than bypassing a byte check.
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        raise CGIError('xml_encoding') from None
    if '\0' in text or '<!' in text:
        raise CGIError('xml_declaration_forbidden')
    parser = ET.XMLPullParser(events=('start', 'end'))
    depth = count = 0
    root = None
    try:
        for offset in range(0, len(text), 1024):
            parser.feed(text[offset:offset + 1024])
            for event, node in parser.read_events():
                if event == 'start':
                    root = root if root is not None else node
                    count += 1
                    depth += 1
                    if depth > MAX_DEPTH or count > MAX_NODES:
                        raise CGIError('xml_complexity')
                else:
                    depth -= 1
        parser.close()
    except ET.ParseError:
        raise CGIError('malformed_xml') from None
    if root is None or root.tag != 'envelope' or depth != 0:
        raise CGIError('invalid_xml_structure')
    body = _one(root, 'body')
    error = _text(body, 'error').strip()
    if not re.fullmatch(r'-?\d{1,8}', error):
        raise CGIError('invalid_error_code')
    if int(error) != 0:
        raise CGIError('device_rejected')
    return _one(body, 'content')


def streamkey_summary(data):
    content = parse_response(data)
    key = _text(content, 'key')
    if not key:
        raise CGIError('missing_streamkey')
    # Never return/store key, tdc or synctime in HA diagnostics or action output.
    return {'streamkey_received': True, 'authentication': 'cgi_accepted',
            'media_available': False}


def history_fields(start, end, channel):
    if type(channel) is not int or not 1 <= channel <= 64:
        raise CGIError('invalid_channel')
    try:
        if any(not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', value)
               for value in (start, end)):
            raise ValueError
        times = [datetime.strptime(value, '%Y-%m-%d %H:%M:%S') for value in (start, end)]
        if not 0 < (times[1] - times[0]).total_seconds() <= 86400:
            raise ValueError
    except (ValueError, TypeError):
        raise CGIError('invalid_time_range') from None
    return dict(channel=str(channel), filetype='picture', occurtype='all',
                # QvDateTime.parseXml appends literal lowercase t and z; it
                # performs no timezone conversion. Input is monitor wall time.
                starttime=times[0].strftime('%Y-%m-%dt%H:%M:%Sz'),
                endtime=times[1].strftime('%Y-%m-%dt%H:%M:%Sz'), stream='all')


def record_page(data):
    content = parse_response(data)
    pages = list(content.iter('page'))
    if len(pages) != 1 or len(pages[0]) or not re.fullmatch(r'\d{1,6}', pages[0].text or ''):
        raise CGIError('invalid_page')
    items = list(content.iter('data'))
    if len(items) > MAX_RECORDS:
        raise CGIError('record_limit')
    records = []
    for item in items:
        values = {name: _text(item, name) for name in
                  ('channel', 'filesize', 'idf', 'ids', 'idx', 'filetype', 'occurtype',
                   'starttime', 'endtime', 'filename')}
        if any(len(value) > 256 for value in values.values()):
            raise CGIError('invalid_record')
        records.append(values)
    return records, int(pages[0].text) != 0


async def _post(session, url, ssl, request):
    async with session.post(url, data=request, ssl=ssl, allow_redirects=False,
                            headers={'Content-Type': 'application/xml; charset=utf-8'}) as response:
        if response.status != 200:
            raise CGIError('http_rejected')
        if response.content_length is not None and response.content_length > MAX_XML:
            raise CGIError('xml_size')
        data = bytearray()
        async for chunk in response.content.iter_chunked(4096):
            data.extend(chunk)
            if len(data) > MAX_XML:
                raise CGIError('xml_size')
        return bytes(data)


async def read_device(host, auth_code, *, port=443, certificate_sha256='',
                      operation='access', start=None, end=None, channel=1,
                      include_details=False):
    """Owner-triggered read, no redirects, retries, persistent session or cookies."""
    if operation not in ('access', 'history'):
        raise CGIError('unsupported_operation')
    address = IPv4Address(host)
    if address.is_multicast or address.is_unspecified or int(address) == 0xffffffff:
        raise CGIError('invalid_address')
    if type(port) is not int or not 1 <= port <= 65535:
        raise CGIError('invalid_port')
    encode_auth_code(auth_code)
    ssl = True
    if certificate_sha256:
        if not re.fullmatch(r'[a-fA-F0-9]{64}', certificate_sha256):
            raise CGIError('invalid_certificate_fingerprint')
        ssl = aiohttp.Fingerprint(bytes.fromhex(certificate_sha256))
    fields = history_fields(start, end, channel) if operation == 'history' else None
    url = f'https://{address}:{port}/tdkcgi'
    async with asyncio.timeout(TIMEOUT):
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
                auto_decompress=False, connector=aiohttp.TCPConnector(force_close=True)) as session:
            if operation == 'access':
                data = await _post(session, url, ssl, envelope('get.device.streamkey', auth_code))
                return streamkey_summary(data)
            data = await _post(session, url, ssl, envelope('get.record.session', auth_code, fields))
            session_id = _text(_one(parse_response(data), 'record'), 'id')
            if not session_id or len(session_id) > 256:
                raise CGIError('invalid_record_session')
            records, seen = [], set()
            for page in range(MAX_PAGES):
                data = await _post(session, url, ssl,
                                   envelope('get.record.message', auth_code, {'id': session_id}))
                items, more = record_page(data)
                for record in items:
                    identity = tuple(record.values())
                    if identity not in seen:
                        seen.add(identity)
                        records.append(record)
                if len(records) > MAX_RECORDS:
                    raise CGIError('record_limit')
                if not more:
                    break
            return {'authentication': 'cgi_accepted', 'record_count': len(records),
                    'pages_read': page + 1, 'history_complete': not more,
                    'photo_download_available': False, 'ring_correlation_verified': False,
                    **({'records': records} if include_details else {})}
