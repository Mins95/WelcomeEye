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
