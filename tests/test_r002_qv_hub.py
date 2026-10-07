"""SYNTHETIC QV records and mocked networking, never the user's datagram."""
import asyncio
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load, cap
from test_connect3_discovery import synthetic_packet, synthetic_record

qv = load('r002.qv')
hubs = load('r002.hub')
c3 = load('connect3.hub')


def packet(*, kind='IDS9417AW', cgi=443, media=0, tls=0, channels=1, address=None, uid=None):
    record = synthetic_record()
    record[0x188:0x19c] = kind.encode().ljust(20, b'\0')
    for offset, value in ((0x78, media), (0x1a8, cgi), (0x1cc, tls), (0x1a4, channels)):
        struct.pack_into('<H', record, offset, value)
    if address:
        record[0x64:0x68] = address
    if uid:
        record[0xc8:0x108] = uid.encode().ljust(64, b'\0')
    return synthetic_packet(record)


def observation(*packets):
    return dict(status='observed', request_sent_count=1, datagrams_seen=len(packets), responses=[
        dict(response_hex=data.hex(), truncated=False) for data in packets])


def hub(**data):
    return hubs.R002InvestigationHub(None, SimpleNamespace(data={'host': '192.0.2.1', **data}))


class SelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_shape_one_discovery_no_persistence(self):
        obs = {}
        raw = packet()
        with patch.object(qv, 'discover_qv', AsyncMock(return_value=observation(raw, raw))) as discover:
            endpoint = await qv.resolve_endpoint('192.0.2.1', obs)
        self.assertEqual(endpoint, dict(cgi_port=443, port=34567, transport='r002_tcp'))
        discover.assert_awaited_once_with('192.0.2.1', include_response=True)
        self.assertTrue(obs['discovery_model_matched'])
        for private in (raw.hex(), 'SYNTHETIC_PRIVATE_UID', '192.0.2.1', 'IDS9417AW'):
            self.assertNotIn(private, json.dumps(obs))

    async def test_unknown_conflicting_and_other_endpoints_fail_closed(self):
        cases = [observation(), observation(b'unknown'), observation(packet(kind='IDS94E6SW')),
                 {**observation(packet()), 'status': 'failed'},
                 observation(packet(tls=8443)), observation(packet(media=34567)),
                 observation(packet(cgi=80)), observation(packet(channels=2)),
                 observation(packet(address=bytes([192, 0, 2, 2]))),
                 observation(packet(), packet(uid='DIFFERENT_PRIVATE_UID'))]
        for result in cases:
            with self.subTest(result_index=cases.index(result)), patch.object(qv, 'discover_qv',
                    AsyncMock(return_value=result)) as discover:
                with self.assertRaises(qv.CGIError):
                    await qv.resolve_endpoint('192.0.2.1', {})
                self.assertEqual(discover.await_count, 1)


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_remains_offline_and_options_are_explicit(self):
        for data, camera, output in (({}, False, False), ({'experimental_video': True}, True, False),
            ({'experimental_video': True, 'experimental_outputs': True, 'opening_code': 'SYNTH'}, True, True)):
            h = hub(**data)
            with patch.object(qv, 'discover_qv', AsyncMock(side_effect=AssertionError('network'))):
                await h.start()
                self.assertEqual(h.capabilities.camera, camera)
                self.assertEqual(h.capabilities.strike, output)
                self.assertFalse(h.capabilities.connect3_read)
                self.assertTrue(h.capabilities.r002_qv_read)
                self.assertFalse(h.capabilities.local_ring)
                self.assertIsNone(h.live.task)
                self.assertEqual(h.variant, cap.DeviceVariant.R002)
                await h.stop()

    async def test_access_discovery_then_single_trusted_cgi_no_media(self):
        h = hub(auth_code='SYNTH_PASSWORD', certificate_sha256='b'*64)
        await h.start()
        order = []
        async def resolve(host, obs):
            order.append('discovery')
            return dict(cgi_port=443, port=34567, transport='r002_tcp')
        async def read(host, auth, **kw):
            order.append('cgi')
            self.assertEqual(kw['certificate_sha256'], 'b'*64)
            self.assertEqual(kw['operation'], 'access')
            kw['diagnostics'].update(authentication_status='accepted', request_sent_count=1)
            return dict(streamkey_received=True, authentication='cgi_accepted')
        with patch.object(hubs, 'resolve_endpoint', side_effect=resolve), patch.object(
                hubs, 'read_device', side_effect=read) as reader:
            result = await h.execute('access')
        self.assertEqual(order, ['discovery', 'cgi'])
        self.assertEqual(reader.await_count, 1)
        self.assertTrue(result['device_authenticated'])
        self.assertTrue(h.diagnostics()['device_authenticated'])
        self.assertIsNone(h.live.task)
        self.assertIsNone(h._task)
        for private in ('SYNTH_PASSWORD', 'b'*64, '192.0.2.1'):
            self.assertNotIn(private, json.dumps(h.diagnostics()))
        await h.stop()

    async def test_failed_discovery_prevents_credentials_and_no_retry(self):
        h = hub(auth_code='SYNTH_PASSWORD')
        await h.start()
        with patch.object(hubs, 'resolve_endpoint', AsyncMock(side_effect=qv.CGIError(
                'r002_discovery_model_not_supported'))) as resolve, patch.object(
                hubs, 'read_device', AsyncMock()) as read:
            result = await h.execute('access')
        self.assertEqual(resolve.await_count, 1)
        read.assert_not_called()
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['device_authenticated'])
        await h.stop()

    async def test_new_media_attempt_does_not_reuse_old_authentication_status(self):
        h = hub(auth_code='SYNTH_PASSWORD')
        h._authentication = dict(status='accepted', operation='access')
        with patch.object(hubs, 'resolve_endpoint', AsyncMock(side_effect=qv.CGIError('r002_discovery_failed'))):
            with self.assertRaises(qv.CGIError):
                await h.prepare_media_endpoint({})
        self.assertFalse(h.diagnostics()['device_authenticated'])

    async def test_raw_certificate_only_explicit_response_not_diagnostics(self):
        h = hub()
        await h.start()
        with patch.object(hubs, 'resolve_endpoint', AsyncMock(return_value={'cgi_port':443})), patch.object(
            hubs, 'inspect_certificate', AsyncMock(return_value=dict(status='observed', certificate_sha256='c'*64,
                certificate_metadata_status='parsed', certificate_serial_status='non_positive'))) as inspect:
            result = await h.execute('certificate', include_details=True)
        inspect.assert_awaited_once_with('192.0.2.1', port=443, include_details=True)
        self.assertEqual(result['certificate_sha256'], 'c'*64)
        self.assertNotIn('c'*64, json.dumps(h.diagnostics()))
        self.assertNotIn('certificate_sha256', h.entry.data)
        await h.stop()

    async def test_active_media_blocks_all_disruptive_diagnostics(self):
        h = hub(experimental_video=True)
        await h.start()
        h.live.consumers.add('viewer')
        for operation in (lambda:h.execute('access'), lambda:h.probe(),
                          lambda:h.check_certificate(), lambda:h.discover_qv()):
            with self.assertRaises(RuntimeError):
                await operation()
        h.live.consumers.clear()
        await h.stop()

    async def test_unload_cancels_discovery_and_no_cgi_or_late_callback(self):
        h = hub(auth_code='SYNTH_PASSWORD')
        await h.start()
        entered = asyncio.Event()
        async def wait(*args):
            entered.set()
            await asyncio.Event().wait()
        changed = Mock()
        h.subscribe(changed)
        with patch.object(hubs, 'resolve_endpoint', side_effect=wait), patch.object(hubs, 'read_device', AsyncMock()) as read:
            task = asyncio.create_task(h.execute('access'))
            await entered.wait()
            await h.stop()
            with self.assertRaises(asyncio.CancelledError):
                await task
        read.assert_not_called()
        self.assertEqual(changed.call_count, 1)
        self.assertIsNone(h._task)
        self.assertIsNone(h.live.task)
