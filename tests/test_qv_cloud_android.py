"""Native QV registration and migration: all HTTP and MCS peers are fake."""
import asyncio
from copy import deepcopy
from dataclasses import asdict, FrozenInstanceError
import gzip
import json
import ssl
import unittest
from unittest.mock import AsyncMock, Mock

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from load_integration import load
import test_v1_cloud_fcm as legacy
from test_qv_cloud_events import NOW, UID, payload

native = load('qv_cloud_android')
fcm = load('v1_cloud_fcm')
events = load('qv_cloud_events')


def native_credentials(receiver):
    return {'transport': 'android', 'config': receiver._config(),
        'android': asdict(receiver._android),
        'gcm': {'android_id': '123456', 'security_token': '654321',
                'app_id': receiver._profile.sender_id, 'token': 'PRIVATE_NATIVE_TOKEN'},
        'fcm': {'registration': {'token': 'PRIVATE_NATIVE_TOKEN'},
                'installation': {'fid': 'c' + 'A' * 21, 'token': 'PRIVATE_AUTH',
                    'refresh_token': 'PRIVATE_REFRESH', 'expires_in': 604800,
                    'created_at': 1792230000.0}}}


def android_build_message():
    """Independent schema extract from microG checkin.proto, not our encoder."""
    file = descriptor_pb2.FileDescriptorProto(name='android_build_test.proto', syntax='proto2')
    build = file.message_type.add(name='Build')
    build.field.add(name='client_id', number=6, type=9, label=1)
    build.field.add(name='sdk_version', number=10, type=5, label=1)
    checkin = file.message_type.add(name='Checkin')
    checkin.field.add(name='build', number=1, type=11, type_name='.Build', label=2)
    pool = descriptor_pool.DescriptorPool()
    pool.Add(file)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName('Checkin'))


class AndroidFCMTests(unittest.IsolatedAsyncioTestCase):
    run_frames = legacy.FCMTests.run_frames
    acknowledgements = legacy.FCMTests.acknowledgements

    def receiver(self, *, variant='connect3', saved=None, persistent_ids=None):
        session = Mock()
        session.post.side_effect = AssertionError('No live HTTP permitted')
        receiver = native.QVAndroidFCMReceiver(session, Mock(), AsyncMock(),
            variant=variant, persistent_ids=persistent_ids)
        receiver._ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if saved == 'native':
            receiver._credentials = native_credentials(receiver)
        elif saved == 'web':
            receiver._credentials = legacy.credentials()
            receiver._credentials['config'] = receiver._config()
            receiver._migration_status = 'pending'
        elif saved is not None:
            receiver._credentials = deepcopy(saved)
        return receiver

    def fake_enrollment(self, receiver, *, fail_at=None, invalid=None):
        calls = []

        def post(url, **kwargs):
            calls.append((url, kwargs))
            if fail_at == len(calls):
                return legacy.Response(b'PRIVATE_SERVER_ERROR', status=403)
            if url == fcm.GCM_CHECKIN_URL:
                return legacy.Response(fcm.AndroidCheckinResponse(
                    stats_ok=True, android_id=123456, security_token=654321).SerializeToString())
            if url.startswith(fcm.FCM_INSTALLATION):
                request = json.loads(gzip.decompress(kwargs['data']))
                response = {'fid': request['fid'], 'refreshToken': 'PRIVATE_REFRESH',
                    'authToken': {'token': 'PRIVATE_AUTH', 'expiresIn': '604800s'}}
                if invalid:
                    invalid(response)
                return legacy.Response(json.dumps(response).encode())
            if url == fcm.GCM_REGISTER_URL:
                return legacy.Response(b'token=PRIVATE_NATIVE_TOKEN\n')
            raise AssertionError('Unexpected endpoint')

        receiver._session.post.side_effect = post
        return calls

    def notification(self, receiver, *, values=None, identifier='PRIVATE_ID', sent=NOW * 1000):
        message = legacy.notification(identifier, content=None, sent=sent)
        message.category = receiver._android.package
        for key, value in (payload() if values is None else values).items():
            message.app_data.add(key=key, value=value)
        return message

    def test_public_profiles_are_immutable_distinct_and_keep_private_client_ids(self):
        connect3, r002 = self.receiver(), self.receiver(variant='connect2_r002')
        self.assertEqual(connect3._android.package, 'com.extel.philipsdoorconnect')
        self.assertEqual(r002._android.package, 'com.extel.philipswelcomeeye')
        self.assertEqual(connect3._android.certificate_sha1,
                         'E23CA2E769D41E28F45B3C0B823D098909497BE3')
        self.assertEqual(r002._android.certificate_sha1,
                         '83B9A96C794DC8196DBAF3E35199E5F141E430D0')
        self.assertEqual(connect3._profile.bundle_id, 'welcomeeye.homeassistant.connect3')
        self.assertEqual(r002._profile.bundle_id, 'welcomeeye.homeassistant.r002')
        self.assertEqual(fcm.DEFAULT_PROFILE.bundle_id, 'welcomeeye.homeassistant')
        with self.assertRaises(FrozenInstanceError):
            connect3._android.package = 'other'

    def test_checkin_is_android_has_required_build_and_no_phone_identifiers(self):
        request = native._android_checkin()
        parsed = fcm.AndroidCheckinRequest.FromString(request.SerializeToString())
        self.assertTrue(parsed.IsInitialized())
        self.assertEqual(parsed.version, 3)
        self.assertEqual(parsed.fragment, 0)
        self.assertEqual(parsed.checkin.type, native.DEVICE_ANDROID_OS)
        self.assertFalse(parsed.checkin.HasField('chrome_build'))
        build = android_build_message().FromString(parsed.checkin.SerializeToString())
        self.assertTrue(build.IsInitialized())
        self.assertEqual(build.build.client_id, 'android-google')
        self.assertEqual(build.build.sdk_version, 29)
        self.assertEqual(list(parsed.account_cookie), [''])
        for field in ('imei', 'meid', 'esn', 'serial_number', 'id', 'security_token'):
            self.assertFalse(parsed.HasField(field))
        self.assertEqual(list(parsed.mac_addr), [])

    async def test_both_apks_enroll_natively_with_exact_headers_and_no_web_endpoint(self):
        for variant in ('connect3', 'connect2_r002'):
            receiver = self.receiver(variant=variant)
            calls = self.fake_enrollment(receiver)
            self.assertEqual(await receiver.register(), 'PRIVATE_NATIVE_TOKEN')
            self.assertEqual([url for url, _ in calls], [fcm.GCM_CHECKIN_URL,
                fcm.FCM_INSTALLATION + 'projects/tdkvdp/installations', fcm.GCM_REGISTER_URL])
            checkin = fcm.AndroidCheckinRequest.FromString(gzip.decompress(calls[0][1]['data']))
            self.assertEqual(checkin.checkin.type, native.DEVICE_ANDROID_OS)
            fis = calls[1][1]
            self.assertEqual(fis['headers']['X-Android-Package'], receiver._android.package)
            self.assertEqual(fis['headers']['X-Android-Cert'], receiver._android.certificate_sha1)
            body = json.loads(gzip.decompress(fis['data']))
            self.assertEqual(set(body), {'appId', 'fid', 'authVersion', 'sdkVersion'})
            self.assertEqual(body['appId'], receiver._profile.app_id)
            self.assertEqual(body['sdkVersion'], 'a:17.1.3')
            self.assertEqual(body['authVersion'], 'FIS_v2')
            registration = calls[2][1]
            self.assertEqual(registration['headers']['Authorization'], 'AidLogin 123456:654321')
            self.assertEqual(registration['headers']['app'], receiver._android.package)
            form = registration['data']
            self.assertEqual(form['app'], receiver._android.package)
            self.assertEqual(form['cert'], receiver._android.certificate_sha1.lower())
            self.assertEqual(form['sender'], receiver._profile.sender_id)
            self.assertEqual(form['X-subtype'], receiver._profile.sender_id)
            self.assertEqual(form['X-scope'], '*')
            self.assertEqual(form['X-gmp_app_id'], receiver._profile.app_id)
            self.assertEqual(form['X-appid'], body['fid'])
            self.assertEqual(form['X-Goog-Firebase-Installations-Auth'], 'PRIVATE_AUTH')
            self.assertEqual(form['app_ver'], str(receiver._android.version_code))
            self.assertEqual(form['target_ver'], '36')
            self.assertEqual(form['X-cliv'], 'fcm-23.1.2')
            self.assertEqual(form['X-firebase-app-name-hash'], 'R1dAH9Ui7M-ynoznwBdw01tLxhI')
            self.assertNotIn('keys', receiver.credentials)
            self.assertEqual(receiver.credentials['transport'], 'android')
            receiver._on_credentials.assert_awaited_once_with(receiver.credentials)
            self.assertNotIn('PRIVATE', json.dumps(receiver.diagnostics()))
            for _, options in calls:
                self.assertFalse(options['allow_redirects'])
                self.assertTrue(options['ssl'].check_hostname)
                self.assertIsNone(options['json'])

    async def test_saved_native_reuses_identity_token_and_persistent_ids(self):
        receiver = self.receiver(saved='native', persistent_ids=['PRIVATE_OLD_ID'])
        receiver._credentials['gcm']['android_id'] = str(2**63 + 123)
        old = receiver.credentials
        receiver._session.post.side_effect = [legacy.Response(fcm.AndroidCheckinResponse(
            stats_ok=True, android_id=2**63 + 123, security_token=12345).SerializeToString())]
        self.assertEqual(await receiver.register(), 'PRIVATE_NATIVE_TOKEN')
        receiver._session.post.assert_called_once()
        request = fcm.AndroidCheckinRequest.FromString(gzip.decompress(
            receiver._session.post.call_args.kwargs['data']))
        self.assertEqual(request.id, -(2**63) + 123)
        self.assertEqual(request.security_token, 654321)
        self.assertEqual(request.fragment, 1)
        self.assertEqual(receiver.credentials['fcm'], old['fcm'])
        login = receiver._login()
        self.assertEqual(login.id, 'android-29')
        self.assertEqual(login.user, str(2**63 + 123))
        self.assertEqual(login.auth_token, '12345')
        self.assertEqual(list(login.received_persistent_id), ['PRIVATE_OLD_ID'])

    async def test_migration_commits_after_durable_callback_and_does_not_mutate_callback_input(self):
        receiver = self.receiver(saved='web', persistent_ids=['PRIVATE_WEB_ID'])
        old = receiver.credentials
        candidate = native_credentials(receiver)
        receiver._enroll = AsyncMock(return_value=candidate)

        async def save(value):
            self.assertEqual(receiver.credentials, old)
            self.assertEqual(value['transport'], 'android')
            self.assertEqual(receiver.diagnostics()['migration_status'], 'running')
            value['gcm']['token'] = 'PRIVATE_MUTATED_CALLBACK'

        receiver._on_credentials = save
        self.assertEqual(await receiver.register(), 'PRIVATE_NATIVE_TOKEN')
        self.assertEqual(receiver.credentials, candidate)
        self.assertEqual(list(receiver._seen), [])
        self.assertEqual(receiver.diagnostics()['migration_status'], 'complete')
        self.assertEqual(receiver.diagnostics()['credential_transport'], 'android')
        self.assertEqual(old['fcm']['registration']['token'], 'PRIVATE_FCM_TOKEN')
        receiver._session.post.assert_not_called()

    async def test_enrollment_or_storage_failure_restores_entire_saved_web_state(self):
        for stage in ('enroll', 'save'):
            receiver = self.receiver(saved='web', persistent_ids=['PRIVATE_WEB_ID'])
            old = receiver.credentials
            receiver._enroll = AsyncMock(return_value=native_credentials(receiver))
            if stage == 'enroll':
                receiver._enroll.side_effect = RuntimeError('PRIVATE_ENROLL_FAILURE')
            else:
                receiver._on_credentials.side_effect = RuntimeError('PRIVATE_SAVE_FAILURE')
            with self.assertRaises(fcm.FCMError) as caught:
                await receiver.register()
            self.assertEqual(receiver.credentials, old)
            self.assertEqual(list(receiver._seen), ['PRIVATE_WEB_ID'])
            self.assertIsNone(receiver._register_task)
            self.assertEqual(receiver.diagnostics()['migration_status'], 'failed')
            self.assertNotIn('PRIVATE', str(caught.exception))
            self.assertNotIn('PRIVATE', json.dumps(receiver.diagnostics()))

    async def test_cancel_during_enrollment_or_save_restores_saved_web(self):
        for stage in ('enroll', 'save'):
            receiver = self.receiver(saved='web', persistent_ids=['PRIVATE_WEB_ID'])
            old = receiver.credentials
            entered = asyncio.Event()

            async def wait(*args):
                entered.set()
                await asyncio.Event().wait()

            receiver._enroll = AsyncMock(return_value=native_credentials(receiver))
            if stage == 'enroll':
                receiver._enroll.side_effect = wait
            else:
                receiver._on_credentials.side_effect = wait
            task = asyncio.create_task(receiver.register())
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(receiver.credentials, old)
            self.assertEqual(list(receiver._seen), ['PRIVATE_WEB_ID'])
            self.assertEqual(receiver.diagnostics()['migration_status'], 'cancelled')
            self.assertIsNone(receiver._register_task)

    async def test_invalid_saved_credentials_never_enroll_or_fall_back_to_web(self):
        changes = [lambda s: s.update(transport='PRIVATE_OTHER'),
            lambda s: s['android'].update(package='PRIVATE_WRONG_PACKAGE'),
            lambda s: s['android'].update(certificate_sha1='0' * 40),
            lambda s: s['config'].update(bundle_id='PRIVATE_WRONG_CLIENT'),
            lambda s: s['config'].update(app_id='PRIVATE_WRONG_APP'),
            lambda s: s['gcm'].update(app_id='PRIVATE_WRONG_SENDER'),
            lambda s: s['gcm'].update(security_token='0'),
            lambda s: s['gcm'].update(android_id='\u0661'),
            lambda s: s['gcm'].update(token='PRIVATE_DIFFERENT'),
            lambda s: s['fcm']['installation'].update(fid='PRIVATE_BAD_FID'),
            lambda s: s['fcm']['installation'].update(created_at=float('nan')),
            lambda s: s['fcm']['installation'].update(expires_in=True),
            lambda s: s.update(keys={'private': 'PRIVATE_KEY'})]
        for change in changes:
            receiver = self.receiver(saved='native')
            change(receiver._credentials)
            receiver._enroll = AsyncMock()
            with self.assertRaisesRegex(fcm.FCMError, '^invalid_credentials$'):
                await receiver.register()
            receiver._enroll.assert_not_called()
            receiver._session.post.assert_not_called()
            self.assertNotIn('PRIVATE', json.dumps(receiver.diagnostics()))

    async def test_cross_profile_or_corrupt_saved_web_is_not_silently_replaced(self):
        for field in ('bundle_id', 'project_id', 'app_id'):
            receiver = self.receiver(saved='web')
            receiver._credentials['config'][field] = 'PRIVATE_OTHER_PROFILE'
            receiver._enroll = AsyncMock()
            with self.assertRaisesRegex(fcm.FCMError, '^invalid_credentials$'):
                await receiver.register()
            receiver._enroll.assert_not_called()
            receiver._session.post.assert_not_called()

    async def test_checkin_identity_change_preserves_saved_native_credentials(self):
        receiver = self.receiver(saved='native')
        old = receiver.credentials
        receiver._session.post.side_effect = [legacy.Response(fcm.AndroidCheckinResponse(
            stats_ok=True, android_id=999, security_token=888).SerializeToString())]
        with self.assertRaisesRegex(fcm.FCMError, '^identity_changed$'):
            await receiver.register()
        self.assertEqual(receiver.credentials, old)
        receiver._on_credentials.assert_not_awaited()

    async def test_http_failure_at_each_enrollment_step_is_private_and_preserves_web(self):
        for step in (1, 2, 3):
            receiver = self.receiver(saved='web')
            old = receiver.credentials
            self.fake_enrollment(receiver, fail_at=step)
            with self.assertRaisesRegex(fcm.FCMError, '^http_rejected$'):
                await receiver.register()
            self.assertEqual(receiver.credentials, old)
            receiver._on_credentials.assert_not_awaited()
            self.assertNotIn('PRIVATE', json.dumps(receiver.diagnostics()))

    async def test_invalid_fis_response_does_not_reach_registration_or_save(self):
        changes = [lambda s: s.update(fid='PRIVATE_WRONG_FID'),
            lambda s: s['authToken'].update(token='PRIVATE_BAD\nTOKEN'),
            lambda s: s['authToken'].update(expiresIn=True),
            lambda s: s['authToken'].update(expiresIn='0s'),
            lambda s: s['authToken'].update(expiresIn='999999999999s'),
            lambda s: s.update(refreshToken='')]
        for change in changes:
            receiver = self.receiver()
            calls = self.fake_enrollment(receiver, invalid=change)
            with self.assertRaises(fcm.FCMError):
                await receiver.register()
            self.assertEqual(len(calls), 2)
            self.assertIsNone(receiver.credentials)
            receiver._on_credentials.assert_not_awaited()
            self.assertNotIn('PRIVATE', json.dumps(receiver.diagnostics()))

    async def test_native_delivery_ack_dedup_and_transport_time_use_real_frames(self):
        for variant in ('connect3', 'connect2_r002'):
            receiver = self.receiver(variant=variant, saved='native')
            message = self.notification(receiver, values={**payload(),
                'gcm.n.e': '0', 'gcm.n.title': 'PRIVATE_TITLE',
                'google.sent_time': str(NOW * 1000), 'subtype': receiver._profile.sender_id})
            writer, _ = await self.run_frames(receiver, (8, message), (8, message))
            receiver._on_notification.assert_called_once_with(payload(), 'PRIVATE_ID', NOW * 1000)
            self.assertEqual(self.acknowledgements(writer), ['PRIVATE_ID', 'PRIVATE_ID'])
            self.assertEqual(receiver.diagnostics()['duplicates'], 1)
            self.assertNotIn('PRIVATE', json.dumps(receiver.diagnostics()))

    async def test_wrong_native_target_sender_subtype_and_raw_data_are_acked_and_rejected(self):
        changes = [(lambda m: setattr(m, 'category', 'PRIVATE_OTHER_PACKAGE'), 'category_mismatch'),
            (lambda m: setattr(m, 'from', 'PRIVATE_OTHER_SENDER'), 'sender_mismatch'),
            (lambda m: m.app_data.add(key='subtype', value='PRIVATE_WEB_SUBTYPE'), 'subtype_mismatch'),
            (lambda m: setattr(m, 'raw_data', b'PRIVATE_ENCRYPTED_WEB'), 'unexpected_transport')]
        for change, reason in changes:
            receiver = self.receiver(saved='native')
            message = self.notification(receiver)
            change(message)
            writer, _ = await self.run_frames(receiver, (8, message))
            receiver._on_notification.assert_not_called()
            self.assertEqual(self.acknowledgements(writer), ['PRIVATE_ID'])
            self.assertEqual(receiver.diagnostics()['last_rejection_reason'], reason)
            self.assertNotIn('PRIVATE', json.dumps(receiver.diagnostics()))

    async def test_target_rejection_after_delivery_resets_previous_timestamp_diagnostics(self):
        receiver = self.receiver(saved='native')
        receiver._writer = legacy.Writer()
        await receiver._data_message(self.notification(receiver))
        self.assertEqual(receiver.diagnostics()['timestamp_status'], 'valid')
        rejected = self.notification(receiver, identifier='PRIVATE_OTHER_ID')
        rejected.category = 'PRIVATE_OTHER_PACKAGE'
        await receiver._data_message(rejected)
        receiver._on_notification.assert_called_once()
        diag = receiver.diagnostics()
        self.assertEqual(diag['last_rejection_reason'], 'category_mismatch')
        self.assertEqual(diag['timestamp_status'], 'unobserved')
        self.assertEqual(diag['timestamp_source'], 'none')
        self.assertEqual(diag['acknowledged'], 2)

    async def test_native_wire_preserves_identity_age_and_event_replay_filters(self):
        receiver = self.receiver(saved='native')
        event_filter = events.QVEventFilter(UID, now=lambda: NOW, startup_time=NOW - 10)
        accepted = []
        receiver._on_notification = lambda *args: accepted.append(event_filter.accept(*args))
        messages = [self.notification(receiver, values=payload(DevUmid='PRIVATE_OTHER_UID'),
                                      identifier='PRIVATE_WRONG_UID'),
            self.notification(receiver, sent=(NOW - 180) * 1000, identifier='PRIVATE_OLD'),
            self.notification(receiver, identifier='PRIVATE_FRESH'),
            self.notification(receiver, identifier='PRIVATE_REPEATED_EVENT')]
        writer, _ = await self.run_frames(receiver, *((8, message) for message in messages))
        self.assertEqual([value is not None for value in accepted], [False, False, True, False])
        self.assertEqual(len(self.acknowledgements(writer)), 4)

    async def test_missing_qv_fields_and_enabled_generic_notifications_still_reject(self):
        for values, reason in [({'from': 'PRIVATE_VALUE'}, 'missing_required_fields'),
                               ({**payload(), 'gcm.n.e': '1'}, 'generic_notification')]:
            receiver = self.receiver(saved='native')
            message = self.notification(receiver, values=values)
            writer, _ = await self.run_frames(receiver, (8, message))
            receiver._on_notification.assert_not_called()
            self.assertEqual(self.acknowledgements(writer), ['PRIVATE_ID'])
            self.assertEqual(receiver.diagnostics()['last_payload_subreason'], reason)


if __name__ == '__main__':
    unittest.main()
