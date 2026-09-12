"""Bounded, local-only WelcomeEye TCP session."""
import socket
import struct
import time
import threading

from .protected import (ProtocolError, build_protected_login, decode_discovery,
                        decode_login_reply, owsp, parse_tlvs, tlv)
from .protocol import encode_password


class AuthenticationError(Exception):
    """The device refused the supplied credentials."""


def discover(host):
    # Requiring an IPv4 literal avoids DNS surprises and broadcast scans.
    socket.inet_pton(socket.AF_INET, host)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.settimeout(3)
        for _ in range(2):
            udp.sendto(bytes.fromhex('07a02000') + bytes(32), (host, 1500))
            until = time.monotonic() + 3
            while time.monotonic() < until:
                udp.settimeout(max(0.01, until - time.monotonic()))
                try:
                    packet, peer = udp.recvfrom(4096)
                except TimeoutError:
                    break
                if peer[0] == host:
                    info = decode_discovery(packet)
                    if info.address != host:
                        raise ProtocolError('Discovery address mismatch')
                    if not info.protected:
                        raise ProtocolError('This integration requires a protected WelcomeEye protocol')
                    return info
    raise TimeoutError('No device discovery response')


class Session:
    def __init__(self, host, username, password, channel=16, *, stream=1, mode=2):
        self.host, self.username, self.password = host, username, password
        self.channel = channel
        self.stream, self.mode = stream, mode
        self.closed = threading.Event()
        self.sock = None
        self.info = None
        self.last_keepalive = 0
        self.device_time = 0
        self.clock_received = 0
        self.encryption_profile = None

    def connect(self):
        self.info = discover(self.host)
        if self.closed.is_set():
            raise ConnectionAbortedError('Session cancelled')
        self.sock = socket.create_connection((self.host, self.info.tcp_port), timeout=5)
        try:
            if self.closed.is_set():
                raise ConnectionAbortedError('Session cancelled')
            self.sock.settimeout(10)
            self.sock.sendall(build_protected_login(self.info.uid, self.username,
                encode_password(self.password), channel=self.channel,
                stream=self.stream, mode=self.mode))
            self.last_keepalive = time.monotonic()
            parts = self.read()
            replies = [body for kind, body in parts if kind == 502]
            if len(replies) != 1:
                raise ProtocolError('Missing login response')
            status, _, metadata, device_time = decode_login_reply(self.info.uid, replies[0], include_time=True)
            if status == 2:
                raise AuthenticationError('Device refused authentication')
            if status != 1:
                raise ProtocolError(f'Device login status {status}')
            self.device_time = device_time
            self.clock_received = time.monotonic()
            self.encryption_profile = metadata.get('AppId')
            self.last_keepalive = time.monotonic()
            return parts
        except BaseException:
            self.close()
            raise

    def device_now(self):
        if self.device_time <= 0:
            raise ProtocolError('Device clock is unavailable')
        return self.device_time + int(time.monotonic() - self.clock_received)

    def _exact(self, size):
        data = bytearray()
        while len(data) < size:
            part = self.sock.recv(size - len(data))
            if not part:
                raise ConnectionError('Device closed the connection')
            data.extend(part)
        return bytes(data)

    def read(self):
        if time.monotonic() - self.last_keepalive >= 10:
            # Native DataChannel::sendAliveReq: TLV 49, channel byte + 3 zeroes.
            self.sock.sendall(owsp(tlv(49, bytes((self.channel, 0, 0, 0)))))
            self.last_keepalive = time.monotonic()
        size = struct.unpack('>I', self._exact(4))[0]
        if not 4 <= size <= 1048576:
            raise ProtocolError('Frame length outside bounds')
        return parse_tlvs(self._exact(size)[4:])

    def close(self):
        self.closed.set()
        sock, self.sock = self.sock, None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


def validate_connection(host, username, password, channel=16):
    session = Session(host, username, password, channel)
    try:
        session.connect()
        return session.info.uid
    finally:
        session.close()
