"""Private discovery hints and bounded UDP diagnostics; synthetic network only."""
import asyncio
from copy import deepcopy
import json
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load

c = load('client')
p = load('protected')
d = load('discovery_store')
HOST = '192.0.2.1'
UID = 'SYNTHETIC-PRIVATE-UID'


def info(port=12345, *, host=HOST, uid=UID, protected=True):
    return p.DiscoveryInfo(uid, 1500, port, host, protected)


def entry(**changes):
    data = {'host': HOST, 'username': 'private-user', 'password': 'private-password',
            'device_variant': 'connect_v1', 'protocol_family': 'legacy_owsp'}
    data.update(changes)
    return SimpleNamespace(data=data, unique_id=UID, entry_id='synthetic-entry')


class NetworkTests(unittest.TestCase):
    def setUp(self):
        for name in ('_DISCOVERY_CACHE', '_DISCOVERY_HINTS', '_DISCOVERY_BINDINGS'):
            scope = patch.dict(getattr(c, name), {}, clear=True)
            scope.start()
            self.addCleanup(scope.stop)

    def socket(self):
        scope = patch.object(c.socket, 'socket')
        factory = scope.start()
        self.addCleanup(scope.stop)
        return factory.return_value.__enter__.return_value

    def test_noise_rejected_malformed_and_success_counts(self):
        udp = self.socket()
        udp.recvfrom.side_effect = [(b'other', ('192.0.2.2', 1500)),
            (b'malformed', (HOST, 1500)), (b'wrong-address', (HOST, 1500)),
            (b'wrong-uid', (HOST, 1500)), (b'accepted', (HOST, 1500))]
        _, unregister = c.register_discovery_binding(HOST, UID, True, Mock())
        self.addCleanup(unregister)
        expected = info()
        diagnostic = {}
        with patch.object(c, 'decode_discovery', side_effect=[p.ProtocolError('private data'),
                info(host='192.0.2.9'), info(uid='WRONG-UID'), expected]):
            self.assertIs(c.discover(HOST, diagnostics=diagnostic), expected)
        self.assertEqual(diagnostic['discovery_send_attempts'], 1)
        self.assertEqual(diagnostic['discovery_sends_succeeded'], 1)
        self.assertEqual(diagnostic['discovery_datagrams_received'], 5)
        self.assertEqual(diagnostic['discovery_unrelated_datagrams'], 1)
        self.assertEqual(diagnostic['discovery_malformed_datagrams'], 1)
        self.assertEqual(diagnostic['discovery_rejected_datagrams'], 2)
        self.assertIs(c._DISCOVERY_CACHE[HOST], expected)
        self.assertNotIn('private', json.dumps(diagnostic))
        self.assertNotIn(HOST, json.dumps(diagnostic))
        self.assertNotIn(UID, json.dumps(diagnostic))

    def test_send_and_receive_failures_are_sanitized_and_distinct(self):
        for stage in ('send', 'receive'):
            with self.subTest(stage=stage):
                udp = self.socket()
                getattr(udp, 'sendto' if stage == 'send' else 'recvfrom').side_effect = OSError('private host or secret')
                diagnostic = {}
                with self.assertRaises(OSError):
                    c.discover(HOST, diagnostics=diagnostic)
                self.assertEqual(diagnostic['discovery_send_attempts'], 1)
                self.assertEqual(diagnostic.get('discovery_sends_succeeded', 0), stage == 'receive')
                self.assertEqual(diagnostic['discovery_error_stage'], stage)
                self.assertEqual(diagnostic['discovery_error_type'], 'OSError')
                self.assertNotIn('secret', json.dumps(diagnostic))

    def test_flood_stops_at_datagram_bound(self):
        udp = self.socket()
        udp.recvfrom.return_value = (b'noise', ('192.0.2.2', 1500))
        diagnostic = {}
        with self.assertRaises(c.DiscoveryTimeout):
            c.discover(HOST, diagnostics=diagnostic)
        self.assertEqual(udp.recvfrom.call_count, 64)
        self.assertEqual(udp.sendto.call_count, 1)
        self.assertTrue(diagnostic['discovery_datagram_limit_reached'])

    def test_plain_cache_cannot_skip_fresh_identity_check(self):
        c._DISCOVERY_CACHE[HOST] = info(protected=False)
        udp = self.socket()
        udp.recvfrom.return_value = (b'fresh', (HOST, 1500))
        changed = info(uid='OTHER', protected=False)
        session = c.Session(HOST, 'user', 'secret', legacy_approval={'host': HOST, 'uid': UID})
        with patch.object(c, 'decode_discovery', return_value=changed), \
                patch.object(c.socket, 'create_connection') as tcp:
            with self.assertRaises(p.ProtocolError):
                session.connect()
        tcp.assert_not_called()
        udp.sendto.assert_called_once()

    def test_one_host_wait_does_not_block_another(self):
        blocker = threading.Lock()
        blocker.acquire()
        c._DISCOVERY_HOST_LOCKS[HOST] = blocker
        self.addCleanup(blocker.release)
        other = '192.0.2.2'
        expected = info(host=other)
        c._DISCOVERY_CACHE[other] = expected
        # The blocked host's lock is independent of the short registry lock.
        self.assertIs(c.discover(other), expected)


class StoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for name in ('_DISCOVERY_CACHE', '_DISCOVERY_HINTS', '_DISCOVERY_BINDINGS'):
            scope = patch.dict(getattr(c, name), {}, clear=True)
            scope.start()
            self.addCleanup(scope.stop)
        self.saved = None
        self.store = SimpleNamespace(async_load=AsyncMock(side_effect=lambda: deepcopy(self.saved)),
            async_save=AsyncMock(side_effect=self.save), async_remove=AsyncMock())
        scope = patch.object(d, '_store', return_value=self.store)
        scope.start()
        self.addCleanup(scope.stop)

    def save(self, data):
        self.saved = deepcopy(data)

    async def manager(self, value=None):
        manager = d.DiscoveryStore(SimpleNamespace(), value or entry())
        await manager.start()
        self.addAsyncCleanup(manager.close)
        return manager

    def authenticate(self, observed=None, *, status=1):
        observed = observed or info()
        session = c.Session(HOST, 'private-user', 'private-password')
        tcp = Mock()
        with patch.object(c, 'discover', return_value=observed), \
                patch.object(c.socket, 'create_connection', return_value=tcp), \
                patch.object(c, 'build_protected_login', return_value=b'login'), \
                patch.object(c, 'decode_login_reply', return_value=(status, 0, {}, 0)), \
                patch.object(session, 'read', return_value=[(502, b'fixture')]):
            session.connect()
        session.close()
        return session

    async def seed(self):
        manager = await self.manager()
        self.authenticate()
        await manager.close()
        c._DISCOVERY_CACHE.clear()
        return deepcopy(self.saved)

    async def test_only_authenticated_endpoint_survives_restart(self):
        manager = await self.manager()
        self.assertIsNone(self.saved)
        self.assertFalse(c._DISCOVERY_CACHE)
        self.authenticate()
        await manager.close()
        self.assertEqual(self.saved['endpoint']['tcp_port'], 12345)
        self.assertEqual(self.saved['policy'], 'lt_protected')
        self.assertNotIn('private-password', json.dumps(self.saved))
        self.assertNotIn('private-user', json.dumps(self.saved))
        self.assertFalse(c._DISCOVERY_BINDINGS)
        c._DISCOVERY_CACHE.clear()
        restarted = await self.manager()
        self.assertTrue(restarted.diagnostics()['hint_loaded'])
        diagnostic = {}
        with patch.object(c.socket, 'socket') as udp:
            selected = c.discover(HOST, diagnostics=diagnostic)
        udp.assert_not_called()
        self.assertEqual(selected, info())
        self.assertEqual(diagnostic['endpoint_source'], 'persisted_hint')

    async def test_config_credentials_policy_and_identity_changes_reject_hint(self):
        saved = await self.seed()
        cases = [entry(host='192.0.2.9'), entry(username='changed'), entry(password='changed'),
                 entry(device_variant='connect2_r001'), entry(legacy_unprotected_approval={'host': HOST, 'uid': UID})]
        different_uid = entry()
        different_uid.unique_id = 'OTHER'
        cases.append(different_uid)
        for changed in cases:
            with self.subTest(data=changed.data):
                self.saved = deepcopy(saved)
                manager = await self.manager(changed)
                self.assertFalse(c._DISCOVERY_HINTS)
                await manager.close()

    async def test_bad_or_expired_records_and_load_error_are_nonfatal(self):
        saved = await self.seed()
        records = [None, [], {'binding': 'incorrect'}, deepcopy(saved), deepcopy(saved), deepcopy(saved)]
        records[3]['authenticated_at'] = time.time() - d.HINT_MAX_AGE - 1
        records[4]['endpoint']['tcp_port'] = True
        records[5]['endpoint']['protected'] = False
        for record in records:
            self.saved = record
            manager = await self.manager()
            self.assertFalse(c._DISCOVERY_HINTS)
            await manager.close()
        self.store.async_load.side_effect = OSError('private file')
        manager = await self.manager()
        self.assertEqual(manager.diagnostics()['load_error_type'], 'OSError')
        self.assertNotIn('private', json.dumps(manager.diagnostics()))

    async def test_refused_hint_refreshes_once_before_one_login(self):
        await self.seed()
        manager = await self.manager()
        session = c.Session(HOST, 'private-user', 'private-password')
        tcp = Mock()
        with patch.object(c.socket, 'create_connection', side_effect=[TimeoutError(), tcp]) as connect, \
                patch.object(c.socket, 'socket') as udp_factory, \
                patch.object(c, 'decode_discovery', return_value=info(23456)), \
                patch.object(c, 'build_protected_login', return_value=b'login'), \
                patch.object(c, 'decode_login_reply', return_value=(1, 0, {}, 0)), \
                patch.object(session, 'read', return_value=[(502, b'fixture')]):
            udp_factory.return_value.__enter__.return_value.recvfrom.return_value = (b'fresh', (HOST, 1500))
            session.connect()
        session.close()
        self.assertEqual(connect.call_count, 2)
        tcp.sendall.assert_called_once_with(b'login')
        self.assertEqual([item['endpoint_source'] for item in session.connection_attempts],
                         ['persisted_hint', 'fresh_discovery'])
        await manager.close()
        self.assertEqual(self.saved['endpoint']['tcp_port'], 23456)

    async def test_reload_cache_failure_cannot_fall_back_to_persisted_hint(self):
        await self.seed()
        cached = info()
        c._DISCOVERY_CACHE[HOST] = cached
        manager = await self.manager()
        self.assertFalse(c._DISCOVERY_HINTS)
        self.assertIs(manager._binding['confirmed'], cached)
        session = c.Session(HOST, 'private-user', 'private-password')
        with patch.object(c.socket, 'create_connection', side_effect=ConnectionRefusedError()) as connect, \
                patch.object(c.socket, 'socket') as udp_factory, \
                patch.object(c, 'decode_discovery', return_value=info(23456)), \
                patch.object(c, 'build_protected_login') as login:
            udp_factory.return_value.__enter__.return_value.recvfrom.return_value = (b'fresh', (HOST, 1500))
            with self.assertRaises(ConnectionRefusedError):
                session.connect()
        self.assertEqual(connect.call_count, 2)
        self.assertEqual([item['endpoint_source'] for item in session.connection_attempts],
                         ['cache', 'fresh_discovery'])
        login.assert_not_called()
        await manager.close()
        self.assertIsNone(self.saved)

    async def test_hint_auth_failure_invalidates_without_retry(self):
        await self.seed()
        manager = await self.manager()
        session = c.Session(HOST, 'private-user', 'private-password')
        tcp = Mock()
        with patch.object(c.socket, 'create_connection', return_value=tcp) as connect, \
                patch.object(c.socket, 'socket') as udp, \
                patch.object(c, 'build_protected_login', return_value=b'login'), \
                patch.object(c, 'decode_login_reply', return_value=(2, 0, {}, 0)), \
                patch.object(session, 'read', return_value=[(502, b'fixture')]):
            with self.assertRaises(c.AuthenticationError):
                session.connect()
        self.assertEqual(connect.call_count, 1)
        tcp.sendall.assert_called_once_with(b'login')
        udp.assert_not_called()
        await manager.close()
        self.assertIsNone(self.saved)
        self.assertFalse(c._DISCOVERY_HINTS)

    async def test_failed_hint_refresh_is_bounded_before_credentials(self):
        await self.seed()
        manager = await self.manager()
        session = c.Session(HOST, 'private-user', 'private-password')
        with patch.object(c.socket, 'create_connection', side_effect=TimeoutError()) as connect, \
                patch.object(c.socket, 'socket') as udp_factory, \
                patch.object(c, 'decode_discovery', return_value=info(23456)), \
                patch.object(c, 'build_protected_login') as login:
            udp_factory.return_value.__enter__.return_value.recvfrom.return_value = (b'fresh', (HOST, 1500))
            with self.assertRaises(TimeoutError):
                session.connect()
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(len(session.connection_attempts), 2)
        login.assert_not_called()
        await manager.close()
        self.assertIsNone(self.saved)
        self.assertFalse(c._DISCOVERY_CACHE)

    async def test_registration_change_and_uid_mismatch_never_send_credentials(self):
        manager = await self.manager()
        with self.assertRaises(p.ProtocolError):
            self.authenticate(info(uid='OTHER'))
        self.assertIsNone(self.saved)
        await manager.close()
        c._authenticated_discovery(HOST, info(), manager._binding)
        await asyncio.sleep(0)
        self.assertIsNone(self.saved)

    async def test_stale_auth_or_callback_cannot_overwrite_newer_endpoint(self):
        manager = await self.manager()
        old, newer = info(), info(23456)
        c._DISCOVERY_CACHE[HOST] = newer
        c._authenticated_discovery(HOST, old, manager._binding)
        await asyncio.sleep(0)
        self.assertIsNone(self.saved)
        manager._accept(2, newer)
        manager._accept(1, old)
        await manager.close()
        self.assertEqual(self.saved['endpoint']['tcp_port'], 23456)

    async def test_plain_policy_never_loads_or_saves_hint(self):
        manager = await self.manager(entry(legacy_unprotected_approval={'host': HOST, 'uid': UID}))
        manager._accept(1, info(protected=False))
        await manager.close()
        self.store.async_load.assert_not_called()
        self.store.async_save.assert_not_called()

    async def test_slow_final_flush_is_bounded_and_removal_waits_for_write(self):
        manager = await self.manager()
        entered, release = asyncio.Event(), asyncio.Event()
        events = []
        async def save(record):
            entered.set()
            await release.wait()
            events.append('saved')
        self.store.async_save.side_effect = save
        self.store.async_remove.side_effect = lambda: events.append('removed')
        self.authenticate()
        await entered.wait()
        with patch.object(d, 'STORE_TIMEOUT', .01):
            await manager.close()
        self.assertEqual(manager.diagnostics()['save_error_type'], 'TimeoutError')
        self.assertFalse(c._DISCOVERY_BINDINGS)
        self.assertFalse(manager._save_task.done())
        removal = asyncio.create_task(d.async_remove_discovery_store(SimpleNamespace(), 'synthetic-entry'))
        await asyncio.sleep(0)
        self.assertFalse(removal.done())
        release.set()
        await removal
        self.assertEqual(events, ['saved', 'removed'])

    async def test_entry_removal_uses_only_owned_store(self):
        await d.async_remove_discovery_store(SimpleNamespace(), 'synthetic-entry')
        self.store.async_remove.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
