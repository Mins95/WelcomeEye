"""Offline QV payload/replay tests, independent of HA and Firebase packages."""
import json
import unittest

from load_integration import load

qv = load('qv_cloud_events')
NOW = 1792230000
UID = 'PRIVATE_EXACT_UMID'


def payload(**changes):
    return {'DevUmid': UID, 'AlarmEvent': '19', 'DevChNo': '0',
            'AlarmId': 'PRIVATE_ALARM_ID', 'AlarmTime': '2026-10-17 12:34:56',
            'AlarmState': '0', **changes}


class QVEventTests(unittest.TestCase):
    def filter(self, **kwargs):
        return qv.QVEventFilter(UID, now=lambda: NOW, startup_time=NOW - 10, **kwargs)

    def accept(self, data=None, *, sent_ms=NOW * 1000, persistent_id='PRIVATE_MCS_ID',
               instance=None):
        instance = self.filter() if instance is None else instance
        return instance.accept(payload() if data is None else data, persistent_id, sent_ms)

    def test_verified_call_and_channel_conversion(self):
        for wire, expected in [('0', 1), ('1', 2)]:
            event = self.accept(payload(DevChNo=wire))
            self.assertEqual((event.channel, event.event_id, event.sent_ms),
                             (expected, 'PRIVATE_ALARM_ID', NOW * 1000))
            self.assertNotIn('PRIVATE', repr(event))

    def test_only_call_19_not_indoor_accepted_motion_or_generic(self):
        for number in ('0', '1', '2', '14', '20', '38', '39', '45', '46', '100002', '100003'):
            for state in ('0', '1'):
                with self.subTest(number=number, state=state):
                    self.assertIsNone(self.accept(payload(AlarmEvent=number, AlarmState=state)))

    def test_no_numeric_type_or_string_coercion(self):
        for field in ('AlarmEvent', 'DevChNo'):
            for value in (True, False, 0, 19, 1.0, None, [], {}, '', '-1', '+1',
                          ' 1', '1 ', '1.0', '1e0', '\u0661', '9' * 100):
                with self.subTest(field=field, value=value):
                    self.assertIsNone(self.accept(payload(**{field: value})))
        self.assertIsNone(self.accept(payload(DevChNo='256')))

    def test_uid_is_exact_not_name_prefix_or_casefold(self):
        for value in ('', 'PRIVATE_EXACT', UID.lower(), UID + ' ', UID + '-other',
                      'prefix-' + UID, 'other-uid'):
            self.assertIsNone(self.accept(payload(DevUmid=value, CameraName=UID)))

    def test_missing_identity_event_or_channel_never_defaults(self):
        for key in ('DevUmid', 'AlarmEvent', 'AlarmId', 'DevChNo'):
            data = payload()
            del data[key]
            self.assertIsNone(self.accept(data))
        for value in ('', ' ', ' alarm', 'alarm\n', 'a' * 257, None, True):
            self.assertIsNone(self.accept(payload(AlarmId=value)))

    def test_rejects_competing_apk_branches_and_generic_notifications(self):
        for changes in ({'CameraId': 'camera'}, {'SrcAccountId': 'account'},
                        {'message_content': ''}, {'message_content': '0|UID|0|14|date|[name]'},
                        {'userHouseId': 'house'}, {'notification': 'ring'},
                        {'gcm.n.e': '1'}, {'gcm.notification.body': 'call'}):
            self.assertIsNone(self.accept(payload(**changes)))
        self.assertIsNone(qv.qv_notification_data(payload(), has_notification=True))
        self.assertIsNone(self.accept({'title': 'Doorbell', 'body': 'Incoming call'}))

    def test_bounded_maps_strings_bytes_and_no_nested_data(self):
        for data in ([], 'json', {'data': payload()},
                     payload(AlarmInfo='x' * 16385),
                     payload(**{'k' * 129: 'value'}),
                     payload(**{f'k{i}': 'v' for i in range(65)}),
                     payload(**{f'k{i}': 'x' * 16384 for i in range(4)}),
                     payload(AlarmInfo='\U0001f600' * 16384),
                     payload(AlarmInfo='\ud800')):
            self.assertIsNone(self.accept(data))

    def test_untrusted_alarm_time_cannot_make_a_message_fresh(self):
        for alarm_time in ('2026-10-17T12:00:00Z', str(NOW * 1000), 'tomorrow', ''):
            data = payload(AlarmTime=alarm_time)
            self.assertIsNone(self.accept(data, sent_ms=0))
            self.assertIsNone(self.accept(data, sent_ms=(NOW - 121) * 1000))
            self.assertIsNotNone(self.accept(data))
        data = payload()
        del data['AlarmTime']
        self.assertIsNotNone(self.accept(data))

    def test_transport_timestamp_strict_types_units_and_boundaries(self):
        for sent_ms in (None, 0, -1, True, False, str(NOW * 1000), float(NOW * 1000),
                        [], {}, 1 << 63, NOW, NOW * 1000000, (NOW + 6) * 1000):
            self.assertIsNone(self.accept(sent_ms=sent_ms))
        self.assertIsNotNone(self.accept(sent_ms=(NOW + 5) * 1000))
        filt = qv.QVEventFilter(UID, now=lambda: NOW, startup_time=NOW - 200)
        self.assertIsNotNone(self.accept(sent_ms=(NOW - 120) * 1000, instance=filt))
        self.assertIsNone(self.accept(sent_ms=(NOW - 120) * 1000 - 1, instance=filt))

    def test_startup_skips_queued_events_even_without_persisted_state(self):
        filt = qv.QVEventFilter(UID, now=lambda: NOW)
        self.assertIsNone(self.accept(sent_ms=NOW * 1000 - 1, instance=filt))
        self.assertEqual(filt.diagnostics()['last_disposition'], 'before_startup')
        self.assertIsNotNone(self.accept(instance=filt))

    def test_duplicate_alarm_id_covers_new_fcm_id_and_changed_timestamp(self):
        filt = self.filter()
        self.assertIsNotNone(self.accept(instance=filt))
        self.assertIsNone(self.accept(persistent_id='new-transport-id',
                                     sent_ms=(NOW + 1) * 1000, instance=filt))
        self.assertEqual(filt.diagnostics()['duplicate'], 1)

    def test_duplicate_transport_id_covers_changed_event_id(self):
        filt = self.filter()
        self.assertIsNotNone(self.accept(instance=filt))
        self.assertIsNone(self.accept(payload(AlarmId='other'), instance=filt))

    def test_same_alarm_id_on_other_channel_or_device_is_independent(self):
        filt = self.filter()
        self.assertIsNotNone(self.accept(instance=filt))
        self.assertIsNotNone(self.accept(payload(DevChNo='1'), persistent_id='other', instance=filt))
        other = qv.QVEventFilter('OTHER_UMID', now=lambda: NOW, startup_time=NOW - 10)
        self.assertIsNotNone(other.accept(payload(DevUmid='OTHER_UMID'), '', NOW * 1000))

    def test_restore_suppresses_alarm_replay_even_with_refreshed_transport_time(self):
        filt = self.filter()
        self.assertIsNotNone(self.accept(instance=filt))
        saved = json.loads(json.dumps(filt.export_state()))
        restored = qv.QVEventFilter(UID, now=lambda: NOW + 1, state=saved)
        self.assertIsNone(restored.accept(payload(), 'new-mcs-id', (NOW + 1) * 1000))
        self.assertEqual(restored.diagnostics()['duplicate'], 1)

    def test_replay_state_is_bounded_private_and_validated(self):
        filt = self.filter()
        for number in range(qv.SEEN_LIMIT + 5):
            self.assertIsNotNone(self.accept(payload(AlarmId=str(number)),
                persistent_id=str(number), instance=filt))
        state = filt.export_state()
        self.assertEqual(len(state['seen']), qv.SEEN_LIMIT)
        for value in (state, filt.diagnostics()):
            self.assertNotIn('PRIVATE', json.dumps(value))
        state['seen'].clear()
        self.assertEqual(len(filt.export_state()['seen']), qv.SEEN_LIMIT)
        for bad in ({}, {'version': True, 'device': 'unknown', 'seen': []},
                    {**filt.export_state(), 'seen': ['PRIVATE_ID']},
                    {**filt.export_state(), 'seen': ['0' * 64] * (qv.SEEN_LIMIT + 1)}):
            with self.assertRaisesRegex(ValueError, '^invalid_event_state$'):
                self.filter(state=bad)
        with self.assertRaisesRegex(ValueError, '^invalid_event_state$'):
            qv.QVEventFilter('OTHER_UMID', state=filt.export_state())

    def test_no_mcs_id_still_deduplicates_vendor_event(self):
        filt = self.filter()
        self.assertIsNotNone(self.accept(persistent_id='', instance=filt))
        self.assertIsNone(self.accept(persistent_id='', instance=filt))
        for bad in (True, False, [], {}, 123, 'a' * 257):
            self.assertIsNone(self.accept(persistent_id=bad))


if __name__ == '__main__':
    unittest.main()
