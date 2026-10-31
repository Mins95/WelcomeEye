"""Real FCM framing/crypto with synthetic data; structural diagnostics only."""
import json
from copy import deepcopy
import unittest
from unittest.mock import patch

from load_integration import load
import test_v1_cloud_fcm as legacy
import test_qv_cloud_fcm as cloud_tests
import test_qv_cloud as controller_tests
from test_qv_cloud_events import NOW, payload

qv = load('qv_cloud_fcm')
cloud = load('qv_cloud')


class PayloadStructureTests(unittest.IsolatedAsyncioTestCase):
    receiver = cloud_tests.QVFCMTests.receiver
    notification = cloud_tests.QVFCMTests.notification
    run_frames = legacy.FCMTests.run_frames
    acknowledgements = legacy.FCMTests.acknowledgements
    fixture = controller_tests.QVControllerTests.fixture
    subscription_state = staticmethod(controller_tests.QVControllerTests.subscription_state)

    @staticmethod
    def node(shape, path):
        return next(node for node in shape['nodes'] if node['path'] == path)

    def assert_private(self, diag):
        encoded = json.dumps(diag)
        for value in ('PRIVATE', 'private-installation', str(NOW),
                      payload()['DevUmid'], payload()['AlarmId']):
            self.assertNotIn(value, encoded)

    async def test_metadata_only_message_locates_all_four_missing_fields(self):
        for variant in ('connect3', 'connect2_r002'):
            with self.subTest(variant=variant):
                receiver = self.receiver(variant)
                message = self.notification({'google.other': 'PRIVATE_METADATA',
                    'PRIVATE_UNKNOWN_KEY': 'PRIVATE_VALUE'})
                writer, _ = await self.run_frames(receiver, (8, message))
                receiver._on_notification.assert_not_called()
                self.assertEqual(self.acknowledgements(writer), [message.persistent_id])
                diag = receiver.diagnostics()
                self.assertEqual(diag['last_payload_subreason'], 'missing_required_fields')
                self.assertEqual(len(diag['last_payload_missing_fields']), 4)
                self.assertEqual(diag['last_payload_wire']['transport'], 'android_data')
                self.assertEqual(set(diag['last_payload_shapes']),
                                 {'transport_data', 'parser_input'})
                root = self.node(diag['last_payload_shapes']['parser_input'], '$')
                self.assertEqual(len(root['missing_required_fields']), 4)
                self.assertEqual(root['unknown_fields'], 2)
                self.assert_private(diag)

    async def test_nested_json_fields_observed_without_accepting_unproven_envelope(self):
        for variant in ('connect3', 'connect2_r002'):
            for encoding in ('aesgcm', 'aes128gcm'):
                with self.subTest(variant=variant, encoding=encoding):
                    receiver = self.receiver(variant)
                    content = {'data': {'payload': json.dumps(payload()),
                                        'PRIVATE_UNKNOWN': 'PRIVATE_TEXT'}}
                    message = legacy.FCMTests.encrypted(self, receiver, content, encoding=encoding)
                    writer, _ = await self.run_frames(receiver, (8, message))
                    receiver._on_notification.assert_not_called()
                    self.assertEqual(self.acknowledgements(writer), [message.persistent_id])
                    diag = receiver.diagnostics()
                    self.assertEqual(diag['last_payload_wire']['transport'], 'encrypted_web')
                    self.assertEqual(set(diag['last_payload_shapes']),
                                     {'transport_data', 'decrypted_envelope', 'parser_input'})
                    root = self.node(diag['last_payload_shapes']['parser_input'], '$')
                    nested = self.node(diag['last_payload_shapes']['parser_input'], '$.payload.json')
                    self.assertEqual(len(root['missing_required_fields']), 4)
                    self.assertEqual(nested['missing_required_fields'], [])
                    self.assertEqual(diag['last_payload_subreason'], 'missing_required_fields')
                    self.assert_private(diag)

    async def test_fields_outside_selected_data_are_visible_before_extraction(self):
        receiver = self.receiver()
        message = legacy.FCMTests.encrypted(self, receiver,
            {**payload(), 'data': {'PRIVATE_OTHER': 'PRIVATE_VALUE'}})
        await self.run_frames(receiver, (8, message))
        receiver._on_notification.assert_not_called()
        diag = receiver.diagnostics()
        before = self.node(diag['last_payload_shapes']['decrypted_envelope'], '$')
        after = self.node(diag['last_payload_shapes']['parser_input'], '$')
        self.assertEqual(before['missing_required_fields'], [])
        self.assertEqual(len(after['missing_required_fields']), 4)
        self.assert_private(diag)

    async def test_valid_payload_delivery_and_ack_are_unchanged(self):
        for encoding in ('plain', 'aesgcm', 'aes128gcm'):
            receiver = self.receiver()
            message = (self.notification() if encoding == 'plain' else
                legacy.FCMTests.encrypted(self, receiver, {'data': payload()}, encoding=encoding))
            message.sent = NOW * 1000
            writer, _ = await self.run_frames(receiver, (8, message))
            receiver._on_notification.assert_called_once_with(payload(), message.persistent_id, NOW * 1000)
            self.assertEqual(self.acknowledgements(writer), [message.persistent_id])
            diag = receiver.diagnostics()
            self.assertEqual(diag['recent_payload_rejections'], [])
            self.assertEqual(self.node(diag['last_payload_shapes']['parser_input'], '$')
                             ['missing_required_fields'], [])
            self.assert_private(diag)

    async def test_three_rejections_survive_duplicates_and_are_independent_copies(self):
        receiver = self.receiver()
        messages = []
        for index in range(4):
            message = self.notification({'data': json.dumps(payload()), 'PRIVATE_KEY': 'PRIVATE_VALUE'})
            message.persistent_id = f'PRIVATE_EVENT_{index}'
            messages.append((8, message))
        writer, _ = await self.run_frames(receiver, *messages, messages[-1])
        receiver._on_notification.assert_not_called()
        self.assertEqual(len(self.acknowledgements(writer)), 5)
        diag = receiver.diagnostics()
        history = diag['recent_payload_rejections']
        self.assertEqual([item['message_number'] for item in history], [2, 3, 4])
        self.assertEqual(diag['last_message_disposition'], 'duplicate')
        self.assertEqual(diag['last_payload_shapes'], {})
        self.assertIsNone(diag['last_payload_wire'])
        self.assertTrue(history[-1]['stages']['parser_input']['nodes'])
        self.assert_private(diag)
        history[-1]['stages']['parser_input']['nodes'].clear()
        self.assertTrue(receiver.diagnostics()['recent_payload_rejections'][-1]
                        ['stages']['parser_input']['nodes'])

    async def test_inspection_failure_cannot_change_notification_delivery(self):
        receiver = self.receiver()
        message = self.notification()
        with patch.object(qv, 'summarize_payload_structure', side_effect=ValueError('PRIVATE_ERROR')):
            writer, _ = await self.run_frames(receiver, (8, message))
        receiver._on_notification.assert_called_once_with(payload(), message.persistent_id, NOW * 1000)
        self.assertEqual(self.acknowledgements(writer), [message.persistent_id])
        diag = receiver.diagnostics()
        self.assertEqual(diag['last_payload_shapes']['parser_input'], {'status': 'inspection_failed'})
        self.assert_private(diag)

    async def test_sender_and_subtype_guards_precede_shape_inspection(self):
        for kind in ('sender', 'subtype'):
            receiver = self.receiver()
            if kind == 'sender':
                message = self.notification()
                setattr(message, 'from', 'PRIVATE_WRONG_SENDER')
            else:
                message = legacy.FCMTests.encrypted(self, receiver, {'data': payload()})
                next(item for item in message.app_data if item.key == 'subtype').value = 'PRIVATE_WRONG_SUBTYPE'
            with patch.object(qv, 'summarize_payload_structure') as inspect:
                writer, _ = await self.run_frames(receiver, (8, message))
            inspect.assert_not_called()
            receiver._on_notification.assert_not_called()
            self.assertEqual(self.acknowledgements(writer), [message.persistent_id])
            self.assertEqual(receiver.diagnostics()['last_payload_shapes'], {})
            self.assert_private(receiver.diagnostics())

    async def test_notification_and_malformed_json_remain_rejected_without_values(self):
        for kind in ('notification', 'malformed'):
            receiver = self.receiver()
            message = legacy.FCMTests.encrypted(self, receiver,
                {'notification': {'title': 'PRIVATE_TITLE'}, 'data': payload()},
                raw_json=b'{"PRIVATE_BROKEN":' if kind == 'malformed' else None)
            writer, _ = await self.run_frames(receiver, (8, message))
            receiver._on_notification.assert_not_called()
            self.assertEqual(self.acknowledgements(writer), [message.persistent_id])
            diag = receiver.diagnostics()
            self.assertEqual(diag['last_rejection_reason'],
                'malformed_payload' if kind == 'malformed' else 'qv_payload_rejected')
            self.assertEqual(diag['last_payload_subreason'],
                None if kind == 'malformed' else 'generic_notification')
            if kind == 'malformed':
                self.assertEqual(set(diag['last_payload_shapes']), {'transport_data'})
            self.assert_private(diag)

    async def test_structure_reaches_downloadable_controller_diagnostics_for_both_models(self):
        for variant in ('connect3', 'connect2_r002'):
            controller, _, client, _, _ = self.fixture(variant=variant)
            receiver = self.receiver(variant)
            controller._receiver = receiver
            receiver._on_notification = controller._message
            message = legacy.FCMTests.encrypted(self, receiver,
                {'data': {'payload': json.dumps(payload())}})
            await self.run_frames(receiver, (8, message))
            report = controller.diagnostics()
            fcm = report['fcm']
            self.assertEqual(fcm['payload_structure_version'], 1)
            self.assertEqual(fcm['last_payload_shapes'], receiver.diagnostics()['last_payload_shapes'])
            self.assertEqual(len(fcm['recent_payload_rejections']), 1)
            self.assertEqual(self.node(fcm['last_payload_shapes']['parser_input'], '$.payload.json')
                             ['missing_required_fields'], [])
            controller._on_ring.assert_not_called()
            client.async_register.assert_not_awaited()
            client.async_subscribe.assert_not_awaited()
            self.assert_private(report)
            fcm['recent_payload_rejections'][0]['stages'].clear()
            self.assertTrue(controller.diagnostics()['fcm']['recent_payload_rejections'][0]['stages'])

    async def test_ha_boundary_revalidates_shapes_and_filters_private_receiver_data(self):
        controller, receiver, _, _, _ = self.fixture()
        controller._receiver = receiver
        good = qv.summarize_payload_structure({'data': json.dumps(payload())})
        cases = []
        for change in (
                lambda value: value.update(PRIVATE_KEY='PRIVATE_VALUE'),
                lambda value: value['nodes'][0].update(path='$.PRIVATE_KEY'),
                lambda value: value['nodes'][0].update(type='PRIVATE_VALUE'),
                lambda value: value['nodes'][0]['known_fields'].update(data='PRIVATE_VALUE'),
                lambda value: value.update(nodes=[{}] * 17),
                lambda value: value.update(inspected_fields=-1)):
            bad = deepcopy(good)
            change(bad)
            cases.append(bad)
        recursive = {}
        recursive['nodes'] = [recursive]
        cases.append(recursive)
        for bad in cases:
            receiver.diagnostics = lambda: {'payload_structure_version': 1,
                'last_payload_wire': {'transport': 'PRIVATE_VALUE', 'raw_data_present': True,
                                     'PRIVATE_TOKEN': 'PRIVATE_VALUE'},
                'last_payload_shapes': {'parser_input': bad, 'PRIVATE_STAGE': good},
                'recent_payload_rejections': [
                    {'message_number': 1, 'reason': 'qv_payload_rejected',
                     'subreason': 'PRIVATE_REASON', 'stages': {'parser_input': bad}},
                    {'message_number': 2, 'reason': 'PRIVATE_REASON', 'stages': {'parser_input': good}}]}
            report = controller.diagnostics()
            fcm = report['fcm']
            self.assertEqual(fcm['last_payload_shapes'],
                             {'parser_input': {'status': 'invalid_structure'}})
            self.assertEqual(fcm['last_payload_wire'], {'raw_data_present': True})
            self.assertEqual(len(fcm['recent_payload_rejections']), 1)
            self.assertIsNone(fcm['recent_payload_rejections'][0]['subreason'])
            self.assert_private(report)


if __name__ == '__main__':
    unittest.main()
