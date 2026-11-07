"""A failed local listener retains safe connection evidence after closing."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from load_integration import load

ring = load('ring')


class RingNetworkDiagnosticsTests(unittest.TestCase):
    def test_terminal_failure_keeps_connection_summary_without_another_attempt(self):
        loop = SimpleNamespace(call_soon_threadsafe=lambda callback, *args: callback(*args))
        entry = SimpleNamespace(unique_id='PRIVATE_DEVICE', data={
            'host': '192.0.2.1', 'username': 'PRIVATE_USER', 'password': 'PRIVATE_PASSWORD'})
        on_ring, on_state = Mock(), Mock()
        listener = ring.RingListener(loop, entry, on_ring, on_state)
        summary = {'stage': 'failed_login', 'attempts': [{
            'endpoint_source': 'persisted_hint', 'discovery_send_attempts': 0,
            'discovery_datagrams_received': 0, 'authenticated': False}]}
        session = SimpleNamespace(
            connect=Mock(side_effect=ring.AuthenticationError('PRIVATE_DEVICE')),
            close=Mock(), connection_diagnostics=Mock(return_value=summary),
            framing_diagnostics=Mock(return_value={}), keepalive_count=0)
        with patch.object(ring, 'Session', return_value=session) as create:
            listener._worker()
        create.assert_called_once()
        session.connect.assert_called_once()
        session.close.assert_called_once()
        self.assertIsNone(listener.session)
        self.assertEqual(listener.connection_diagnostics, summary)
        self.assertNotIn('PRIVATE_', str(listener.connection_diagnostics))
        self.assertIsNone(listener.last_error_message)
        on_ring.assert_not_called()


if __name__ == '__main__':
    unittest.main()
