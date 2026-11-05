"""Synthetic history: pagination, privacy, isolation and no replay into rings."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from xml.etree import ElementTree as ET

from load_integration import load
from test_qv_cloud_protocol import envelope
from test_qv_cloud_api import SERVICES, INSTALLATION
from test_r002_qv_services import services, HAError

h = load('qv_cloud_history')
p = load('qv_cloud_protocol')
services._async_cloud_history.__globals__['QvCloudError'] = p.QvCloudError
api = load('qv_cloud_api')
cloud = load('qv_cloud')
DEVICE = p.QvCloudDevice('PRIVATE_UID', 'PRIVATE_NAME', 'IDS', (0, 1))


def row(identity='PRIVATE_RECORD', *, uid=DEVICE.uid, channel=0, event=63, state=5):
    return (f'<data><id>{identity}</id><devid>{uid}</devid><chno>{channel}</chno>'
            f'<event>{event}</event><alarmstate>{state}</alarmstate>'
            '<time>2026-11-05 10:12:13</time><alarminfo>PRIVATE_BADGE</alarminfo>'
            '<recordresurl>https://private.invalid/token</recordresurl>'
            '<alarmsourcename>PRIVATE_PERSON</alarmsourcename></data>')


def page(rows='', cursor='123'):
    return envelope(f'<maxid>{cursor}</maxid><record>{rows}</record>')


class HistoryProtocolTests(unittest.TestCase):
    def test_exact_sdk_request_single_device_first_and_next_page(self):
        for number, cursor in ((0, '0'), (1, '123')):
            root = ET.fromstring(h.build_history_request('PRIVATE_SESSION', DEVICE, number, cursor))
            self.assertEqual(root.findtext('header/command'), 'client-query-recordlist')
            self.assertEqual(root.findtext('content/filter/devid/id'), DEVICE.uid)
            self.assertEqual(root.findtext('content/pagelinenum'), '15')
            self.assertEqual(root.findtext('content/pageno'), str(number))
            self.assertEqual(root.findtext('content/maxid'), cursor)

    def test_scoped_rows_method_names_and_privacy(self):
        result = h.parse_history_page(page(row() + row('2', channel=1, state=4)
            + row('3', uid='OTHER_DEVICE') + row('4', channel=2)), DEVICE)
        self.assertEqual(result.received, 4)
        self.assertEqual(result.rejected, 2)
        public = [item for identity, item in result.rows]
        self.assertEqual([item['channel'] for item in public], [1, 2])
        self.assertEqual([item['reported_method'] for item in public], ['rf_card', 'application'])
        self.assertNotIn('PRIVATE', json.dumps(public))
        self.assertNotIn('private.invalid', json.dumps(public))
        self.assertTrue(all(item['historical'] for item in public))
        self.assertNotIn('PRIVATE', repr(result))

    def test_lock_number_is_not_presented_as_a_badge_number(self):
        payload = page(row()).replace(b'PRIVATE_BADGE', b'2')
        result = h.parse_history_page(payload, DEVICE).rows[0][1]
        self.assertEqual(result['reported_output'], 2)
        self.assertNotIn('badge_id', result)
        self.assertEqual(result['reported_method'], 'rf_card')

    def test_unknown_event_is_not_a_ring_and_bad_time_not_exposed(self):
        payload = page(row(event=1234)).replace(b'2026-11-05 10:12:13', b'PRIVATE_REMOTE_TEXT')
        record = h.parse_history_page(payload, DEVICE).rows[0][1]
        self.assertEqual(record['kind'], 'unknown')
        self.assertIsNone(record['reported_time'])
        self.assertNotIn('reported_method', record)

    def test_malformed_and_oversized_pages_fail_closed(self):
        good = page(row())
        bad = (good.replace(b'<result>0</result>', b'<result>0</result><result>1</result>'),
               good.replace(b'<chno>0</chno>', b'<chno>0</chno><chno>1</chno>'),
               good.replace(b'<record>', b'<record><other/>'),
               good.replace(b'<maxid>123</maxid>', b'<maxid>PRIVATE</maxid>'),
               page(row() * 16), b'<!DOCTYPE envelope>' + good,
               good.replace(b'<content>', b'<content><record/>'),
               good.replace(b'<result>0</result>', b'<result>1</result>'))
        for payload in bad:
            with self.subTest(payload=payload[:30]), self.assertRaises(p.QvCloudError):
                h.parse_history_page(payload, DEVICE)

    def test_bounds(self):
        for number in (-1, 4, True, '1'):
            with self.assertRaises(p.QvCloudError):
                h.build_history_request('session', DEVICE, number)
        for cursor in ('', '1<2', 'a' * 65):
            with self.assertRaises(p.QvCloudError):
                h.build_history_request('session', DEVICE, cursor=cursor)


class HistoryAPITests(unittest.IsolatedAsyncioTestCase):
    def client(self, pages):
        client = api.QvCloudClient(SERVICES, INSTALLATION)
        client._account = SimpleNamespace(session_id='PRIVATE_SESSION')
        client._devices = {DEVICE.uid: DEVICE}
        client._registered = True
        client._request = AsyncMock(side_effect=pages)
        return client

    async def test_pages_are_deduplicated_and_capped_without_mutations(self):
        client = self.client([page(''.join(row(str(i)) for i in range(15))),
                              page(row('14') + row('15', event=19))])
        result = await client.async_history(DEVICE)
        self.assertEqual(result['record_count'], 16)
        self.assertEqual(result['duplicates_ignored'], 1)
        self.assertTrue(result['history_complete'])
        self.assertEqual(result['live_events_emitted'], 0)
        commands = [ET.fromstring(call.kwargs['data']).findtext('header/command')
                    for call in client._request.call_args_list]
        self.assertEqual(commands, ['client-query-recordlist'] * 2)
        self.assertNotIn('PRIVATE', json.dumps(result))

    async def test_limit_duplicate_page_and_bad_authorization(self):
        full = page(''.join(row(str(i)) for i in range(15)))
        for pages, limit, calls, count in (([full], 1, 1, 1),
                                         ([full, full], 30, 2, 15),
                                         ([page()], 30, 1, 0)):
            client = self.client(pages)
            result = await client.async_history(DEVICE, limit=limit)
            self.assertEqual(result['record_count'], count)
            self.assertEqual(client._request.await_count, calls)
        client = self.client([])
        for limit in (0, 61, True):
            with self.assertRaises(p.QvCloudError):
                await client.async_history(DEVICE, limit=limit)
        client._registered = False
        with self.assertRaises(p.QvCloudError):
            await client.async_history(DEVICE)
        client._request.assert_not_called()

    async def test_four_page_bound(self):
        client = self.client([page(''.join(row(str(i + n * 15)) for i in range(15)))
                              for n in range(4)])
        result = await client.async_history(DEVICE, limit=60)
        self.assertEqual(result['record_count'], 60)
        self.assertEqual(result['pages_read'], 4)
        self.assertFalse(result['history_complete'])

    async def test_foreign_device_response_never_claims_complete_history(self):
        client = self.client([page(row(uid='FOREIGN_DEVICE'))])
        result = await client.async_history(DEVICE)
        self.assertEqual(result['record_count'], 0)
        self.assertEqual(result['rows_rejected'], 1)
        self.assertFalse(result['history_complete'])

    async def test_timeout_and_cancel_no_retry(self):
        for cancel in (False, True):
            client = self.client([])
            async def wait(*args, **kwargs):
                await asyncio.Event().wait()
            client._request.side_effect = wait
            with patch.object(h, 'HISTORY_TIMEOUT', .02 if not cancel else 30):
                task = asyncio.create_task(client.async_history(DEVICE))
                await asyncio.sleep(.005)
                if cancel:
                    task.cancel()
                with self.assertRaises(asyncio.CancelledError if cancel else p.QvCloudError):
                    await task
            self.assertEqual(client._request.await_count, 1)


class HistoryControllerTests(unittest.IsolatedAsyncioTestCase):
    def controller(self):
        entry = SimpleNamespace(entry_id='test', unique_id='local', data={})
        controller = cloud.QVCloudDoorbell(None, entry, Mock())
        controller._eligible = Mock(return_value=True)
        controller._closed = False
        controller._subscribed = True
        controller._device = DEVICE
        controller._client = SimpleNamespace(async_history=AsyncMock(return_value={
            'records': [{'reported_time': 'PRIVATE_TIME'}], 'record_count': 1,
            'pages_read': 1, 'rows_rejected': 0, 'duplicates_ignored': 0,
            'history_complete': True}), async_close=AsyncMock())
        return controller

    async def test_no_ring_dispatch_or_private_diagnostic(self):
        controller = self.controller()
        result = await controller.async_history()
        self.assertEqual(result['record_count'], 1)
        controller._on_ring.assert_not_called()
        self.assertNotIn('PRIVATE', json.dumps(controller._history_diag))
        self.assertIsNone(controller._history_task)

    async def test_reconfigure_discards_result(self):
        controller = self.controller()
        async def revoked(*args, **kwargs):
            controller._eligible.return_value = False
            return {}
        controller._client.async_history.side_effect = revoked
        with self.assertRaises(p.QvCloudError):
            await controller.async_history()
        controller._on_ring.assert_not_called()

    async def test_busy_and_unload_cancel_history(self):
        controller = self.controller()
        async def wait(*args, **kwargs):
            await asyncio.Event().wait()
        client = controller._client
        client.async_history.side_effect = wait
        task = asyncio.create_task(controller.async_history())
        await asyncio.sleep(0)
        with self.assertRaisesRegex(p.QvCloudError, 'history_busy'):
            await controller.async_history()
        await controller._close_connections()
        self.assertTrue(task.cancelled())
        client.async_close.assert_awaited_once()
        self.assertIsNone(controller._history_task)


class HistoryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_identified_admin_and_control_permission_required(self):
        for admin, control, user_id in ((False, True, 'u'), (True, False, 'u'),
                                        (True, True, None), (True, True, 'u')):
            user = SimpleNamespace(is_admin=admin,
                permissions=SimpleNamespace(check_entity=Mock(return_value=control)))
            reader = SimpleNamespace(async_history=AsyncMock(return_value={'record_count': 0}))
            entity = SimpleNamespace(entity_id='sensor.test',
                hub=SimpleNamespace(stopped=False, qv_cloud=reader),
                hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user))))
            call = SimpleNamespace(context=SimpleNamespace(user_id=user_id), data={'limit': 15})
            if admin and control and user_id:
                self.assertEqual(await services._async_cloud_history(entity, call), {'record_count': 0})
                reader.async_history.assert_awaited_once_with(limit=15)
            else:
                with self.assertRaises(HAError):
                    await services._async_cloud_history(entity, call)
                reader.async_history.assert_not_called()
