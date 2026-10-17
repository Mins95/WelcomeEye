"""QV profile/wire regression tests. All network I/O is replaced with fakes."""
from dataclasses import FrozenInstanceError, replace
import json
import ssl
import unittest
from unittest.mock import AsyncMock, Mock

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


if __name__ == '__main__':
    unittest.main()
