"""Rejected QV CGI credentials reach the player without opening media or ICE."""
import ast
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import ROOT, cap, load

cgi = load('connect3.cgi')
live = load('connect3.live')
c3 = load('connect3.hub')
r002 = load('r002.hub')


class Peer:
    connectionState = iceConnectionState = signalingState = 'new'
    iceGatheringState = 'new'

    def __init__(self, configuration):
        self.setRemoteDescription = AsyncMock()
        self.createAnswer = AsyncMock(side_effect=AssertionError('No answer without media'))
        self.addTrack = Mock()

    def on(self, event):
        return lambda handler: handler

    def getTransceivers(self):
        return [SimpleNamespace(kind='video', direction='recvonly')]


def rtc_fixture():
    """Execute the production offer and error path, replacing only HA/ICE I/O."""
    source = ast.parse((ROOT / 'rtc.py').read_text(encoding='utf-8'))
    cls = next(node for node in source.body
               if isinstance(node, ast.ClassDef) and node.name == 'WebRTCManager')
    cls.body = [node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in ('__init__', '_diag', 'offer')]
    helper = next(node for node in source.body
                  if isinstance(node, ast.FunctionDef) and node.name == '_stream_error')
    namespace = {
        'asyncio': asyncio, '_LOGGER': Mock(), 'RTCPeerConnection': Peer,
        'RTCSessionDescription': lambda **kwargs: kwargs,
        'WebRTCError': lambda **kwargs: SimpleNamespace(**kwargs),
        'WebRTCAnswer': Mock(side_effect=AssertionError('No WebRTC answer')),
        'DeviceTrack': lambda kind: SimpleNamespace(kind=kind),
        'Viewer': lambda pc: SimpleNamespace(pc=pc, lease=object(), tracks={}, jobs=set(),
                                             timeout=None, control_channel=None),
        'protect_peer_ice': Mock(), '_candidate_metadata_from_sdp': lambda sdp: ([], []),
        '_ice_configuration': lambda hass: (None, {}),
        'language_for': load('localization').language_for,
    }
    exec(compile(ast.Module(body=[helper, cls], type_ignores=[]), 'rtc-auth-fixture', 'exec'), namespace)
    return namespace


def data(family):
    return {'host': '192.0.2.10', 'protocol_family': family,
            'auth_code': 'SYNTHETIC_PRIVATE_CONNECTION_PASSWORD',
            'opening_code': 'SYNTHETIC_PRIVATE_OPENING_CODE',
            'experimental_video': True, 'media_transport': 'connect3_tcp',
            'media_tcp_approved': True, 'certificate_sha256': 'a' * 64,
            'r002_connection_mode': 'manual',
            'trust_endpoint': {'host': '192.0.2.10', 'cgi_port': 443,
                               'media_port': 34567, 'media_transport': 'connect3_tcp'},
            'tls_certificate_expires': {'cgi': '2099-01-01T00:00:00+00:00'}}


class AuthenticationPlayerTests(unittest.IsolatedAsyncioTestCase):
    async def test_cgi_rejection_is_specific_and_closed_in_both_profiles(self):
        for cls, family in ((c3.Connect3Hub, cap.ProtocolFamily.CONNECT3),
                            (r002.R002InvestigationHub, cap.ProtocolFamily.R002)):
            with self.subTest(family=family):
                hub = cls(None, SimpleNamespace(data=data(family)))
                await hub.start()
                namespace = rtc_fixture()
                manager_cls = namespace['WebRTCManager']
                manager = manager_cls.__new__(manager_cls)
                manager._frame, manager.close_all = Mock(), AsyncMock()
                manager._ice_transactions_cancelled = Mock()
                manager.__init__(hub)

                async def close(session_id, *, expected):
                    self.assertIs(manager.viewers.pop(session_id), expected)
                    expected.timeout.cancel()
                    await asyncio.gather(expected.timeout, return_exceptions=True)
                    await hub.release(expected.lease)

                manager.close = AsyncMock(side_effect=close)
                messages = []

                async def reject(host, auth, *, diagnostics, **kwargs):
                    diagnostics.update(authentication_status='rejected', tls_verified=True,
                                       tls_policy='certificate_pin')
                    raise cgi.CGIError('auth_code_rejected', device_error_code=401)

                try:
                    with patch.object(live, 'read_stream_material', AsyncMock(side_effect=reject)) as read, \
                            patch.object(live, 'QVSession', Mock(side_effect=AssertionError('No media socket'))) as media:
                        await manager.offer('SYNTHETIC_OFFER', 'viewer', messages.append)
                        read.assert_awaited_once()
                        media.assert_not_called()
                    self.assertEqual(len(messages), 1)
                    self.assertEqual(messages[0].code, 'auth_code_rejected')
                    self.assertIn('Reconfigurer', messages[0].message)
                    self.assertNotIn('SYNTHETIC', repr(messages))
                    manager.close.assert_awaited_once()
                    self.assertEqual(hub.live.observation['last_error_type'], 'CGIError')
                    self.assertEqual(hub.live.observation['last_error_reason'], 'auth_code_rejected')
                    self.assertEqual(hub.live.observation['failed_at_stage'], 'stream_key')
                    self.assertEqual(hub.webrtc_diagnostics['failed_at_stage'], 'acquiring_media')
                    self.assertFalse(hub.consumers)
                    self.assertIsNone(hub.live.task)
                    self.assertFalse(manager.viewers)
                    self.assertFalse(manager.offers)
                finally:
                    await hub.stop()

    def test_fixed_messages_use_language_and_never_remote_text(self):
        helper = rtc_fixture()['_stream_error']
        error = live.LocalAuthenticationRejected()
        for language, expected in (('fr', 'Reconfigurer'), ('en', 'Reconfigure'), ('es', 'Reconfigurar')):
            hub = SimpleNamespace(entry=SimpleNamespace(data={'language': language}))
            self.assertIn(expected, helper(hub, error).message)
        hub = SimpleNamespace(hass=SimpleNamespace(config=SimpleNamespace(language='en-US')))
        self.assertIn('Reconfigure', helper(hub, error).message)
        unknown = RuntimeError('PRIVATE_NETWORK_RESPONSE')
        unknown.reason = 'PRIVATE_NETWORK_RESPONSE'
        result = helper(hub, unknown)
        self.assertEqual(result.code, 'stream_failed')
        self.assertNotIn('PRIVATE', result.message)
        unknown.reason = 'other_channel_busy'
        self.assertEqual(helper(hub, unknown).code, 'channel_busy')


if __name__ == '__main__':
    unittest.main()
