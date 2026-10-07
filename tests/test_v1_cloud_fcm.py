"""FCM wire/crypto/lifecycle tests. Every connection and HTTP response is fake."""
import asyncio
from copy import deepcopy
import json
import ssl
import unittest
from unittest.mock import AsyncMock, Mock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from http_ece import encrypt

from load_integration import load

fcm = load('v1_cloud_fcm')

CONTENT = '0|PRIVATE_UID|0|14|20261007143000|[PRIVATE_NAME]'
SENT = 1791376200000


def credentials():
    return {'keys': fcm.FCMReceiver._keys(),
            'gcm': {'android_id': '123456', 'security_token': '654321',
                    'app_id': 'wp:private-installation', 'token': 'PRIVATE_GCM_TOKEN'},
            'fcm': {'registration': {'token': 'PRIVATE_FCM_TOKEN'}}}


def frame(tag, message, *, first=False):
    data = message if isinstance(message, bytes) else message.SerializeToString()
    return (bytes((fcm.MCS_VERSION, tag)) if first else bytes((tag,))) + fcm._varint(len(data)) + data


def notification(identifier='PRIVATE_ID', *, content=CONTENT, sent=SENT):
    message = fcm.DataMessageStanza(**{'from': fcm.SENDER_ID,
        'category': 'org.chromium.linux', 'persistent_id': identifier, 'sent': sent})
    if content is not None:
        message.app_data.add(key='message_content', value=content)
    return message


class Writer:
    def __init__(self):
        self.packets = []
        self.closed = False
        self.transport = Mock()

    def write(self, value):
        self.packets.append(value)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class Response:
    def __init__(self, body, status=200):
        self.body, self.status, self.exited = body, status, False
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.exited = True

    async def iter_chunked(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]


class FCMTests(unittest.IsolatedAsyncioTestCase):
    def receiver(self, saved=True):
        session = Mock()
        session.post.side_effect = AssertionError('No live HTTP permitted')
        receiver = fcm.FCMReceiver(session, Mock(), AsyncMock(),
                                    credentials=credentials() if saved else None,
                                    on_state=Mock())
        receiver._ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return receiver

    async def run_frames(self, receiver, *messages):
        reader, writer = asyncio.StreamReader(), Writer()
        reader.feed_data(frame(3, fcm.LoginResponse(id='login'), first=True))
        for tag, message in messages:
            reader.feed_data(frame(tag, message))
        reader.feed_data(frame(4, fcm.Close()))
        with patch.object(fcm.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))) as connection:
            with self.assertRaisesRegex(fcm.FCMError, '^server_closed$'):
                await receiver.run()
        return writer, connection

    def acknowledgements(self, writer):
        result = []
        for packet in writer.packets[1:]:
            if packet[0] != 7:
                continue
            index, size, shift = 1, 0, 0
            while True:
                byte = packet[index]
                index += 1
                size |= (byte & 127) << shift
                if not byte & 128:
                    break
                shift += 7
            message = fcm.IqStanza.FromString(packet[index:index + size])
            if message.extension.id == 12:
                result.extend(fcm.SelectiveAck.FromString(message.extension.data).id)
        return result

    async def test_plain_lt_real_proto_duplicate_ack_and_millisecond_passthrough(self):
        receiver = self.receiver()
        writer, connection = await self.run_frames(receiver,
            (8, notification()), (8, notification()))
        receiver._on_notification.assert_called_once_with(
            {'message_content': CONTENT}, 'PRIVATE_ID', SENT)
        self.assertEqual(self.acknowledgements(writer), ['PRIVATE_ID', 'PRIVATE_ID'])
        self.assertEqual(receiver.diagnostics()['duplicates'], 1)
        self.assertEqual(receiver._on_state.call_count, 2)
        self.assertFalse(receiver.connected)
        self.assertTrue(writer.closed)
        self.assertIsNone(receiver._run_task)
        options = connection.call_args.kwargs
        self.assertEqual(options['ssl'].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(options['ssl'].check_hostname)
        self.assertEqual(options['server_hostname'], fcm.MCS_HOST)
        self.assertGreater(options['ssl_handshake_timeout'], 0)

    def encrypted(self, receiver, value, *, identifier='encrypted', encoding='aesgcm', raw_json=None):
        keys = receiver.credentials['keys']
        sender = ec.generate_private_key(ec.SECP256R1())
        public = sender.public_key().public_bytes(serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint)
        salt = b'0123456789abcdef'
        raw = encrypt(raw_json if raw_json is not None else json.dumps(value).encode(), private_key=sender,
            dh=fcm._unb64(keys['public']), salt=salt,
            auth_secret=fcm._unb64(keys['secret']), version=encoding)
        message = notification(identifier, content=None)
        message.raw_data = raw
        message.app_data.add(key='subtype', value=receiver.credentials['gcm']['app_id'])
        message.app_data.add(key='content-encoding', value=encoding)
        message.app_data.add(key='crypto-key', value='p256ecdsa=ignored; dh=' + fcm._b64(public).rstrip('='))
        message.app_data.add(key='encryption', value='keyid=test; salt=' + fcm._b64(salt).rstrip('='))
        return message

    async def test_reserved_google_time_is_epoch_ms_without_mcs_sent(self):
        receiver = self.receiver()
        message = notification()
        message.ClearField('sent')
        message.app_data.add(key='google.sent_time', value=str(SENT))
        writer, _ = await self.run_frames(receiver, (8, message))
        receiver._on_notification.assert_called_once_with(
            {'message_content': CONTENT}, 'PRIVATE_ID', SENT)
        self.assertEqual(self.acknowledgements(writer), ['PRIVATE_ID'])
        diag = receiver.diagnostics()
        self.assertEqual(diag['timestamp_source'], 'google_sent_time')
        self.assertEqual(diag['timestamp_status'], 'valid')
        self.assertTrue(diag['google_sent_time_valid'])
        self.assertFalse(diag['mcs_sent_present'])
        self.assertEqual(diag['mcs_sent_digits'], 0)

    async def test_reserved_time_in_encrypted_envelope_and_matching_outer_time(self):
        for encoding in ('aesgcm', 'aes128gcm'):
            with self.subTest(encoding=encoding):
                receiver = self.receiver()
                message = self.encrypted(receiver,
                    {'google.sent_time': SENT,
                     'data': {'message_content': CONTENT}}, encoding=encoding)
                message.ClearField('sent')
                message.app_data.add(key='google.sent_time', value=str(SENT))
                await self.run_frames(receiver, (8, message))
                receiver._on_notification.assert_called_once_with(
                    {'message_content': CONTENT}, 'encrypted', SENT)
                self.assertEqual(receiver.diagnostics()['timestamp_status'], 'valid')

    async def test_invalid_reserved_time_never_falls_back_to_mcs(self):
        for value in ('', '0', '-1', '+1', '1.5', ' 123', '1e12',
                      '١٢٣', str(1 << 63), '9' * 20):
            with self.subTest(value=value):
                receiver = self.receiver()
                message = notification()
                message.app_data.add(key='google.sent_time', value=value)
                await self.run_frames(receiver, (8, message))
                self.assertEqual(receiver._on_notification.call_args.args[2], 0)
                diag = receiver.diagnostics()
                self.assertEqual(diag['timestamp_source'], 'google_sent_time')
                self.assertEqual(diag['timestamp_status'], 'invalid')
                self.assertTrue(diag['google_sent_time_present'])
                self.assertFalse(diag['google_sent_time_valid'])

    async def test_invalid_envelope_timestamp_types_and_conflicting_sources(self):
        for value in (None, True, False, 1.25, [], {}, -1, 0, 1 << 63, SENT + 1):
            with self.subTest(value=value):
                receiver = self.receiver()
                message = self.encrypted(receiver,
                    {'google.sent_time': value, 'data': {'message_content': CONTENT}})
                message.app_data.add(key='google.sent_time', value=str(SENT))
                await self.run_frames(receiver, (8, message))
                self.assertEqual(receiver._on_notification.call_args.args[2], 0)
                expected = 'conflicting' if value == SENT + 1 else 'invalid'
                self.assertEqual(receiver.diagnostics()['timestamp_status'], expected)

    async def test_developer_data_cannot_supply_transport_time(self):
        receiver = self.receiver()
        message = self.encrypted(receiver, {'data': {
            'message_content': CONTENT, 'google.sent_time': SENT}})
        message.ClearField('sent')
        await self.run_frames(receiver, (8, message))
        self.assertEqual(receiver._on_notification.call_args.args[2], 0)
        diag = receiver.diagnostics()
        self.assertEqual(diag['timestamp_source'], 'none')
        self.assertEqual(diag['timestamp_status'], 'missing')
        self.assertFalse(diag['google_sent_time_present'])

    async def test_reserved_google_time_cannot_override_old_or_invalid_mcs_time(self):
        for sent, google, expected in ((SENT - 121000, SENT, 'conflicting'),
                                      (SENT, SENT - 121000, 'conflicting'),
                                      (0, SENT, 'invalid'), (-1, SENT, 'invalid')):
            with self.subTest(sent=sent, google=google):
                receiver = self.receiver()
                message = notification(sent=sent)
                message.app_data.add(key='google.sent_time', value=str(google))
                writer, _ = await self.run_frames(receiver, (8, message))
                self.assertEqual(receiver._on_notification.call_args.args[2], 0)
                self.assertEqual(receiver.diagnostics()['timestamp_status'], expected)
                self.assertTrue(receiver.diagnostics()['google_sent_time_valid'])
                self.assertEqual(self.acknowledgements(writer), ['PRIVATE_ID'])

    async def test_duplicate_timestamp_fields_are_rejected_and_acked(self):
        for mode in ('outer', 'envelope'):
            with self.subTest(mode=mode):
                receiver = self.receiver()
                if mode == 'outer':
                    message = notification()
                    message.app_data.add(key='google.sent_time', value=str(SENT))
                    message.app_data.add(key='google.sent_time', value=str(SENT))
                else:
                    wire = ('{"google.sent_time":1,"google.sent_time":2,"data":' +
                            json.dumps({'message_content': CONTENT}) + '}').encode()
                    message = self.encrypted(receiver, None, raw_json=wire)
                writer, _ = await self.run_frames(receiver, (8, message))
                receiver._on_notification.assert_not_called()
                self.assertEqual(receiver.diagnostics()['malformed'], 1)
                self.assertEqual(len(self.acknowledgements(writer)), 1)

    async def test_timestamp_diagnostics_exclude_dates_and_payload(self):
        receiver = self.receiver()
        message = self.encrypted(receiver,
            {'google.sent_time': SENT, 'data': {'message_content': CONTENT}})
        await self.run_frames(receiver, (8, message))
        encoded = json.dumps(receiver.diagnostics())
        for private in (str(SENT), CONTENT, 'PRIVATE',
                        'wp:private-installation'):
            self.assertNotIn(private, encoded)
        self.assertIn(receiver.diagnostics()['timestamp_source'], fcm.TIMESTAMP_SOURCES)
        self.assertIn(receiver.diagnostics()['timestamp_status'], fcm.TIMESTAMP_STATUSES)

    async def test_mcs_downlink_milliseconds_are_not_rescaled_or_guessed(self):
        for sent, expected, status in ((SENT, SENT, 'valid'),
                                      (SENT // 1000, SENT // 1000, 'valid'),
                                      (0, 0, 'invalid'), (-1, 0, 'invalid'),
                                      ((1 << 63) - 1, (1 << 63) - 1, 'valid')):
            with self.subTest(sent=sent):
                receiver = self.receiver()
                await self.run_frames(receiver, (8, notification(sent=sent)))
                self.assertEqual(receiver._on_notification.call_args.args[2], expected)
                diag = receiver.diagnostics()
                self.assertEqual(diag['timestamp_source'], 'mcs_sent_milliseconds')
                self.assertEqual(diag['timestamp_status'], status)
                self.assertEqual(diag['mcs_sent_digits'], len(str(abs(sent))))

    async def test_end_to_end_fresh_ring_once_and_expired_ring_rejected(self):
        # Synthetic regression of the previous *1000 bug, never a hardware trace.
        import test_v1_cloud as controller_tests
        controller, transport, _, _ = controller_tests.ControllerTests.fixture(self)
        await controller.start()
        await asyncio.wait_for(transport.ready.wait(), 1)
        self.addAsyncCleanup(controller.close)
        receiver = self.receiver()
        receiver._on_notification = controller._message
        with patch.object(controller_tests.cloud.time, 'time', return_value=SENT / 1000):
            controller._message({'message_content': CONTENT}, 'old-conversion', SENT * 1000)
            controller._on_ring.assert_not_called()
            self.assertEqual(controller.diagnostics()['last_timestamp_check'], 'future')
            expired = notification('expired',
                content=CONTENT.replace('20261007143000', '20261007143001'),
                sent=SENT - 121000)
            await self.run_frames(receiver, (8, notification()),
                                  (8, notification()), (8, expired))
        controller._on_ring.assert_called_once_with(1)
        self.assertEqual(controller.diagnostics()['rings_received'], 1)
        self.assertEqual(controller.diagnostics()['last_timestamp_check'], 'expired')
        self.assertEqual(receiver.diagnostics()['duplicates'], 1)
        if controller._save_task:
            await controller._save_task

    async def test_actual_encrypted_web_payloads_are_normalized(self):
        for encoding in ('aesgcm', 'aes128gcm'):
            with self.subTest(encoding=encoding):
                receiver = self.receiver()
                message = self.encrypted(receiver, {'data': {'message_content': CONTENT},
                    'notification': {'title': 'not an event'}}, encoding=encoding)
                setattr(message, 'from', 'generic_web_push_route')
                writer, _ = await self.run_frames(receiver, (8, message))
                receiver._on_notification.assert_called_once_with(
                    {'message_content': CONTENT}, 'encrypted', SENT)
                self.assertEqual(self.acknowledgements(writer), ['encrypted'])

    async def test_malformed_ciphertext_is_acked_and_next_message_delivered(self):
        receiver = self.receiver()
        bad = self.encrypted(receiver, {'data': {'message_content': CONTENT}})
        bad.raw_data = b'PRIVATE_MALFORMED_BYTES'
        writer, _ = await self.run_frames(receiver, (8, bad), (8, notification('valid')))
        receiver._on_notification.assert_called_once_with(
            {'message_content': CONTENT}, 'valid', SENT)
        self.assertEqual(self.acknowledgements(writer), ['encrypted', 'valid'])
        self.assertEqual(receiver.diagnostics()['malformed'], 1)

    async def test_notification_only_other_sender_and_subtype_are_ignored(self):
        receiver = self.receiver()
        only_title = self.encrypted(receiver, {'notification': {'title': CONTENT}})
        other_sender = notification('other_sender')
        setattr(other_sender, 'from', 'other_project')
        other_subtype = notification('other_subtype')
        other_subtype.app_data.add(key='subtype', value='different_installation')
        writer, _ = await self.run_frames(receiver, (8, only_title), (8, other_sender), (8, other_subtype))
        receiver._on_notification.assert_not_called()
        self.assertEqual(len(self.acknowledgements(writer)), 3)

    async def test_bad_protobuf_callback_error_and_missing_time_are_isolated(self):
        receiver = self.receiver()
        receiver._on_notification.side_effect = [ValueError('PRIVATE_EXCEPTION'), None]
        writer, _ = await self.run_frames(receiver, (8, b'\x80'),
            (8, notification('callback-error')), (8, notification('missing-time', sent=0)))
        self.assertEqual(receiver._on_notification.call_args.args[2], 0)
        self.assertEqual(receiver.diagnostics()['callback_errors'], 1)
        self.assertEqual(receiver.diagnostics()['malformed'], 1)
        self.assertEqual(self.acknowledgements(writer), ['callback-error', 'missing-time'])
        for secret in ('PRIVATE', CONTENT, '654321', '123456'):
            self.assertNotIn(secret, json.dumps(receiver.diagnostics()))

    async def test_fresh_registration_uses_real_proto_and_awaits_storage(self):
        receiver = self.receiver(saved=False)
        checkin = fcm.AndroidCheckinResponse(stats_ok=True, android_id=123, security_token=456)
        responses = [Response(checkin.SerializeToString()), Response(b'token=GCM_TOKEN'),
            Response(json.dumps({'fid': 'installation-id', 'refreshToken': 'REFRESH',
                'authToken': {'token': 'INSTALL_AUTH', 'expiresIn': '604800s'}}).encode()),
            Response(b'{"token":"NEW_FCM_TOKEN"}')]
        receiver._session.post.side_effect = responses
        saved = []
        async def save(value):
            await asyncio.sleep(0)
            saved.append(deepcopy(value))
        receiver._on_credentials = save
        self.assertEqual(await receiver.register(), 'NEW_FCM_TOKEN')
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0], receiver.credentials)
        calls = receiver._session.post.call_args_list
        self.assertEqual(len(calls), 4)
        request = fcm.AndroidCheckinRequest.FromString(calls[0].kwargs['data'])
        self.assertEqual(request.checkin.type, fcm.DEVICE_CHROME_BROWSER)
        self.assertFalse(request.HasField('id'))
        self.assertEqual(calls[2].kwargs['json']['appId'], fcm.APP_ID)
        self.assertEqual(len(calls[2].kwargs['json']['fid']), 22)
        self.assertNotIn('PRIVATE_FCM_TOKEN', json.dumps(calls[3].kwargs['json']))
        for call in calls:
            self.assertFalse(call.kwargs['allow_redirects'])
            self.assertEqual(call.kwargs['ssl'].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(all(response.exited for response in responses))

    async def test_saved_credentials_only_checkin_and_persist_before_return(self):
        receiver = self.receiver()
        response = fcm.AndroidCheckinResponse(stats_ok=True, android_id=123456, security_token=654321)
        receiver._session.post.side_effect = [Response(response.SerializeToString())]
        token = await receiver.register()
        self.assertEqual(token, 'PRIVATE_FCM_TOKEN')
        self.assertEqual(receiver._session.post.call_count, 1)
        request = fcm.AndroidCheckinRequest.FromString(receiver._session.post.call_args.kwargs['data'])
        self.assertEqual(request.id, 123456)
        receiver._on_credentials.assert_awaited_once()

    async def test_rejected_checkin_never_enrolls_or_exposes_error_body(self):
        receiver = self.receiver()
        response = Response(b'PRIVATE_BODY_TOKEN', status=403)
        response.iter_chunked = Mock(side_effect=AssertionError('must not read rejection body'))
        receiver._session.post.side_effect = [response]
        with self.assertRaisesRegex(fcm.FCMError, '^http_rejected$') as error:
            await receiver.register()
        self.assertEqual(receiver._session.post.call_count, 1)
        receiver._on_credentials.assert_not_called()
        self.assertNotIn('PRIVATE', str(error.exception))
        self.assertTrue(response.exited)

    async def test_changed_identity_invalid_saved_and_oversized_http_fail_closed(self):
        for scenario in ('changed', 'credentials', 'large'):
            with self.subTest(scenario=scenario):
                receiver = self.receiver()
                if scenario == 'credentials':
                    receiver._credentials['gcm']['android_id'] = 'SECRET_INVALID'
                elif scenario == 'changed':
                    response = fcm.AndroidCheckinResponse(stats_ok=True, android_id=999, security_token=654321)
                    receiver._session.post.side_effect = [Response(response.SerializeToString())]
                else:
                    receiver._session.post.side_effect = [Response(b'x' * (fcm.MAX_BYTES + 1))]
                with self.assertRaises(fcm.FCMError):
                    await receiver.register()
                self.assertLessEqual(receiver._session.post.call_count, 1)
                receiver._on_credentials.assert_not_called()

    async def test_storage_failure_does_not_return_token(self):
        receiver = self.receiver()
        response = fcm.AndroidCheckinResponse(stats_ok=True, android_id=123456, security_token=654321)
        receiver._session.post.side_effect = [Response(response.SerializeToString())]
        receiver._on_credentials.side_effect = ValueError('PRIVATE_STORAGE_ERROR')
        with self.assertRaisesRegex(fcm.FCMError, '^storage_failed$'):
            await receiver.register()
        self.assertIsNone(receiver._register_task)

    async def test_connection_only_available_after_successful_login_and_close_drains(self):
        receiver = self.receiver()
        reader, writer = asyncio.StreamReader(), Writer()
        with patch.object(fcm.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))):
            task = asyncio.create_task(receiver.run())
            while not writer.packets:
                await asyncio.sleep(0)
            self.assertFalse(receiver.connected)
            reader.feed_data(frame(3, fcm.LoginResponse(id='login'), first=True))
            for _ in range(50):
                if receiver.connected:
                    break
                await asyncio.sleep(0)
            self.assertTrue(receiver.connected)
            await asyncio.wait_for(receiver.close(), 1)
            self.assertTrue(task.done())
            self.assertTrue(task.cancelled())
            self.assertFalse(receiver.connected)
            self.assertTrue(writer.closed)
            self.assertIsNone(receiver._run_task)
            self.assertTrue(receiver._close_task.done())
            count = receiver._on_notification.call_count
            await receiver._data_message(notification('late'))
            self.assertEqual(receiver._on_notification.call_count, count)
            await receiver.close()
        receiver._session.close.assert_not_called()

    async def test_close_during_connect_or_registration_leaves_no_worker(self):
        for phase in ('connect', 'register'):
            with self.subTest(phase=phase):
                receiver = self.receiver()
                entered = asyncio.Event()
                async def block(*args, **kwargs):
                    entered.set()
                    await asyncio.Event().wait()
                with patch.object(fcm.asyncio, 'open_connection', block):
                    receiver._checkin = block
                    task = asyncio.create_task(receiver.run() if phase == 'connect' else receiver.register())
                    await entered.wait()
                    await asyncio.wait_for(receiver.close(), 1)
                    self.assertTrue(task.done())
                    self.assertIsNone(receiver._run_task)
                    self.assertIsNone(receiver._register_task)
                    receiver._on_credentials.assert_not_called()

    async def test_login_rejection_and_partial_frame_timeout_are_safe(self):
        for mode in ('login', 'partial', 'length'):
            with self.subTest(mode=mode):
                receiver = self.receiver()
                reader, writer = asyncio.StreamReader(), Writer()
                if mode == 'login':
                    reply = fcm.LoginResponse(id='id')
                    reply.error.code = 401
                    reply.error.message = 'PRIVATE_REJECTION'
                    reader.feed_data(frame(3, reply, first=True))
                else:
                    reader.feed_data(frame(3, fcm.LoginResponse(id='id'), first=True))
                    reader.feed_data(b'\x08' + (b'\x81' if mode == 'partial' else b'\x80' * 5))
                with patch.object(fcm.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))), patch.object(fcm, 'FRAME_TIMEOUT', 0.01):
                    with self.assertRaises(fcm.FCMError):
                        await asyncio.wait_for(receiver.run(), 1)
                self.assertTrue(writer.closed)
                self.assertFalse(receiver.connected)

    async def test_heartbeat_ack_and_missing_ack_disconnect(self):
        receiver = self.receiver()
        reader, writer = asyncio.StreamReader(), Writer()
        reader.feed_data(frame(3, fcm.LoginResponse(id='id'), first=True))
        reader.feed_data(frame(0, fcm.HeartbeatPing()))
        with patch.object(fcm.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))), patch.object(fcm, 'HEARTBEAT_INTERVAL', 0.01), patch.object(fcm, 'HEARTBEAT_TIMEOUT', 0.01):
            with self.assertRaises(fcm.FCMError):
                await asyncio.wait_for(receiver.run(), 1)
        self.assertIn(1, [packet[0] for packet in writer.packets[1:]])
        self.assertIn(0, [packet[0] for packet in writer.packets[1:]])
        self.assertTrue(writer.closed)

    async def test_cancelled_close_waits_for_owned_cleanup(self):
        receiver = self.receiver()
        reader, writer = asyncio.StreamReader(), Writer()
        reader.feed_data(frame(3, fcm.LoginResponse(id='id'), first=True))
        waiting, release = asyncio.Event(), asyncio.Event()
        async def slow_close():
            waiting.set()
            await release.wait()
        writer.wait_closed = slow_close
        with patch.object(fcm.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))):
            running = asyncio.create_task(receiver.run())
            while not receiver.connected:
                await asyncio.sleep(0)
            closing = asyncio.create_task(receiver.close())
            await waiting.wait()
            closing.cancel()
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(closing, 1)
            self.assertTrue(running.done())
            self.assertTrue(receiver._close_task.done())
            self.assertIsNone(receiver._run_task)

    async def test_socket_login_and_frame_size_are_bounded_and_iq_not_echoed(self):
        for mode in ('login-timeout', 'oversized', 'iq'):
            with self.subTest(mode=mode):
                receiver = self.receiver()
                reader, writer = asyncio.StreamReader(), Writer()
                if mode != 'login-timeout':
                    reader.feed_data(frame(3, fcm.LoginResponse(id='id'), first=True))
                if mode == 'oversized':
                    reader.feed_data(bytes((8,)) + fcm._varint(fcm.MAX_BYTES + 1))
                elif mode == 'iq':
                    message = fcm.IqStanza(type=fcm.IqStanza.SET, id='')
                    message.extension.id = 13
                    message.extension.data = b''
                    reader.feed_data(frame(7, message))
                    reader.feed_data(frame(4, fcm.Close()))
                expected = {'login-timeout': 'login_timeout', 'oversized': 'frame_too_large',
                            'iq': 'server_closed'}[mode]
                with patch.object(fcm.asyncio, 'open_connection', AsyncMock(return_value=(reader, writer))), patch.object(fcm, 'LOGIN_TIMEOUT', 0.01):
                    with self.assertRaisesRegex(fcm.FCMError, '^' + expected + '$'):
                        await asyncio.wait_for(receiver.run(), 1)
                self.assertTrue(writer.closed)
                self.assertEqual(receiver.diagnostics()['last_error'], expected)
                if mode == 'iq':
                    self.assertEqual(len(writer.packets), 1)


if __name__ == '__main__':
    unittest.main()
