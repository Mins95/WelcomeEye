"""APK legacy path: synthetic data and loopback servers only, no hardware."""
import asyncio
import json
import socket
import struct
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
from test_r002_config_flow import flow_module

p = load('protected')
c = load('client')
talk = load('talkback')
outputs = load('v1_control')
protocol = load('protocol')


def info(port=12345, host='127.0.0.1', uid='SYNTHETIC', protected=False):
    return p.DiscoveryInfo(uid, 1500, port, host, protected)


def read_packet(sock):
    def exact(n):
        data = b''
        while len(data) < n:
            part = sock.recv(n - len(data))
            if not part:
                raise EOFError
            data += part
        return data
    n = struct.unpack('>I', exact(4))[0]
    if not 4 <= n <= 4096:
        raise AssertionError('Unbounded test request')
    return p.parse_tlvs(exact(n)[4:])


class ProtocolTests(unittest.TestCase):
    def test_native_login_layout_and_no_hash(self):
        packet = p.build_plain_login('admin', 'local-code')
        self.assertEqual(len(packet), 88)
        parts = p.parse_tlvs(packet[8:])
        self.assertEqual(parts[0], (40, b'\x05\0\0\0'))
        kind, body = parts[1]
        self.assertEqual((kind, len(body)), (41, 68))
        self.assertEqual(body[1:33].rstrip(b'\0'), b'admin')
        self.assertEqual(body[33:49].rstrip(b'\0'), b'local-code')
        self.assertEqual(body[60:68], struct.pack('<I', 1 << 16) + b'\x01\x02\0\0')
        self.assertNotIn(protocol.encode_password('local-code').encode(), packet)
        self.assertEqual(p.decode_plain_login_reply(b'\x01\0\x03\0'), (1, 3))

    def test_invalid_fields_and_reply_lengths(self):
        for user, secret in [('x'*32,'p'), ('a','x'*16), ('a','é'*8), ('a','p\0')]:
            with self.assertRaises(ValueError):
                p.build_plain_login(user, secret)
        for body in (b'', bytes(3), bytes(5), bytes(12)):
            with self.assertRaises(p.ProtocolError):
                p.decode_plain_login_reply(body)
            with self.assertRaises(p.ProtocolError):
                p.decode_plain_av_reply(body)

    def test_av_and_talk_native_frames(self):
        self.assertEqual(p.parse_tlvs(p.build_plain_av_request(16,1,2)[8:]), [(5007,b'\x10\x01\x02\0')])
        self.assertEqual(p.parse_tlvs(p.build_plain_av_request(17,1,2,stop=True)[8:]), [(5009,b'\x11\x01\x02\0')])
        self.assertEqual(p.decode_plain_av_reply(b'\x01\0\0\0'), (0,1,0))
        session = SimpleNamespace(legacy_plain=True)
        for enabled, value in ((True,1), (False,2)):
            self.assertEqual(p.parse_tlvs(talk.talk_request(session, enabled)[8:]),
                             [(331, bytes(4)+bytes((value,0,0,0)))])

    def test_consent_and_cache_do_not_allow_downgrade_or_identity_change(self):
        observed = info()
        with patch.dict(c._DISCOVERY_CACHE, {'127.0.0.1':observed}, clear=True):
            with self.assertRaises(c.LegacyApprovalRequired):
                c.discover('127.0.0.1')
            self.assertIs(c.discover('127.0.0.1', allow_unprotected=True), observed)
        for approval, discovered in ((None, observed), ({'host':'elsewhere','uid':'SYNTHETIC'},observed),
                ({'host':'127.0.0.1','uid':'OTHER'},observed),
                ({'host':'127.0.0.1','uid':'SYNTHETIC'},info(protected=True))):
            with self.subTest(approval=approval), patch.object(c, 'discover', return_value=discovered), patch.object(c.socket,'create_connection') as connect:
                s = c.Session('127.0.0.1','admin','local-code',legacy_approval=approval)
                with self.assertRaises(p.ProtocolError):
                    s.connect()
                connect.assert_not_called()
                self.assertTrue(s.closed.is_set())

    def test_real_loopback_login_firmware_media_and_cleanup(self):
        received, errors = [], []
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0)); listener.listen(); listener.settimeout(4)
            observed = info(listener.getsockname()[1])
            def peer():
                try:
                    with listener.accept()[0] as conn:
                        conn.settimeout(4)
                        received.extend(read_packet(conn))
                        # Fragmented reply exercises framing without another reader.
                        reply = p.owsp(p.tlv(42,b'\x01\0\0\0'))
                        conn.sendall(reply[:6]); conn.sendall(reply[6:])
                        received.extend(read_packet(conn))
                        conn.sendall(p.owsp(p.tlv(470,b'{"AppCom":"401001001","UID":"private"}\0')))
                        received.extend(read_packet(conn))
                        conn.sendall(p.owsp(p.tlv(5008,b'\x01\0\0\0')+p.tlv(98,b'synthetic-audio')))
                        received.extend(read_packet(conn))
                        received.extend(read_packet(conn))
                        self.assertEqual(conn.recv(1), b'')
                except BaseException as exc:
                    errors.append(exc)
            thread = threading.Thread(target=peer); thread.start()
            s = c.Session('127.0.0.1','admin','local-code',legacy_approval={'host':'127.0.0.1','uid':'SYNTHETIC'})
            try:
                with patch.object(c,'discover',return_value=observed):
                    s.connect()
                self.assertEqual(s.read_legacy_firmware(), {'status':'received','source':'lt_tlv_470','fields':{'AppCom':'401001001'}})
                s.send_start_av()
                self.assertEqual(s.read(), [(5008,b'\x01\0\0\0'),(98,b'synthetic-audio')])
                s.send_stop_av(); s.send_session_stop()
                diag = s.framing_diagnostics()
                self.assertEqual(diag['login_transport'],'legacy_plain')
                self.assertEqual(diag['login_response_type'],42)
                self.assertNotIn('local-code',json.dumps(diag))
                self.assertNotIn('SYNTHETIC',json.dumps(diag))
            finally:
                s.close(); thread.join(5)
            self.assertFalse(thread.is_alive())
            if errors: raise errors[0]
        self.assertEqual([kind for kind,_ in received], [40,41,469,5007,5009,5005])

    def test_firmware_missing_malformed_and_bounded(self):
        s = c.Session('127.0.0.1','admin','local-code')
        s.legacy_plain=True; s.connection_stage='authenticated'; s.send_packet=Mock()
        for replies, status in ((TimeoutError(),'not_reported'), ([(470,b'not-json')],'failed'),
                ([(470,b'{"UID":"not-a-version"}')],'not_reported'),
                ([(98,bytes(65537))],'response_limit')):
            s.read = Mock(side_effect=replies if isinstance(replies,Exception) else None,
                          return_value=replies)
            self.assertEqual(s.read_legacy_firmware()['status'],status)
            self.assertIsNone(s._login_deadline)

    def test_refused_login_has_one_attempt_no_media_and_closes(self):
        for reply in (b'\x02\0\x07\0', b'\x09\0\0\0', bytes(3)):
            sock=Mock()
            s=c.Session('127.0.0.1','admin','local-code',
                legacy_approval={'host':'127.0.0.1','uid':'SYNTHETIC'})
            s.read=Mock(return_value=[(42,reply)])
            with patch.object(c,'discover',return_value=info()), patch.object(c.socket,'create_connection',return_value=sock):
                with self.assertRaises((c.AuthenticationError,p.ProtocolError)):
                    s.connect()
            self.assertEqual(sock.sendall.call_count,1)
            self.assertEqual([kind for kind,_ in p.parse_tlvs(sock.sendall.call_args.args[0][8:])],[40,41])
            self.assertTrue(s.closed.is_set())
            sock.close.assert_called_once()

    def test_protected_session_never_accepts_clear_reply(self):
        sock=Mock()
        s=c.Session('127.0.0.1','admin','local-code')
        s.read=Mock(side_effect=[[(42,b'\x01\0\0\0')], ConnectionError('synthetic EOF')])
        with patch.object(c,'discover',return_value=info(protected=True)), patch.object(c.socket,'create_connection',return_value=sock):
            with self.assertRaises(ConnectionError): s.connect()
        self.assertEqual(sock.sendall.call_count,1)
        self.assertEqual([kind for kind,_ in p.parse_tlvs(sock.sendall.call_args.args[0][8:])],[40,501])
        self.assertNotIn(b'local-code',sock.sendall.call_args.args[0])
        self.assertIsNone(s.login_response_type)
        self.assertTrue(s.closed.is_set())


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_approval_decline_and_persistence(self):
        for approved in (False,True):
            flow = flow_module.WelcomeEyeConfigFlow()
            # Fixture's exception is injected by its actual flow loader.
            discovery_error = flow_module.LegacyApprovalRequired(info(host='192.0.2.1'))
            result={'uid':'SYNTHETIC','firmware':{'status':'received','fields':{'AppCom':'401001001'}}}
            flow.hass=SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=[discovery_error,result]))
            step=await flow.async_step_legacy({'host':'192.0.2.1','username':'admin','password':'local-code'})
            self.assertEqual(step['step_id'],'legacy_unprotected')
            self.assertNotIn('local-code',repr(step))
            self.assertEqual(flow.hass.async_add_executor_job.await_count,1)
            end=await flow.async_step_legacy_unprotected({'confirm':approved})
            self.assertIsNone(flow._legacy_pending)
            if approved:
                self.assertEqual(end['data']['legacy_unprotected_approval'],{'host':'192.0.2.1','uid':'SYNTHETIC'})
                self.assertEqual(end['data']['device_variant'],'connect_v1')
                self.assertEqual(end['data']['legacy_firmware'],result['firmware'])
                self.assertEqual(flow.hass.async_add_executor_job.await_count,2)
                # Private configuration survives restart without renewed consent.
                reloaded=json.loads(json.dumps(end['data']))
                self.assertEqual(c.Session(reloaded['host'],reloaded['username'],reloaded['password'],
                    legacy_approval=reloaded['legacy_unprotected_approval']).legacy_approval,
                    end['data']['legacy_unprotected_approval'])
            else:
                self.assertEqual(end['reason'],'legacy_declined')
                self.assertEqual(flow.hass.async_add_executor_job.await_count,1)
            self.assertEqual((await flow.async_step_legacy_unprotected({'confirm':True}))['reason'], 'legacy_approval_expired')


class OutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_outputs_use_425_and_only_accept_426_once(self):
        for output in (0,1):
            sent=[]
            s=SimpleNamespace(legacy_plain=True,info=SimpleNamespace(uid='SYNTHETIC'),channel=16,
                stream=1,mode=2,closed=threading.Event(),write_lock=threading.RLock(),send_packet=sent.append)
            ctrl=SimpleNamespace(entry=SimpleNamespace(unique_id='SYNTHETIC',data={'password':'local-code'}),
                closed=threading.Event(),request_send_attempt_count=0,request_sent_count=0,response_count=0,
                tlv_counts={},decode_failures=0,cleanup_error_type=None,last_result=None,last_reason=None)
            hub=SimpleNamespace(session=s,acquire=AsyncMock(),release=AsyncMock())
            ctrl.hub=hub
            manager=outputs.V1MediaOutput(ctrl)
            task=asyncio.create_task(manager.execute(output))
            await asyncio.sleep(0)
            manager.send_pending(s); manager.send_pending(s)
            self.assertEqual(len(sent),1)
            self.assertEqual(p.parse_tlvs(sent[0][8:]),[(425,protocol.build_legacy_unlock('local-code',output+1))])
            manager.observe(s,[(506,b'wrong-transport')])
            self.assertFalse(task.done())
            manager.observe(s,[(426,b'\x01\0\0\0')])
            await asyncio.wait_for(task,1)
            self.assertEqual(ctrl.last_result,1)
            self.assertEqual(ctrl.request_send_attempt_count,1)
            hub.release.assert_awaited_once()
