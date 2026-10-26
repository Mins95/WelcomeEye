"""QV profile/wire regression tests. All network I/O is replaced with fakes."""
from dataclasses import FrozenInstanceError, replace
import asyncio
import json
import ssl
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
import test_v1_cloud_fcm as legacy
from test_qv_cloud_events import NOW, UID, payload

fcm = load('v1_cloud_fcm')
qv = load('qv_cloud_fcm')
events = load('qv_cloud_events')


class QVFCMTests(unittest.IsolatedAsyncioTestCase):
    def receiver(self, variant='connect3', *, saved=True):
        session = Mock()
        session.post.side_effect = AssertionError('No live HTTP permitted')
        receiver = qv.QVFCMReceiver(session, Mock(), AsyncMock(), variant=variant)
        receiver._ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if saved:
            receiver._credentials = legacy.credentials()
            receiver._credentials['config'] = {
                'bundle_id': receiver._profile.bundle_id,
                'project_id': receiver._profile.project_id,
                'app_id': receiver._profile.app_id,
            }
        return receiver

    def notification(self, data=None, *, sent_ms=NOW * 1000):
        message = legacy.notification(content=None, sent=sent_ms)
        for key, value in (payload() if data is None else data).items():
            message.app_data.add(key=key, value=value)
        return message

    def test_explicit_immutable_apk_profiles_and_unchanged_v1_default(self):
        self.assertEqual(fcm.DEFAULT_PROFILE.app_id, '1:1004874260689:android:74d7f81b899910c7')
        self.assertEqual(fcm.DEFAULT_PROFILE.bundle_id, 'welcomeeye.homeassistant')
        self.assertEqual(qv.DOOR_CONNECT_PROFILE.app_id,
                         '1:1004874260689:android:8c49e83613eafdc495e5f0')
        self.assertEqual(qv.WELCOMEEYE_QV_PROFILE.app_id, fcm.DEFAULT_PROFILE.app_id)
        self.assertNotEqual(qv.WELCOMEEYE_QV_PROFILE.bundle_id, fcm.DEFAULT_PROFILE.bundle_id)
        self.assertEqual(self.receiver('connect2_r002')._profile, qv.WELCOMEEYE_QV_PROFILE)
        with self.assertRaises(FrozenInstanceError):
            qv.DOOR_CONNECT_PROFILE.project_id = 'wrong'
        with self.assertRaisesRegex(ValueError, '^unsupported_qv_variant$'):
            self.receiver('connect_v1')

    async def test_plain_qv_callback_shape_ack_dedup_and_privacy(self):
        receiver = self.receiver()
        receiver._writer = legacy.Writer()
        receiver._on_notification = Mock()
        message = self.notification()
        await receiver._data_message(message)
        await receiver._data_message(message)
        receiver._on_notification.assert_called_once_with(payload(), 'PRIVATE_ID', NOW * 1000)
        self.assertEqual(receiver.diagnostics()['acknowledged'], 2)
        self.assertEqual(receiver.diagnostics()['duplicates'], 1)
        self.assertNotIn('PRIVATE', json.dumps(receiver.diagnostics()))

    def test_qv_and_lt_payload_parsers_are_isolated(self):
        receiver = self.receiver()
        self.assertIsNone(receiver._payload(legacy.notification()))
        v1 = fcm.FCMReceiver(Mock(), Mock(), Mock(), credentials=legacy.credentials())
        self.assertIsNone(v1._payload(self.notification()))
        self.assertEqual(v1._payload(legacy.notification())[0], {'message_content': legacy.CONTENT})

    def test_sender_is_from_selected_profile(self):
        receiver = self.receiver()
        receiver._profile = replace(receiver._profile, sender_id='other-public-sender')
        self.assertIsNone(receiver._payload(self.notification()))

    def test_encrypted_qv_payload_keeps_authenticated_timestamp(self):
        receiver = self.receiver()
        for encoding in ('aesgcm', 'aes128gcm'):
            message = legacy.FCMTests.encrypted(self, receiver,
                {'google.sent_time': NOW * 1000, 'data': payload()}, encoding=encoding)
            message.sent = NOW * 1000
            self.assertEqual(receiver._payload(message), (payload(), NOW * 1000))
            message.sent += 1
            self.assertEqual(receiver._payload(message)[1], 0)
            self.assertEqual(receiver.diagnostics()['timestamp_status'], 'conflicting')

    def test_payload_time_cannot_override_missing_transport_time(self):
        receiver = self.receiver()
        message = legacy.FCMTests.encrypted(self, receiver,
            {'data': {**payload(), 'google.sent_time': str(NOW * 1000)}})
        message.ClearField('sent')
        data, timestamp = receiver._payload(message)
        self.assertEqual(timestamp, 0)
        filt = events.QVEventFilter(UID, now=lambda: NOW)
        self.assertIsNone(filt.accept(data, 'new-id', timestamp))

    def test_encrypted_or_plain_generic_notifications_never_become_calls(self):
        receiver = self.receiver()
        for data in ({'notification': {'title': 'Call'}, 'data': payload()},
                     {'data': {**payload(), 'gcm.n.e': '1'}},
                     {'data': {'title': 'Ring'}}, {'data': {**payload(), 'CameraId': 'hs'}}):
            message = legacy.FCMTests.encrypted(self, receiver, data)
            self.assertIsNone(receiver._payload(message))
        self.assertIsNone(receiver._payload(self.notification({**payload(), 'gcm.n.e': '1'})))

    def test_decrypted_duplicates_and_subtype_mismatch_are_rejected(self):
        receiver = self.receiver()
        message = legacy.FCMTests.encrypted(self, receiver, {},
            raw_json=b'{"data":{"AlarmEvent":"19","AlarmEvent":"2"}}')
        with self.assertRaises(fcm.FCMError):
            receiver._payload(message)
        message = legacy.FCMTests.encrypted(self, receiver, {'data': payload()})
        next(item for item in message.app_data if item.key == 'subtype').value = 'other-installation'
        self.assertIsNone(receiver._payload(message))

    async def test_cross_profile_saved_credentials_fail_before_network(self):
        receiver = self.receiver('connect2_r002')
        receiver._credentials = self.receiver('connect3').credentials
        with self.assertRaisesRegex(fcm.FCMError, '^invalid_credentials$'):
            await receiver.register()
        receiver._session.post.assert_not_called()
        receiver._on_credentials.assert_not_called()

    async def test_both_profiles_enroll_with_exact_public_app_id_and_private_bundle(self):
        for variant in ('connect3', 'connect2_r002'):
            receiver = self.receiver(variant, saved=False)
            checkin = fcm.AndroidCheckinResponse(stats_ok=True, android_id=123, security_token=456)
            receiver._session.post.side_effect = [legacy.Response(checkin.SerializeToString()),
                legacy.Response(b'token=TEST_GCM'),
                legacy.Response(json.dumps({'fid': 'test-install', 'refreshToken': 'TEST_REFRESH',
                    'authToken': {'token': 'TEST_AUTH', 'expiresIn': '604800s'}}).encode()),
                legacy.Response(b'{"token":"TEST_FCM"}')]
            self.assertEqual(await receiver.register(), 'TEST_FCM')
            profile = receiver._profile
            calls = receiver._session.post.call_args_list
            self.assertEqual(calls[2].kwargs['json']['appId'], profile.app_id)
            self.assertEqual(calls[2].kwargs['headers']['x-goog-api-key'], profile.api_key)
            self.assertIn(f'projects/{profile.project_id}/installations', calls[2].args[0])
            self.assertTrue(calls[1].kwargs['data']['X-subtype'].startswith(f'wp:{profile.bundle_id}#'))
            self.assertEqual(receiver.credentials['config']['app_id'], profile.app_id)
            receiver._on_credentials.assert_awaited_once()
            self.assertEqual(receiver.diagnostics()['receive_stage'], 'not_started')
            self.assertFalse(receiver.diagnostics()['receiver_ready'])

    async def test_readiness_waits_for_accepted_mcs_login_and_keeps_failure_phase(self):
        for variant in ('connect3', 'connect2_r002'):
            receiver = self.receiver(variant)
            reader, writer = asyncio.StreamReader(), legacy.Writer()
            states = []
            receiver._on_state = lambda: states.append(receiver.diagnostics())
            self.assertEqual(receiver.diagnostics()['receive_stage'], 'not_started')
            with patch.object(fcm.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))):
                task = asyncio.create_task(receiver.run())
                try:
                    async with asyncio.timeout(1):
                        while not writer.packets:
                            await asyncio.sleep(0)
                    waiting = receiver.diagnostics()
                    self.assertEqual(waiting['receive_stage'], 'mcs_login')
                    self.assertFalse(waiting['receiver_ready'])
                    self.assertEqual(states, [])
                    reader.feed_data(legacy.frame(3, fcm.LoginResponse(id='login'), first=True))
                    async with asyncio.timeout(1):
                        while not receiver.connected:
                            await asyncio.sleep(0)
                    self.assertEqual(states[-1]['receive_stage'], 'receiving')
                    self.assertTrue(states[-1]['receiver_ready'])
                    reader.feed_eof()
                    with self.assertRaises(fcm.FCMError):
                        await task
                    failed = receiver.diagnostics()
                    self.assertEqual(failed['receive_stage'], 'failed')
                    self.assertEqual(failed['receive_failure_stage'], 'receiving')
                    self.assertFalse(failed['receiver_ready'])
                finally:
                    await receiver.close()
                    await asyncio.gather(task, return_exceptions=True)
                self.assertEqual(receiver.diagnostics()['receive_stage'], 'closed')
                self.assertEqual(receiver.diagnostics()['receive_failure_stage'], 'receiving')

    async def test_login_rejection_and_tcp_timeout_never_report_receiving(self):
        for failure in ('connect', 'login'):
            receiver = self.receiver()
            reader, writer = asyncio.StreamReader(), legacy.Writer()
            if failure == 'login':
                reader.feed_data(legacy.frame(0, fcm.HeartbeatPing(), first=True))
            connector = (AsyncMock(side_effect=TimeoutError()) if failure == 'connect'
                         else AsyncMock(return_value=(reader, writer)))
            with patch.object(fcm.asyncio, 'open_connection', connector):
                with self.assertRaises(fcm.FCMError):
                    await receiver.run()
            diag = receiver.diagnostics()
            self.assertEqual(diag['receive_stage'], 'failed')
            self.assertEqual(diag['receive_failure_stage'], 'tls_connect' if failure == 'connect' else 'mcs_login')
            self.assertFalse(diag['receiver_ready'])
            self.assertEqual(diag['messages'], 0)
            await receiver.close()

    async def test_close_during_login_keeps_not_ready_and_drains_receiver(self):
        receiver = self.receiver()
        reader, writer = asyncio.StreamReader(), legacy.Writer()
        with patch.object(fcm.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))):
            task = asyncio.create_task(receiver.run())
            async with asyncio.timeout(1):
                while not writer.packets:
                    await asyncio.sleep(0)
            self.assertEqual(receiver.diagnostics()['receive_stage'], 'mcs_login')
            await asyncio.wait_for(receiver.close(), 1)
            self.assertTrue(task.done())
            self.assertTrue(task.cancelled())
            self.assertTrue(writer.closed)
            self.assertFalse(receiver.diagnostics()['receiver_ready'])
            self.assertEqual(receiver.diagnostics()['receive_stage'], 'closed')
            self.assertIsNone(receiver.diagnostics()['receive_failure_stage'])

    async def test_rejection_diagnostics_preserve_ack_and_never_export_payloads(self):
        scenarios = ('sender_mismatch', 'subtype_mismatch', 'deleted_notice',
                     'qv_payload_rejected', 'malformed_payload', 'unsupported_encoding', 'callback_failed')
        for reason in scenarios:
            receiver = self.receiver()
            receiver._writer = legacy.Writer()
            message = self.notification()
            if reason == 'sender_mismatch':
                setattr(message, 'from', 'PRIVATE_OTHER_SENDER')
            elif reason == 'subtype_mismatch':
                message = legacy.FCMTests.encrypted(self, receiver, {'data': payload()})
                next(item for item in message.app_data if item.key == 'subtype').value = 'PRIVATE_OTHER_SUBTYPE'
            elif reason == 'deleted_notice':
                message = self.notification({'message_type': 'deleted_messages'})
            elif reason == 'qv_payload_rejected':
                message = self.notification({'PRIVATE_UNSUPPORTED_FIELD': 'PRIVATE_VALUE'})
            elif reason == 'malformed_payload':
                message = legacy.FCMTests.encrypted(self, receiver, {}, raw_json=b'{"data":')
            elif reason == 'unsupported_encoding':
                message = legacy.FCMTests.encrypted(self, receiver, {'data': payload()})
                next(item for item in message.app_data if item.key == 'content-encoding').value = 'PRIVATE_ENCODING'
            else:
                receiver._on_notification = Mock(side_effect=RuntimeError('PRIVATE_CALLBACK_TEXT'))
            await receiver._data_message(message)
            diag = receiver.diagnostics()
            self.assertEqual(diag['last_message_disposition'], 'rejected')
            self.assertEqual(diag['last_rejection_reason'], reason)
            self.assertEqual(diag['rejection_counts'][reason], 1)
            self.assertEqual(sum(diag['rejection_counts'].values()), 1)
            self.assertEqual(diag['acknowledged'], 1)
            self.assertNotIn('PRIVATE', json.dumps(diag))
            if reason != 'callback_failed':
                receiver._on_notification.assert_not_called()

    async def test_delivery_to_filter_is_separate_from_ring_and_duplicate(self):
        receiver = self.receiver()
        receiver._writer = legacy.Writer()
        message = self.notification()
        await receiver._data_message(message)
        diag = receiver.diagnostics()
        self.assertEqual(diag['last_message_disposition'], 'delivered_to_filter')
        self.assertIsNone(diag['last_rejection_reason'])
        self.assertEqual(sum(diag['rejection_counts'].values()), 0)
        await receiver._data_message(message)
        self.assertEqual(receiver.diagnostics()['last_message_disposition'], 'duplicate')
        self.assertEqual(receiver.diagnostics()['acknowledged'], 2)
        self.assertEqual(receiver.diagnostics()['delivered'], 1)
        self.assertNotIn('ring', diag)


if __name__ == '__main__':
    unittest.main()
