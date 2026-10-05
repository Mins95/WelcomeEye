"""Synthetic SCT/UDT UDP loopback fixtures, never real WelcomeEye traffic."""

import json
import socket
import struct
import threading
import unittest
from unittest import mock

from load_integration import load

udt = load("experimental_udt")


def response(request, *, stage=1, **changes):
    """Independent synthetic native-layout builder; no hardware bytes."""
    words = list(struct.unpack("!12I", request[:48]))
    words[0:4] = [0x80000000, 0, 0, words[10]]
    words[9] = stage & 0xFFFFFFFF
    words[11] = 0x76543210 if stage == 1 else 0
    if stage == -1:
        words[10] = 0x1234567
    fields = {"header": 0, "additional": 1, "destination": 3,
              "version": 4, "socket_type": 5, "sequence": 6,
              "mss": 7, "window": 8, "peer_id": 10, "cookie": 11}
    for key, value in changes.items():
        words[fields[key]] = value
    return struct.pack("!12I", *words) + bytes(16)


class Peer:
    """One isolated IPv4 UDP peer with captured *synthetic* requests only."""

    def __init__(self, handler):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(0.4)
        self.port = self.socket.getsockname()[1]
        self.handler = handler
        self.requests = []
        self.errors = []
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self.run)

    def __enter__(self):
        self.thread.start()
        return self

    def recv(self):
        data, address = self.socket.recvfrom(2048)
        self.requests.append(data)
        return data, address

    def run(self):
        try:
            self.handler(self)
        except Exception as err:
            self.errors.append(err)
        finally:
            self.finished.set()

    def __exit__(self, *args):
        self.thread.join(1)
        self.socket.close()
        if self.thread.is_alive():
            raise AssertionError("Synthetic UDP peer did not stop")
        if self.errors:
            raise self.errors[0]


class UdtHandshakeTests(unittest.TestCase):
    def probe(self, peer, event=None, *, timeout=0.22):
        with mock.patch.object(udt, "PROBE_TIMEOUT", timeout):
            return udt.probe_handshake("127.0.0.1", peer.port,
                                       cancel_event=event or threading.Event())

    def normal(self, peer):
        initial, address = peer.recv()
        self.assertEqual(len(initial), 64)
        words = struct.unpack("!12I", initial[:48])
        self.assertEqual(words[:6], (0x80000000, 0, 0, 0, 4, 1))
        self.assertEqual(words[7:10], (1386, 1000, 1))
        self.assertEqual(words[11], 0)
        self.assertEqual(initial[48:], b"\x01\x00\x00\x7f" + bytes(12))
        peer.socket.sendto(response(initial), address)
        conclusion, address = peer.recv()
        end_words = struct.unpack("!12I", conclusion[:48])
        self.assertEqual(end_words[9], 0xFFFFFFFF)
        self.assertEqual(end_words[11], 0x76543210)
        self.assertEqual(end_words[:9], words[:9])
        self.assertEqual(end_words[10], words[10])
        peer.socket.sendto(response(conclusion, stage=-1), address)
        shutdown, _ = peer.recv()
        self.assertEqual(shutdown, struct.pack("!5I", 0x80050000, 0, 0, 0x1234567, 0))

    def test_native_layout_cookie_exchange_and_one_shutdown(self):
        with Peer(self.normal) as peer:
            result = self.probe(peer)
        self.assertEqual(result["status"], "observed")
        self.assertTrue(result["handshake_accepted"])
        self.assertTrue(result["challenge_received"])
        self.assertEqual(result["request_sent_count"], 2)
        self.assertEqual(result["request_attempt_count"], 2)
        self.assertEqual(len(peer.requests), 3)
        self.assertTrue(result["shutdown_sent"])
        self.assertTrue(result["udp_closed"])
        self.assertFalse(result["device_authenticated"])
        self.assertFalse(result["media_available"])

    def test_final_cookie_is_not_invented_as_required_echo(self):
        # Native final receiver doesn't check cookie; synthetic final uses 0.
        with Peer(self.normal) as peer:
            result = self.probe(peer)
        self.assertTrue(result["handshake_accepted"])

    def test_source_endpoint_validation(self):
        def handler(peer):
            initial, address = peer.recv()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as wrong:
                wrong.bind(("127.0.0.1", 0))
                wrong.sendto(response(initial, stage=-1), address)
                peer.socket.sendto(response(initial), address)
                conclusion, address = peer.recv()
                peer.socket.sendto(response(conclusion, stage=-1), address)
                peer.recv()
        with Peer(handler) as peer:
            result = self.probe(peer)
        self.assertTrue(result["handshake_accepted"])
        self.assertEqual(result["ignored_datagrams"], 1)

    def rejected(self, reason, packet_builder):
        def handler(peer):
            initial, address = peer.recv()
            peer.socket.sendto(packet_builder(initial), address)
        with Peer(handler) as peer:
            result = self.probe(peer)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["rejection_reason"], reason)
        self.assertFalse(result["handshake_accepted"])
        self.assertFalse(result["shutdown_attempted"])
        self.assertEqual(result["request_sent_count"], 1)
        self.assertTrue(result["udp_closed"])
        return result

    def test_final_requires_completed_cookie_challenge(self):
        self.rejected("conclusion_before_challenge", lambda r: response(r, stage=-1))

    def test_wrong_destination_socket(self):
        self.rejected("unexpected_destination_socket", lambda r: response(r, destination=7))

    def test_version_type_sequence_and_induction_echoes(self):
        cases = (
            ("unexpected_version", {"version": 5}),
            ("unexpected_socket_type", {"socket_type": 2}),
            ("invalid_initial_sequence", {"sequence": 0x80000000}),
            ("initial_sequence_not_echoed", {"sequence": 0}),
            ("induction_socket_not_echoed", {"peer_id": 8}),
            ("induction_parameters_not_echoed", {"mss": 1300}),
            ("induction_parameters_not_echoed", {"window": 900}),
            ("invalid_packet_size", {"mss": 1387}),
            ("invalid_flow_window", {"window": 0x7FFFFFFF}),
            ("invalid_peer_socket", {"peer_id": 0}),
            ("unexpected_control_type", {"header": 0x80010000}),
            ("nonzero_additional_information", {"additional": 1}),
        )
        for reason, changes in cases:
            with self.subTest(reason=reason, changes=changes):
                self.rejected(reason, lambda r, c=changes: response(r, **c))

    def test_short_and_oversized_datagrams_are_bounded(self):
        for size in (0, 15, 63, 65, 4096):
            with self.subTest(size=size):
                self.rejected("invalid_handshake_size", lambda r, n=size: response(r)[:n]
                              if n <= 64 else response(r) + bytes(n - 64))

    def test_duplicate_challenge_does_not_reset_deadline_or_send_immediately(self):
        def handler(peer):
            initial, address = peer.recv()
            packet = response(initial)
            peer.socket.sendto(packet, address)
            conclusion, address = peer.recv()
            for _ in range(10):
                peer.socket.sendto(packet, address)
            peer.socket.sendto(response(conclusion, stage=-1), address)
            peer.recv()
        with Peer(handler) as peer:
            result = self.probe(peer)
        self.assertTrue(result["handshake_accepted"])
        self.assertEqual(result["request_sent_count"], 2)

    def test_cookie_change_rejected(self):
        def handler(peer):
            initial, address = peer.recv()
            peer.socket.sendto(response(initial), address)
            peer.recv()
            peer.socket.sendto(response(initial, cookie=0x76543211), address)
        with Peer(handler) as peer:
            result = self.probe(peer)
        self.assertEqual(result["rejection_reason"], "cookie_changed")
        self.assertTrue(result["udp_closed"])

    def test_lost_induction_or_conclusion_never_retransmitted(self):
        def handler(peer):
            initial, address = peer.recv()
            peer.socket.sendto(response(initial), address)
            peer.recv()
            with self.assertRaises(TimeoutError):
                peer.recv()
        with Peer(handler) as peer:
            result = self.probe(peer, timeout=0.12)
        self.assertEqual(result["request_sent_count"], 2)
        self.assertFalse(result["handshake_accepted"])
        self.assertEqual(len(peer.requests), 2)
        self.assertEqual(result["status"], "timeout")

    def test_cancel_before_socket_creation_and_during_receive(self):
        stopped = threading.Event()
        stopped.set()
        with mock.patch.object(udt.socket, "socket") as create:
            result = udt.probe_handshake("127.0.0.1", 12345, cancel_event=stopped)
        create.assert_not_called()
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["udp_closed"])
        event = threading.Event()
        def handler(peer):
            peer.recv()
            event.set()
        with Peer(handler) as peer:
            result = self.probe(peer, event)
        self.assertEqual(result["status"], "cancelled")
        self.assertLess(result["elapsed_ms"], 150)
        self.assertTrue(result["udp_closed"])
        self.assertFalse(result["shutdown_attempted"])

    def test_deadline_and_finite_handshake_send_budget(self):
        def handler(peer):
            while True:
                try:
                    peer.recv()
                except TimeoutError:
                    return
        with Peer(handler) as peer:
            result = self.probe(peer, timeout=0.16)
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["request_sent_count"], 1)
        self.assertEqual(result["request_attempt_count"], 1)
        self.assertTrue(result["udp_closed"])
        self.assertLess(result["elapsed_ms"], 300)
        self.assertEqual(udt.PROBE_TIMEOUT, 5.0)

    def test_received_datagrams_limit(self):
        def handler(peer):
            initial, address = peer.recv()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as wrong:
                for _ in range(100):
                    wrong.sendto(response(initial), address)
        with Peer(handler) as peer:
            result = self.probe(peer)
        self.assertEqual(result["rejection_reason"], "datagram_limit")
        self.assertEqual(result["datagrams_seen"], 64)
        self.assertTrue(result["udp_closed"])

    def test_invalid_endpoint_never_resolves_or_opens_socket(self):
        for host, port in (("host.invalid", 1), ("0.0.0.0", 1), ("224.0.0.1", 1),
                           ("255.255.255.255", 1), ("127.0.0.1", True),
                           ("127.0.0.1", 0), ("127.0.0.1", 65536)):
            with self.subTest(host=host, port=port), \
                 mock.patch.object(udt.socket, "socket") as create:
                result = udt.probe_handshake(host, port, cancel_event=threading.Event())
            create.assert_not_called()
            self.assertEqual(result["rejection_reason"], "invalid_endpoint")

    def test_summary_privacy_even_on_socket_exception(self):
        with mock.patch.object(udt.socket, "socket", side_effect=OSError("private endpoint cookie")):
            result = udt.probe_handshake("127.0.0.1", 12345, cancel_event=threading.Event())
        text = json.dumps(result)
        self.assertNotIn("127.0.0.1", text)
        self.assertNotIn("12345", text)
        self.assertNotIn("private endpoint cookie", text)
        self.assertEqual(result["last_error_type"], "OSError")
        with Peer(self.normal) as peer:
            result = self.probe(peer)
        self.assertFalse(any(name in result for name in (
            "host", "port", "endpoint", "cookie", "socket_id", "sequence", "raw", "payload")))
        self.assertTrue(all(not isinstance(value, (bytes, bytearray)) for value in result.values()))

    def test_partial_send_records_attempt_without_success_and_closes(self):
        udp = mock.Mock()
        udp.sendto.return_value = 7
        with mock.patch.object(udt.socket, "socket", return_value=udp):
            result = udt.probe_handshake("127.0.0.1", 12345, cancel_event=threading.Event())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["request_attempt_count"], 1)
        self.assertEqual(result["request_sent_count"], 0)
        udp.recvfrom.assert_not_called()
        udp.close.assert_called_once()
        self.assertTrue(result["udp_closed"])

    def test_shutdown_failure_still_closes_once_without_replay(self):
        udp = mock.Mock()
        requests = []
        def send(data, endpoint):
            requests.append(data)
            if len(data) == 20:
                raise OSError("private address and cookie must not escape")
            return len(data)
        def recv(size):
            self.assertEqual(size, 65)
            return response(requests[-1], stage=1 if len(requests) == 1 else -1), ("127.0.0.1", 12345)
        udp.sendto.side_effect = send
        udp.recvfrom.side_effect = recv
        with mock.patch.object(udt.socket, "socket", return_value=udp):
            result = udt.probe_handshake("127.0.0.1", 12345, cancel_event=threading.Event())
        self.assertTrue(result["handshake_accepted"])
        self.assertTrue(result["shutdown_attempted"])
        self.assertFalse(result["shutdown_sent"])
        self.assertEqual(result["cleanup_error_type"], "OSError")
        self.assertTrue(result["udp_closed"])
        self.assertEqual([len(packet) for packet in requests], [64, 64, 20])
        self.assertNotIn("private address", json.dumps(result))
        udp.close.assert_called_once()

    def test_smaller_final_parameters_accepted_without_another_request(self):
        def handler(peer):
            initial, address = peer.recv()
            peer.socket.sendto(response(initial), address)
            conclusion, address = peer.recv()
            peer.socket.sendto(response(conclusion, stage=-1, mss=1200, window=500), address)
            peer.recv()
        with Peer(handler) as peer:
            result = self.probe(peer)
        self.assertTrue(result["handshake_accepted"])
        self.assertEqual(result["request_attempt_count"], 2)
        self.assertEqual(len(peer.requests), 3)

    def test_cookie_is_opaque_u32_including_zero_and_high_bit(self):
        for cookie in (0, 0x80000000, 0xFFFFFFFF):
            def handler(peer):
                initial, address = peer.recv()
                peer.socket.sendto(response(initial, cookie=cookie), address)
                conclusion, address = peer.recv()
                words = struct.unpack("!12I", conclusion[:48])
                self.assertEqual(words[9], 0xFFFFFFFF)
                self.assertEqual(words[11], cookie)
                peer.socket.sendto(response(conclusion, stage=-1), address)
                peer.recv()
            with self.subTest(cookie=cookie), Peer(handler) as peer:
                result = self.probe(peer)
            self.assertTrue(result["handshake_accepted"])
            self.assertEqual(result["request_attempt_count"], 2)


if __name__ == "__main__":
    unittest.main()
