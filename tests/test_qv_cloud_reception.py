"""QV subscriptions and MCS readiness are distinct; all peers are simulated."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_qv_cloud as existing
import test_qv_cloud_fcm as wire
from load_integration import load
from test_qv_cloud_events import payload

cloud = existing.cloud
fcm = load('v1_cloud_fcm')


class GatedReceiver(existing.Receiver):
    def __init__(self, owner):
        super().__init__(owner)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.disconnect = asyncio.Event()
        self.failure = None

    async def run(self):
        self.entered.set()
        await self.release.wait()
        if self.failure:
            raise fcm.FCMError(self.failure)
        self.connected = True
        self.owner._notify()
        self.ready.set()
        await self.disconnect.wait()
        self.connected = False
        self.owner._notify()
        raise fcm.FCMError('server_closed')

    def diagnostics(self):
        return {**super().diagnostics(), 'receive_stage': 'receiving' if self.connected else 'mcs_login',
            'receive_failure_stage': 'mcs_login' if self.failure else None,
            'receiver_ready': self.connected}


class ReceptionTests(unittest.IsolatedAsyncioTestCase):
    subscription_state = staticmethod(existing.QVControllerTests.subscription_state)
    fixture = existing.QVControllerTests.fixture
    running = existing.QVControllerTests.running
    sent = existing.QVControllerTests.sent

    async def gated(self, **kwargs):
        controller, _, client, calls, snapshots = self.fixture(**kwargs)
        receiver = GatedReceiver(controller)
        controller._receiver_factory.return_value = receiver
        self.addAsyncCleanup(controller.stop)
        return controller, receiver, client

    async def test_subscription_confirmation_never_claims_listening_before_mcs_login(self):
        for variant in ('connect3', 'connect2_r002'):
            controller, receiver, _ = await self.gated(variant=variant)
            changes = []
            controller._on_state = lambda: changes.append(controller.diagnostics())
            await controller.start()
            await asyncio.wait_for(receiver.entered.wait(), 1)
            pending = controller.diagnostics()
            self.assertEqual(pending['subscription_status'], 'confirmed')
            self.assertEqual(pending['status'], 'connecting')
            self.assertEqual(pending['reception_status'], 'not_connected')
            self.assertFalse(pending['connected'])
            self.assertTrue(all(value['status'] != 'listening' for value in changes))
            receiver.release.set()
            await asyncio.wait_for(receiver.ready.wait(), 1)
            self.assertEqual(changes[-1]['status'], 'listening')
            self.assertEqual(changes[-1]['reception_status'], 'waiting_for_message')
            self.assertTrue(changes[-1]['fcm']['receiver_ready'])
            receiver.disconnect.set()
            with patch.object(cloud, 'RECONNECT_DELAYS', (60, 60)):
                for _ in range(20):
                    if not controller.connected:
                        break
                    await asyncio.sleep(0)
            self.assertNotEqual(controller.diagnostics()['status'], 'listening')
            await controller.stop()
            self.assertFalse(controller.diagnostics()['fcm']['receiver_ready'])

    async def test_rejected_or_timed_out_login_is_specific_and_never_listening(self):
        for reason in ('login_rejected', 'login_timeout'):
            controller, receiver, _ = await self.gated()
            receiver.failure = reason
            changes = []
            controller._on_state = lambda: changes.append(controller.diagnostics())
            with patch.object(cloud, 'MAX_CONNECTION_ATTEMPTS', 1):
                await controller.start()
                await asyncio.wait_for(receiver.entered.wait(), 1)
                receiver.release.set()
                await asyncio.wait_for(controller._task, 1)
            report = controller.diagnostics()
            self.assertEqual(report['last_transport_error'], reason)
            self.assertEqual(report['last_error_stage'], 'fcm_login')
            self.assertEqual(report['status'], 'failed_reload_required')
            self.assertTrue(all(value['status'] != 'listening' for value in changes))
            controller._on_ring.assert_not_called()

    async def test_fake_without_callback_still_reports_truth_and_stop_is_final(self):
        controller, receiver, _, _, _ = await self.running()
        self.assertEqual(controller.diagnostics()['status'], 'listening')
        await controller.stop()
        receiver.connected = True  # A late, detached receiver cannot revive HA.
        controller._notify()
        report = controller.diagnostics()
        self.assertEqual(report['status'], 'stopped')
        self.assertEqual(report['reception_status'], 'inactive')

    async def test_payload_and_freshness_rejections_are_distinct_from_emitted_rings(self):
        controller, _, _, _, _ = await self.running()
        await controller._message(payload(DevUmid='OTHER_PRIVATE_UID'), 'wrong-device', self.sent())
        report = controller.diagnostics()
        self.assertEqual(report['reception_status'], 'message_rejected')
        self.assertEqual(report['last_message_disposition'], 'not_a_call_for_device')
        await controller._message(payload(), 'old-message', self.sent() - 180000)
        self.assertEqual(controller.diagnostics()['last_message_disposition'], 'expired_timestamp')
        controller._on_ring.assert_not_called()
        await controller._message(payload(), 'current-message', self.sent())
        report = controller.diagnostics()
        self.assertEqual(report['reception_status'], 'ring_received')
        self.assertEqual(report['last_message_disposition'], 'ring_emitted')
        self.assertEqual(report['notification_callbacks'], 3)
        self.assertEqual(report['rejected_notifications'], 2)
        self.assertEqual(report['rings_received'], 1)
        await controller._message(payload(), 'current-message', self.sent())
        self.assertEqual(controller.diagnostics()['last_message_disposition'], 'duplicate')
        controller._on_ring.assert_called_once_with(1)

    async def test_normalized_fcm_data_still_requires_identity_freshness_and_channel(self):
        controller, _, _, _, _ = await self.running()
        receiver = wire.QVFCMTests.receiver(self)
        receiver._writer = wire.legacy.Writer()
        receiver._on_notification = controller._message
        metadata = {'gcm.n.e': '0', 'gcm.n.title': 'PRIVATE_TITLE',
                    'notification': 'PRIVATE_DEVELOPER_TEXT'}
        now = self.sent()
        cases = [(payload(DevUmid='PRIVATE_OTHER_UID'), now, 'not_a_call_for_device'),
                 (payload(AlarmId='PRIVATE_OLD'), now - 180000, 'expired_timestamp'),
                 (payload(AlarmId='PRIVATE_CHANNEL', DevChNo='1'), now,
                  'channel_not_enabled_or_authorized'),
                 (payload(AlarmId='PRIVATE_CURRENT'), now, 'ring_emitted')]
        for index, (data, sent, expected) in enumerate(cases):
            message = wire.QVFCMTests.notification(self, {**data, **metadata}, sent_ms=sent)
            message.persistent_id = f'PRIVATE_TRANSPORT_{index}'
            await receiver._data_message(message)
            self.assertEqual(controller.diagnostics()['last_message_disposition'], expected)
        await receiver._data_message(message)
        controller._on_ring.assert_called_once_with(1)
        self.assertEqual(controller.diagnostics()['notification_callbacks'], 4)
        self.assertEqual(receiver.diagnostics()['delivered'], 4)
        self.assertEqual(receiver.diagnostics()['duplicates'], 1)
        self.assertEqual(receiver.diagnostics()['acknowledged'], 5)
        self.assertNotIn('PRIVATE', json.dumps(controller.diagnostics()))
        self.assertNotIn('PRIVATE', json.dumps(receiver.diagnostics()))

    async def test_storage_or_callback_failure_is_not_erased_by_connected_transport(self):
        for cause in ('storage', 'callback'):
            controller, _, _, _, _ = await self.running()
            saved_writer = controller._store.async_save
            if cause == 'storage':
                controller._store.async_save = AsyncMock()
            else:
                controller._on_ring.side_effect = RuntimeError('PRIVATE_REMOTE_BODY')
            await controller._message(payload(), 'failure-message', self.sent())
            controller._notify()
            report = controller.diagnostics()
            self.assertEqual(report['reception_status'], 'event_delivery_failed')
            self.assertEqual(report['last_error_stage'], 'event_storage' if cause == 'storage' else 'event_callback')
            self.assertEqual(report['rings_received'], 0)
            self.assertNotIn('PRIVATE_REMOTE_BODY', json.dumps(report))
            await controller._message(payload(DevUmid='OTHER_PRIVATE_UID'), 'irrelevant-message', self.sent())
            self.assertEqual(controller.diagnostics()['reception_status'], 'event_delivery_failed')
            controller._store.async_save = saved_writer
            controller._on_ring.side_effect = None
            await controller._message(payload(AlarmId='RECOVERED_ALARM'), 'recovered-message', self.sent())
            recovered = controller.diagnostics()
            self.assertEqual(recovered['reception_status'], 'ring_received')
            self.assertIsNone(recovered['last_error'])
            self.assertIsNone(recovered['last_error_stage'])

    async def test_transport_diagnostics_only_export_allowlisted_fields(self):
        controller, receiver, _, _, _ = await self.running()
        receiver.diagnostics = lambda: {'messages': 1, 'private': 'PRIVATE_TOKEN',
            'receive_stage': 'receiving', 'receiver_ready': True,
            'last_rejection_reason': 'PRIVATE_UID',
            'last_message_disposition': {'private': 'PRIVATE_PAYLOAD'},
            'last_payload_subreason': 'PRIVATE_VALUE', 'last_payload_branch': ['PRIVATE_BRANCH'],
            'last_payload_missing_fields': ['DevChNo', 'PRIVATE_KEY', {'private': 'PRIVATE_VALUE'}],
            'payload_rejection_counts': {'missing_required_fields': 2, 'PRIVATE_REASON': 1},
            'payload_branch_counts': {'house_QV': 1, 'PRIVATE_BRANCH': 2, 'HS': True},
            'payload_missing_field_counts': {'DevChNo': 2, 'DevUmid': 'PRIVATE_VALUE', 'PRIVATE_KEY': 1},
            'rejection_counts': {'sender_mismatch': 1, 'PRIVATE_UID': 5,
                                 'subtype_mismatch': 'PRIVATE_TOKEN'}}
        report = controller.diagnostics()
        self.assertEqual(report['fcm']['receive_stage'], 'receiving')
        self.assertEqual(report['fcm']['rejection_counts'], {'sender_mismatch': 1})
        self.assertEqual(report['fcm']['last_payload_missing_fields'], ['DevChNo'])
        self.assertEqual(report['fcm']['payload_rejection_counts'], {'missing_required_fields': 2})
        self.assertEqual(report['fcm']['payload_branch_counts'], {'house_QV': 1})
        self.assertEqual(report['fcm']['payload_missing_field_counts'], {'DevChNo': 2})
        report['fcm']['rejection_counts']['sender_mismatch'] = 999
        report['fcm']['payload_missing_field_counts']['DevChNo'] = 999
        report['fcm']['last_payload_missing_fields'].append('PRIVATE_FAKE')
        self.assertEqual(controller._transport_diag['rejection_counts']['sender_mismatch'], 1)
        self.assertEqual(controller._transport_diag['payload_missing_field_counts']['DevChNo'], 2)
        self.assertEqual(controller._transport_diag['last_payload_missing_fields'], ['DevChNo'])
        report = controller.diagnostics()
        self.assertEqual(report['reception_status'], 'message_rejected')
        self.assertNotIn('PRIVATE_', json.dumps(report))
        error = RuntimeError('PRIVATE_EXCEPTION')
        error.reason = 'PRIVATE_REASON'
        controller._error(error, 'fcm_receive')
        self.assertIsNone(controller.diagnostics()['last_transport_error'])


if __name__ == '__main__':
    unittest.main()
