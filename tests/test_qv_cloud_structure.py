"""Private payload structures are synthetic and never become accepted events."""
import base64
import json
import unittest
from unittest.mock import patch

from load_integration import load

structure = load('qv_cloud_structure')


def payload():
    return dict(AlarmEvent='PRIVATE_EVENT', AlarmId='PRIVATE_ALARM',
                DevChNo='PRIVATE_CHANNEL', DevUmid='PRIVATE_UID')


class StructureTests(unittest.TestCase):
    def report(self, value):
        result = structure.summarize_payload_structure(value)
        encoded = json.dumps(result, allow_nan=False)
        self.assertNotIn('PRIVATE', encoded)
        self.assertEqual(result['inspected_nodes'], len(result['nodes']))
        self.assertLessEqual(result['inspected_nodes'], 16)
        self.assertLessEqual(result['inspected_fields'], 64)
        self.assertLessEqual(result['json_bytes_inspected'], 65536)
        self.assertLessEqual(result['json_strings_inspected'], 16)
        self.assertLessEqual(result['invalid_json_strings'], 16)
        return result

    def test_root_qv_map_reports_names_types_only_and_missing_four(self):
        result = self.report(payload())
        root = result['nodes'][0]
        self.assertEqual(result['root_type'], 'map')
        self.assertEqual(root['path'], '$')
        self.assertEqual(root['known_fields'], dict.fromkeys(payload(), 'string'))
        self.assertEqual(root['missing_required_fields'], [])
        self.assertEqual(root['qv_fields'], sorted(payload()))
        rejected = self.report({'google.other': 'PRIVATE_METADATA',
                                'PRIVATE_UNKNOWN_KEY': 'PRIVATE_CONTENT'})['nodes'][0]
        self.assertEqual(rejected['unknown_fields'], 2)
        self.assertEqual(rejected['missing_required_fields'], sorted(payload()))

    def test_nested_object_and_json_string_envelopes_are_distinct(self):
        for envelope in structure.ENVELOPE_FIELDS:
            for encoded in (False, True):
                with self.subTest(envelope=envelope, encoded=encoded):
                    value = json.dumps(payload()) if encoded else payload()
                    result = self.report({envelope: value})
                    path = f'$.{envelope}' + ('.json' if encoded else '')
                    nested = next(node for node in result['nodes'] if node['path'] == path)
                    self.assertEqual(nested['type'], 'map')
                    self.assertEqual(nested['missing_required_fields'], [])
                    self.assertEqual(result['json_strings_inspected'], int(encoded))

    def test_arrays_report_types_and_sanitized_positions(self):
        result = self.report([payload(), 'PRIVATE_TEXT', None, True, 123456789,
                              3.14159265, b'PRIVATE_BYTES', [payload()]])
        root = result['nodes'][0]
        self.assertEqual(root['item_types'], {'map': 1, 'string': 1, 'null': 1,
            'boolean': 1, 'integer': 1, 'number': 1, 'bytes': 1, 'list': 1})
        paths = {node['path'] for node in result['nodes']}
        self.assertIn('$[0]', paths)
        self.assertIn('$[7][0]', paths)
        self.assertNotIn('123456789', json.dumps(result))
        self.assertNotIn('3.14159265', json.dumps(result))

    def test_unknown_names_and_case_variants_never_escape(self):
        value = {'PRIVATE_KEY.[0]/secret': {'PRIVATE_NESTED': json.dumps(payload())},
                 'ALARMEVENT': 'PRIVATE_EVENT', 'devumid': 'PRIVATE_UID',
                 'google.PRIVATE_RESERVED': 'PRIVATE_TOKEN',
                 'PRIVATE_' * 10000: {'AlarmId': 'PRIVATE_VALUE'}}
        result = self.report(value)
        root = result['nodes'][0]
        self.assertEqual(root['case_variant_fields'], ['AlarmEvent', 'DevUmid'])
        self.assertEqual(root['unknown_fields'], 5)
        self.assertNotIn('ALARMEVENT', json.dumps(result))
        self.assertNotIn('devumid', json.dumps(result))
        self.assertTrue(any(node['path'].startswith('$.field_') for node in result['nodes'][1:]))
        self.assertTrue(any(node.get('qv_fields') == sorted(payload()) for node in result['nodes']))

    def test_reserved_keys_are_exact_and_values_remain_private(self):
        data = {name: 'PRIVATE_RESERVED_VALUE' for name in (
            'google.sent_time', 'gcm.n.e', 'gcm.notification.e', 'rawData',
            'gcm.rawData64', 'crypto-key', 'encryption', 'subtype')}
        root = self.report(data)['nodes'][0]
        self.assertEqual(root['known_fields'], dict.fromkeys(data, 'string'))
        self.assertEqual(root['unknown_fields'], 0)

    def test_priority_finds_envelope_after_many_unknown_fields(self):
        data = {f'PRIVATE_UNKNOWN_{index}': 'PRIVATE_VALUE' for index in range(500)}
        data['payload'] = json.dumps(payload())
        result = self.report(data)
        nested = next(node for node in result['nodes'] if node['path'] == '$.payload.json')
        self.assertEqual(nested['missing_required_fields'], [])
        self.assertEqual(result['inspected_fields'], 64)
        self.assertEqual(result['nodes'][0]['field_count'], 64)
        self.assertTrue(result['limits']['fields'])
        # Exhausted inspection budget must not falsely claim present fields absent.
        clipped = self.report([None] * 63 + [payload()])
        nested = clipped['nodes'][-1]
        self.assertTrue(nested['fields_truncated'])
        self.assertEqual(nested['missing_required_fields'], [])

    def test_depth_nodes_fields_and_recursive_objects_are_bounded(self):
        value = {'data': {'payload': {'content': {'message': payload()}}}}
        deep = self.report(value)
        self.assertTrue(deep['limits']['depth'])
        self.assertTrue(all(node['path'].count('.') <= 3 for node in deep['nodes']))
        many = self.report([{} for _ in range(1000)])
        self.assertEqual(many['inspected_nodes'], 16)
        self.assertEqual(many['inspected_fields'], 64)
        self.assertTrue(many['limits']['nodes'])
        self.assertTrue(many['limits']['fields'])
        recursive = {}
        recursive['data'] = recursive
        result = self.report(recursive)
        self.assertEqual(result['inspected_nodes'], 2)
        self.assertTrue(result['nodes'][1]['recursive'])

    def test_json_depth_is_checked_before_recursive_decoder(self):
        with patch.object(structure.json, 'loads', side_effect=AssertionError('decoder must not run')):
            result = self.report('[' * 2000 + ']' * 2000)
        self.assertTrue(result['limits']['depth'])
        self.assertEqual(result['nodes'][0]['json_status'], 'depth_limit')
        # Braces and escaped quotes in a JSON value are not nested containers.
        result = self.report(json.dumps({'AlarmInfo': '{[\\"PRIVATE_VALUE"\\]}' }))
        self.assertEqual(result['nodes'][0]['json_status'], 'decoded')

    def test_json_string_and_total_byte_limits(self):
        large = self.report('{"data":"' + 'x' * 16384 + '"}')
        self.assertEqual(large['json_bytes_inspected'], 0)
        self.assertTrue(large['limits']['json_bytes'])
        multibyte = self.report('{"data":"' + '\U0001f600' * 5000 + '"}')
        self.assertTrue(multibyte['limits']['json_bytes'])
        value = json.dumps({'PRIVATE_KEY': 'x' * 15900})
        total = self.report({name: value for name in structure.ENVELOPE_FIELDS})
        self.assertTrue(total['limits']['json_bytes'])
        self.assertEqual(total['json_strings_inspected'], 4)
        self.assertEqual(total['json_bytes_inspected'], len(value.encode()) * 4)

    def test_invalid_json_duplicates_unicode_and_recursion_fail_privately(self):
        for value in ('{PRIVATE_BAD', '{"PRIVATE_KEY":1,"PRIVATE_KEY":2}',
                      '{"data":NaN}', '{"data":"\ud800"}'):
            result = self.report(value)
            self.assertEqual(result['invalid_json_strings'], 1)
            self.assertIn(result['nodes'][0]['json_status'], ('malformed', 'invalid_utf8'))
        with patch.object(structure.json, 'loads', side_effect=RecursionError('PRIVATE_EXCEPTION')):
            result = self.report('{}')
        self.assertEqual(result['invalid_json_strings'], 1)

    def test_scalar_types_and_base64_are_never_interpreted_as_events(self):
        for value, kind in ((None, 'null'), (True, 'boolean'), (123456789, 'integer'),
                            (float('nan'), 'number'), (b'PRIVATE_BYTES', 'bytes'),
                            (('PRIVATE_VALUE',), 'other')):
            result = self.report(value)
            self.assertEqual(result['root_type'], kind)
            self.assertEqual(result['nodes'], [{'path': '$', 'type': kind}])
        encoded = base64.b64encode(json.dumps(payload()).encode()).decode()
        result = self.report(encoded)
        self.assertEqual(result['json_strings_inspected'], 0)
        self.assertEqual(len(result['nodes']), 1)
        self.assertNotIn(encoded, json.dumps(result))

    def test_reports_do_not_mutate_or_retain_input_and_are_independent(self):
        source = {'payload': payload()}
        original = json.loads(json.dumps(source))
        first = self.report(source)
        self.assertEqual(source, original)
        first['nodes'][0]['known_fields']['payload'] = 'PRIVATE_MUTATION'
        first['limits']['fields'] = True
        second = self.report(source)
        self.assertEqual(second['nodes'][0]['known_fields']['payload'], 'map')
        self.assertFalse(second['limits']['fields'])
        source['payload'].clear()
        self.assertEqual(second['nodes'][1]['missing_required_fields'], [])


if __name__ == '__main__':
    unittest.main()
