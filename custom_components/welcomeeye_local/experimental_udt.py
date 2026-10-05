"""Bounded, explicit SCT/UDT4 handshake observation; no application traffic.

Wire evidence: WelcomeEye APK libglnkio.so (ARMv7), socketpatrol
0xa72dc--0xa7358, processCtrl 0xa5fb0--0xa6018 / 0xa628c--0xa6374,
SCTSetOption 0xa334a--0xa3370 and sl_connect 0xa3858--0xa3866.
The UDT draft describes the control header, cookie exchange and shutdown:
https://datatracker.ietf.org/doc/html/draft-gg-udt-03#section-5

Only the native handshake subset is implemented. This is not a reliable data
transport, device authentication or evidence that an OWSP session is available.
No discovery, port selection, credentials, media, ACK/NAK or data is sent here.
The caller must authorize and coordinate the one explicitly supplied endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
import errno
import ipaddress
import secrets
import socket
import struct
import threading
import time
from typing import Any

# Local probe limits. Native SCT can retransmit a handshake; this diagnostic
# intentionally sends the induction and conclusion at most once each.
PROBE_TIMEOUT = 5.0
POLL_INTERVAL = 0.05
MAX_HANDSHAKE_REQUESTS = 2
MAX_DATAGRAMS = 64

# APK socketpatrol writes these exact fields; do not infer a messaging mode
# from the socket type number or switch it in response to a failed handshake.
_VERSION = 4
_SOCKET_TYPE = 1
_MSS = 1386  # SCTSetOption(3, 1400) stores 1400 - 14 before sl_connect.
_WINDOW = 1000
_MASK31 = 0x7FFFFFFF
_CONTROL = struct.Struct("!IIII")
_HANDSHAKE = struct.Struct("!IIIIIiII16s")
_HANDSHAKE_SIZE = _CONTROL.size + _HANDSHAKE.size


class _ProtocolError(Exception):
    """A fixed, non-sensitive reason; never include received field values."""


class _Cancelled(Exception):
    """The owner requested shutdown."""


@dataclass(frozen=True, repr=False)
class _Reply:
    sequence: int
    mss: int
    window: int
    request: int
    socket_id: int
    cookie: int


def _request(sequence: int, socket_id: int, peer_ip: bytes,
             cookie: int | None) -> bytes:
    # sl_connect copies inet_addr into the first IP word; sctsendto reverses
    # every control-body word. Thus IPv4 octets are reversed on this wire.
    return _CONTROL.pack(0x80000000, 0, 0, 0) + _HANDSHAKE.pack(
        _VERSION, _SOCKET_TYPE, sequence, _MSS, _WINDOW,
        1 if cookie is None else -1, socket_id,
        0 if cookie is None else cookie, peer_ip[::-1] + bytes(12),
    )


def _reply(data: bytes, local_socket_id: int) -> _Reply:
    if len(data) != _HANDSHAKE_SIZE:
        raise _ProtocolError("invalid_handshake_size")
    packet_type, additional, _timestamp, destination = _CONTROL.unpack_from(data)
    if packet_type != 0x80000000:
        raise _ProtocolError("unexpected_control_type")
    if additional != 0:
        raise _ProtocolError("nonzero_additional_information")
    if destination != local_socket_id:
        raise _ProtocolError("unexpected_destination_socket")
    version, socket_type, sequence, mss, window, request, peer_id, cookie, _ip = (
        _HANDSHAKE.unpack_from(data, _CONTROL.size)
    )
    if version != _VERSION:
        raise _ProtocolError("unexpected_version")
    if socket_type != _SOCKET_TYPE:
        raise _ProtocolError("unexpected_socket_type")
    if sequence > _MASK31:
        raise _ProtocolError("invalid_initial_sequence")
    if not 100 < mss <= _MSS:
        raise _ProtocolError("invalid_packet_size")
    if not 10 < window <= _WINDOW:
        raise _ProtocolError("invalid_flow_window")
    if peer_id == 0 or peer_id > _MASK31:
        raise _ProtocolError("invalid_peer_socket")
    if request not in (1, -1):
        raise _ProtocolError("unexpected_handshake_stage")
    return _Reply(sequence, mss, window, request, peer_id, cookie)


def probe_handshake(host: str, port: int, *, cancel_event: threading.Event) -> dict[str, Any]:
    """Observe one native-layout handshake and close it within five seconds.

    IPv4 literals only, one UDP socket, one induction and one conclusion
    at most, and sixty-four bounded receives. Successful negotiation
    is followed by one native-layout shutdown; application data is never sent.
    Cancellation is polled every fifty milliseconds. Only allowlisted summary
    values escape this function, including when socket operations fail.
    """
    started = time.monotonic()
    deadline = started + PROBE_TIMEOUT
    result: dict[str, Any] = {
        "operation": "udt_handshake",
        "status": "failed",
        "last_stage": "validating_endpoint",
        "last_error_type": None,
        "rejection_reason": None,
        "handshake_accepted": False,
        "challenge_received": False,
        "request_attempt_count": 0,
        "request_sent_count": 0,
        "datagrams_seen": 0,
        "ignored_datagrams": 0,
        "shutdown_attempted": False,
        "shutdown_sent": False,
        "cleanup_error_type": None,
        "udp_closed": False,
        "elapsed_ms": 0,
        "device_authenticated": False,
        "media_available": False,
        "hardware_validated": False,
    }
    udp: socket.socket | None = None
    peer_id: int | None = None
    endpoint: tuple[str, int] | None = None
    try:
        if not isinstance(host, str) or type(port) is not int or not 1 <= port <= 65535:
            raise _ProtocolError("invalid_endpoint")
        try:
            address = ipaddress.IPv4Address(host)
        except ipaddress.AddressValueError:
            raise _ProtocolError("invalid_endpoint") from None
        if address.is_unspecified or address.is_multicast or int(address) == 0xFFFFFFFF:
            raise _ProtocolError("invalid_endpoint")
        if not isinstance(cancel_event, threading.Event):
            raise _ProtocolError("invalid_cancellation_event")
        if cancel_event.is_set():
            raise _Cancelled
        endpoint = (str(address), port)
        local_socket_id = secrets.randbelow(_MASK31) + 1
        sequence = secrets.randbelow(_MASK31) + 1
        cookie: int | None = None
        current_request = _request(sequence, local_socket_id, address.packed, cookie)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(("0.0.0.0", 0))
        request_pending = True
        result["last_stage"] = "awaiting_challenge"
        while True:
            now = time.monotonic()
            if cancel_event.is_set():
                raise _Cancelled
            if now >= deadline:
                raise TimeoutError
            if request_pending:
                if result["request_attempt_count"] >= MAX_HANDSHAKE_REQUESTS:
                    raise _ProtocolError("handshake_send_limit")
                udp.settimeout(min(POLL_INTERVAL, deadline - now))
                result["request_attempt_count"] += 1
                if udp.sendto(current_request, endpoint) != len(current_request):
                    raise OSError
                result["request_sent_count"] += 1
                request_pending = False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            if result["datagrams_seen"] >= MAX_DATAGRAMS:
                raise _ProtocolError("datagram_limit")
            udp.settimeout(min(POLL_INTERVAL, remaining))
            try:
                # A sixty-five-byte cap distinguishes oversized handshakes
                # without storing their payload or allocating from peer sizes.
                packet, source = udp.recvfrom(_HANDSHAKE_SIZE + 1)
            except TimeoutError:
                continue
            except OSError as err:
                # Winsock raises instead of returning the truncated prefix.
                # No larger read is needed or allowed to classify its size.
                if err.errno == errno.EMSGSIZE or getattr(err, "winerror", None) == 10040:
                    result["datagrams_seen"] += 1
                    raise _ProtocolError("invalid_handshake_size") from None
                raise
            result["datagrams_seen"] += 1
            if source != endpoint:
                result["ignored_datagrams"] += 1
                continue
            reply = _reply(packet, local_socket_id)
            if reply.sequence != sequence:
                raise _ProtocolError("initial_sequence_not_echoed")
            if reply.request == 1:
                # processCtrl @0xa5fb0--0xa5fe6 validates all induction
                # echoes, then stores the server cookie for the conclusion.
                if reply.socket_id != local_socket_id:
                    raise _ProtocolError("induction_socket_not_echoed")
                if reply.mss != _MSS or reply.window != _WINDOW:
                    raise _ProtocolError("induction_parameters_not_echoed")
                if cookie is not None and cookie != reply.cookie:
                    raise _ProtocolError("cookie_changed")
                if cookie is None:
                    cookie = reply.cookie
                    current_request = _request(sequence, local_socket_id, address.packed, cookie)
                    result["challenge_received"] = True
                    result["last_stage"] = "awaiting_conclusion"
                    request_pending = True
                # Duplicate induction responses never reset the deadline or
                # cause another conclusion transmission.
                continue
            if cookie is None:
                raise _ProtocolError("conclusion_before_challenge")
            # The native final receiver @0xa628c validates ISN / MSS and
            # adopts peer ID; it does not require an echoed final cookie.
            peer_id = reply.socket_id
            result["handshake_accepted"] = True
            result["status"] = "observed"
            result["last_stage"] = "handshake_complete"
            break
    except _Cancelled:
        result["status"] = "cancelled"
        result["last_error_type"] = "CancelledError"
    except _ProtocolError as err:
        result["status"] = "rejected"
        result["last_error_type"] = "UDTProtocolError"
        result["rejection_reason"] = str(err)
    except TimeoutError:
        result["status"] = "timeout"
        result["last_error_type"] = "TimeoutError"
    except OSError:
        result["status"] = "failed"
        result["last_error_type"] = "OSError"
    finally:
        if udp is not None:
            if peer_id is not None and endpoint is not None:
                result["shutdown_attempted"] = True
                try:
                    udp.settimeout(max(0.0, min(POLL_INTERVAL, deadline - time.monotonic())))
                    # Native socketpatrol @0xa6e06--0xa6e30 sends twenty
                    # bytes. One send is our bounded probe cleanup policy.
                    shutdown = _CONTROL.pack(0x80050000, 0, 0, peer_id) + bytes(4)
                    result["shutdown_sent"] = udp.sendto(shutdown, endpoint) == len(shutdown)
                except OSError:
                    result["cleanup_error_type"] = "OSError"
            try:
                udp.close()
                result["udp_closed"] = True
            except OSError:
                result["cleanup_error_type"] = "OSError"
        else:
            result["udp_closed"] = True
        result["elapsed_ms"] = max(0, round((time.monotonic() - started) * 1000))
    return result
