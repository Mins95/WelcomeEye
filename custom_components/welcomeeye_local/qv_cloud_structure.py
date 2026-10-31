"""Bounded structural diagnostics only; never normalize or accept an event."""
import json

MAX_DEPTH = 3
MAX_NODES = 16
MAX_FIELDS = 64
MAX_JSON_STRING_BYTES = 16384
MAX_JSON_BYTES = 65536
REQUIRED_FIELDS = frozenset({'AlarmEvent', 'AlarmId', 'DevChNo', 'DevUmid'})
QV_FIELDS = REQUIRED_FIELDS | frozenset({
    'AlarmTime', 'AlarmState', 'AlarmSource', 'AlarmSourceName', 'AlarmInfo',
    'CameraId', 'CameraName', 'SensorChNo', 'NotReadCount', 'RecordResUrl',
    'RecordSubResUrl', 'sound', 'entranceKeyName', 'userHouseId', 'SrcAccountId',
    'DestName', 'SrcName', 'RequestType', 'DevInfo', 'AdditionalInfo', 'RequestTime',
    'clientNoticeId', 'releaseTime', 'siteId', 'message_content',
})
ENVELOPE_FIELDS = ('data', 'notification', 'message', 'payload', 'content', 'aps')
RESERVED_FIELDS = frozenset({
    'from', 'message_type', 'collapse_key', 'message_id', 'subtype', 'rawData',
    'content-encoding', 'crypto-key', 'encryption', 'gcm.rawData64',
    'google.sent_time', 'google.message_id', 'google.c.sender.id', 'google.to',
    'google.ttl', 'google.priority', 'google.original_priority',
    'google.delivered_priority', 'google.priority_reduced', 'google.c.a.e',
    'google.c.a.c_id', 'google.c.a.c_l', 'google.c.a.m_l', 'google.c.a.m_c',
    'google.c.a.ts', 'google.c.a.udt', 'gcm.n.e', 'gcm.n.title', 'gcm.n.body',
    'gcm.notification.e', 'gcm.notification.title', 'gcm.notification.body',
})
KNOWN_FIELDS = QV_FIELDS | RESERVED_FIELDS | frozenset(ENVELOPE_FIELDS)
_FIELD_ORDER = (*sorted(REQUIRED_FIELDS), *ENVELOPE_FIELDS,
                *sorted(QV_FIELDS - REQUIRED_FIELDS), *sorted(RESERVED_FIELDS))
_CASE_NAMES = {name.casefold(): name for name in KNOWN_FIELDS}


def _kind(value):
    return {dict: 'map', list: 'list', str: 'string', bool: 'boolean',
            int: 'integer', float: 'number', type(None): 'null', bytes: 'bytes'}.get(type(value), 'other')


def _json_candidate(value):
    # Inspect a bounded prefix only; do not copy or decode an arbitrary string.
    if type(value) is not str:
        return False
    for char in value[:MAX_JSON_STRING_BYTES]:
        if char not in ' \t\r\n':
            return char in '{['
    return False


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_json_key')
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError('invalid_json_constant')


def _too_deep(text, maximum):
    """Bound JSON container depth before the recursive standard decoder runs."""
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '{[':
            depth += 1
            if depth > maximum:
                return True
        elif char in '}]':
            depth -= 1
    return False


def summarize_payload_structure(data):
    """Return fresh JSON-safe facts, with no values or arbitrary field names.

    Map fields and array items share a 64-entry traversal budget. Only root,
    containers and possible JSON object/array strings consume the 16-node
    budget. Exact required/envelope/known keys take priority over unknown keys.
    JSON strings are inspected as structure only, never Base64 or event data.
    """
    report = dict(root_type=_kind(data), nodes=[], inspected_nodes=0,
        inspected_fields=0, json_strings_inspected=0, json_bytes_inspected=0,
        invalid_json_strings=0,
        limits=dict(depth=False, nodes=False, fields=False, json_bytes=False))
    active = set()

    def child(value, path, depth):
        if type(value) in (dict, list) or _json_candidate(value):
            visit(value, path, depth)

    def visit(value, path, depth):
        if depth > MAX_DEPTH:
            report['limits']['depth'] = True
            return
        if report['inspected_nodes'] >= MAX_NODES:
            report['limits']['nodes'] = True
            return
        kind = _kind(value)
        node = dict(path=path, type=kind)
        report['nodes'].append(node)
        report['inspected_nodes'] += 1
        if kind in ('map', 'list'):
            if id(value) in active:
                node['recursive'] = True
                return
            active.add(id(value))
            try:
                if kind == 'map':
                    node.update(field_count=min(len(value), MAX_FIELDS), inspected_fields=0,
                        known_fields={}, unknown_fields=0, qv_fields=[], case_variant_fields=[],
                        # Four fixed membership checks remain exact even if the
                        # shared budget prevents inspecting all field values.
                        missing_required_fields=sorted(name for name in REQUIRED_FIELDS
                                                       if name not in value),
                        fields_truncated=False)
                    selected = set()

                    def fields():
                        for key in _FIELD_ORDER:
                            if key in value:
                                selected.add(key)
                                yield key, value[key]
                        for key, item in value.items():
                            if type(key) is not str or key not in selected:
                                yield key, item

                    for key, item in fields():
                        if report['inspected_fields'] >= MAX_FIELDS:
                            break
                        index = node['inspected_fields']
                        node['inspected_fields'] += 1
                        report['inspected_fields'] += 1
                        name = key if type(key) is str and key in KNOWN_FIELDS else None
                        if name is not None:
                            node['known_fields'][name] = _kind(item)
                            if name in QV_FIELDS:
                                node['qv_fields'].append(name)
                        else:
                            node['unknown_fields'] += 1
                            canonical = (_CASE_NAMES.get(key.casefold())
                                         if type(key) is str and len(key) <= 128 else None)
                            if canonical is not None and canonical not in node['case_variant_fields']:
                                node['case_variant_fields'].append(canonical)
                        child(item, path + '.' + (name or f'field_{index}'), depth + 1)
                    node['fields_truncated'] = node['inspected_fields'] < len(value)
                    node['qv_fields'].sort()
                    node['case_variant_fields'].sort()
                    if node['fields_truncated']:
                        report['limits']['fields'] = True
                else:
                    node.update(item_count=min(len(value), MAX_FIELDS), inspected_items=0,
                                item_types={}, items_truncated=False)
                    for index, item in enumerate(value):
                        if report['inspected_fields'] >= MAX_FIELDS:
                            break
                        report['inspected_fields'] += 1
                        node['inspected_items'] += 1
                        item_kind = _kind(item)
                        node['item_types'][item_kind] = node['item_types'].get(item_kind, 0) + 1
                        child(item, f'{path}[{index}]', depth + 1)
                    node['items_truncated'] = node['inspected_items'] < len(value)
                    if node['items_truncated']:
                        report['limits']['fields'] = True
            finally:
                active.remove(id(value))
        elif kind == 'string':
            node['json_candidate'] = _json_candidate(value)
            if not node['json_candidate']:
                return
            if depth >= MAX_DEPTH:
                report['limits']['depth'] = True
                node['json_status'] = 'depth_limit'
                return
            if len(value) > MAX_JSON_STRING_BYTES:
                report['limits']['json_bytes'] = True
                node['json_status'] = 'byte_limit'
                return
            try:
                size = len(value.encode('utf-8'))
            except UnicodeError:
                node['json_status'] = 'invalid_utf8'
                report['invalid_json_strings'] += 1
                return
            if (size > MAX_JSON_STRING_BYTES or
                    report['json_bytes_inspected'] + size > MAX_JSON_BYTES):
                report['limits']['json_bytes'] = True
                node['json_status'] = 'byte_limit'
                return
            report['json_strings_inspected'] += 1
            report['json_bytes_inspected'] += size
            if _too_deep(value, MAX_DEPTH - depth):
                report['limits']['depth'] = True
                node['json_status'] = 'depth_limit'
                return
            try:
                decoded = json.loads(value, object_pairs_hook=_unique_object,
                                     parse_constant=_invalid_constant)
            except (ValueError, RecursionError):
                node['json_status'] = 'malformed'
                report['invalid_json_strings'] += 1
                return
            node['json_status'] = 'decoded'
            visit(decoded, path + '.json', depth + 1)

    visit(data, '$', 0)
    return report
