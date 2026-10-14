"""Bounded encrypted record stream for native control, never a media reader."""
import asyncio
import time

from . import alarm_protocol as wire

WRITE_TIMEOUT = 2.0
MAX_WIRE_BYTES = 2 * 1024 * 1024
MAX_RECORDS = 1024


class ControlStream:
    """SDK WriteData records can split or coalesce native command packets.

    All encrypted records are checked before their payload joins the private
    receive buffer. Only the caller may interpret a complete native packet.
    """

    def __init__(self, reader, writer, material, observation):
        self.reader, self.writer = reader, writer
        self._material = material
        self.observation = observation
        self._buffer = bytearray()
        self._received = self._records = 0
        self._write_lock = asyncio.Lock()

    async def _record(self):
        if self._records >= MAX_RECORDS or self._received >= MAX_WIRE_BYTES:
            raise wire.AlarmProtocolError('control_receive_limit')
        header_bytes = await self.reader.readexactly(wire.HEADER_SIZE)
        header = wire.decode_control_header(header_bytes, self._material)
        if self._received + wire.HEADER_SIZE + header.body_length > MAX_WIRE_BYTES:
            raise wire.AlarmProtocolError('control_receive_limit')
        body = await self.reader.readexactly(header.body_length)
        payload = wire.decode_control_body(header, body, self._material)
        self._records += 1
        self._received += wire.HEADER_SIZE + len(body)
        self.observation['control_records_received'] = self._records
        self.observation['control_bytes_received'] = self._received
        self.observation['native_checksum_verified'] = True
        if len(self._buffer) + len(payload) > MAX_WIRE_BYTES:
            raise wire.AlarmProtocolError('control_receive_limit')
        self._buffer.extend(payload)

    async def read_packet(self):
        while len(self._buffer) < wire.HEADER_SIZE:
            await self._record()
        header = wire.parse_native_packet_header(bytes(self._buffer[:wire.HEADER_SIZE]))
        length = wire.HEADER_SIZE + header.body_length
        while len(self._buffer) < length:
            await self._record()
        packet = bytes(self._buffer[:length])
        del self._buffer[:length]
        return packet

    async def write_encrypted(self, packet):
        """Already encoded by an explicitly selected native command builder."""
        if not isinstance(packet, bytes) or len(packet) < 48 or len(packet) > MAX_WIRE_BYTES:
            raise wire.AlarmProtocolError('control_write_size')
        async with asyncio.timeout(WRITE_TIMEOUT):
            async with self._write_lock:
                if self.writer.is_closing():
                    raise wire.AlarmProtocolError('control_closed')
                self.writer.write(packet)
                await self.writer.drain()

    async def write_native(self, packet, *, allowed_commands):
        # Runtime diagnostics have no generic transparent/output write path.
        header = wire.parse_native_packet_header(packet[:wire.HEADER_SIZE])
        if header.command not in allowed_commands or len(packet) != wire.HEADER_SIZE + header.body_length:
            raise wire.AlarmProtocolError('control_write_not_allowed')
        await self.write_encrypted(wire.encode_control_packet(packet, self._material,
                                    timestamp_seconds=int(time.time())))

    def clear(self):
        self._buffer.clear()
        self._material = None


class _NativeControlStream:
    """Shared bounded V2 framing behind separately guarded transport paths."""

    def __init__(self, reader, writer, observation):
        self.reader, self.writer = reader, writer
        self.observation = observation
        self._received = self._records = 0
        self._write_lock = asyncio.Lock()
        self._cleared = False

    async def _read(self, length):
        if self._cleared:
            raise wire.AlarmProtocolError('control_closed')
        if self._received + length > MAX_WIRE_BYTES:
            raise wire.AlarmProtocolError('control_receive_limit')
        try:
            data = await self.reader.readexactly(length)
        except asyncio.IncompleteReadError as error:
            self._received += len(error.partial)
            self.observation['control_bytes_received'] = self._received
            raise
        self._received += len(data)
        self.observation['control_bytes_received'] = self._received
        return data

    async def read_packet(self):
        if self._records >= MAX_RECORDS:
            raise wire.AlarmProtocolError('control_receive_limit')
        raw_header = await self._read(wire.HEADER_SIZE)
        header = wire.parse_native_packet_header(raw_header)
        body = await self._read(header.body_length)
        self._records += 1
        self.observation['control_records_received'] = self._records
        # TLS integrity is distinct from a nonexistent native SHA checksum.
        self.observation['native_checksum_verified'] = False
        return raw_header + body

    async def _write(self, packet):
        if self._cleared or self.writer.is_closing():
            raise wire.AlarmProtocolError('control_closed')
        async with asyncio.timeout(WRITE_TIMEOUT):
            async with self._write_lock:
                if self._cleared or self.writer.is_closing():
                    raise wire.AlarmProtocolError('control_closed')
                self.writer.write(packet)
                await self.writer.drain()

    @staticmethod
    def _validate(packet):
        if not isinstance(packet, bytes) or len(packet) < wire.HEADER_SIZE:
            raise wire.AlarmProtocolError('control_write_size')
        header = wire.parse_native_packet_header(packet[:wire.HEADER_SIZE])
        if len(packet) != wire.HEADER_SIZE + header.body_length:
            raise wire.AlarmProtocolError('control_write_size')
        return header

    async def write_login(self, packet):
        header = self._validate(packet)
        if header.command not in (0x0A, 0xA0):
            raise wire.AlarmProtocolError('control_write_not_allowed')
        if not 0 < header.body_length <= 514:
            raise wire.AlarmProtocolError('control_write_size')
        await self._write(packet)

    async def write_native(self, packet, *, allowed_commands):
        header = self._validate(packet)
        # This diagnostic cannot write a transparent, media or relay command.
        if (header.command not in (0x68, 0xA1)
                or header.command not in allowed_commands or header.body_length):
            raise wire.AlarmProtocolError('control_write_not_allowed')
        await self._write(packet)

    def clear(self):
        self._cleared = True


class TLSControlStream(_NativeControlStream):
    """V2 packets on a freshly verified TLS socket, with no TCP fallback."""

    def __init__(self, reader, writer, observation, *, tls_verified=False):
        if tls_verified is not True:
            raise wire.AlarmProtocolError('control_tls_required')
        super().__init__(reader, writer, observation)


class ExplicitTCPControlStream(_NativeControlStream):
    """Unencrypted V2 diagnostic after two separate private device approvals."""

    def __init__(self, reader, writer, observation, *, tcp_approved=False,
                 legacy_login_approved=False):
        if tcp_approved is not True or legacy_login_approved is not True:
            raise wire.AlarmProtocolError('control_legacy_tcp_approval_required')
        super().__init__(reader, writer, observation)
