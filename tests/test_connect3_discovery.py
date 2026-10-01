"""SYNTHETIC records; independent native-math vectors, no hardware capture."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from load_integration import load
from test_r002_qv_discovery import FakeNetwork, qv

module = load('connect3.discovery')
kdf = load('connect3.kdf')


def synthetic_record():
    record = bytearray(528)
    record[0x64:0x68] = bytes([192, 0, 2, 1])
    record[0xc8:0xc8 + 21] = b'SYNTHETIC_PRIVATE_UID'
    record[0x188:0x188 + 8] = b'FIXTURE3'
    record[0x1bc:0x1bc + 9] = b'SYNTHETIC'
    for offset, value in ((0x78, 34567), (0x1a8, 443), (0x1cc, 34568), (0x1a4, 1)):
        struct.pack_into('<H', record, offset, value)
    return record


def synthetic_packet(record=None, seed=b'A', prefix=module.PREFIXES[0]):
    record = bytes(synthetic_record() if record is None else record)
    encryptor = Cipher(algorithms.AES(kdf.derive_key(seed)), modes.CBC(b'0' * 16)).encryptor()
    ciphertext = encryptor.update(record) + encryptor.finalize()
    header = bytearray(40)
    struct.pack_into('<II', header, 8, len(seed), len(ciphertext))
    return prefix + header + seed + ciphertext


class ParserTests(unittest.TestCase):
    def test_native_math_vectors_and_thread_safety(self):
        vectors = json.loads((Path(__file__).parent / 'fixtures/connect3_kdf_native.json').read_text())
        with ThreadPoolExecutor(max_workers=4) as executor:
            actual = list(executor.map(kdf.derive_key, [bytes.fromhex(v['seed_hex']) for v in vectors] * 20))
        self.assertEqual([value.hex() for value in actual], [v['key_hex'] for v in vectors] * 20)

    def test_both_verified_prefixes_and_ip_byte_order(self):
        for prefix in module.PREFIXES:
            record = module.decode_datagram(synthetic_packet(prefix=prefix))
            self.assertEqual(record.address, '192.0.2.1')
            self.assertEqual(record.uid, 'SYNTHETIC_PRIVATE_UID')
            self.assertEqual(record.stream_port, 34567)
            self.assertEqual(record.cgi_port, 443)
            self.assertEqual(record.tls_media_port, 34568)
            self.assertNotIn(record.uid, repr(record))

    def test_every_truncation_rejected(self):
        packet = synthetic_packet()
        for length in range(len(packet)):
            with self.subTest(length=length), self.assertRaises(module.DiscoveryDecodeError):
                module.decode_datagram(packet[:length])

    def test_unknown_versions_extra_data_size_and_length(self):
        packet = synthetic_packet()
        mutations = [b'ASZENO.SEARCH.V5' + packet[18:], packet + b'x', b'x' * 2049]
        for seed_size, size in ((1025, 528), (1, 512), (1, 544), (1, 529), (1, 0xffffffff)):
            changed = bytearray(packet)
            struct.pack_into('<II', changed, 18 + 8, seed_size, size)
            mutations.append(bytes(changed))
        for changed in mutations:
            with self.assertRaises(module.DiscoveryDecodeError):
                module.decode_datagram(changed)
        with self.assertRaises(ValueError):
            kdf.derive_key(bytes(1025))

    def test_unterminated_invalid_encoding_address_and_corruption(self):
        for start, value in ((0xc8, b'x' * 64), (0x188, b'\xff\0'), (0x64, bytes(4)), (0xc8, b'\n')):
            record = synthetic_record()
            record[start:start + len(value)] = value
            with self.assertRaises(module.DiscoveryDecodeError):
                module.decode_datagram(synthetic_packet(record))
        changed = bytearray(synthetic_packet())
        changed[18 + 40] = 99
        with self.assertRaises(module.DiscoveryDecodeError):
            module.decode_datagram(bytes(changed))


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_exchange_dedup_privacy_and_cleanup(self):
        packet = synthetic_packet()
        for detailed in (False, True):
            network = FakeNetwork([(packet, ('192.0.2.1', 5000), 5001),
                                   (packet, ('192.0.2.1', 5000), 5003)])
            with patch.object(qv, '_open_listener', side_effect=network.open), patch.object(qv, 'TIMEOUT', .01):
                result = await module.discover('192.0.2.1', include_details=detailed)
            self.assertEqual(result['decoded_records'], 1)
            self.assertEqual(result['duplicate_records'], 1)
            self.assertFalse(result['device_authenticated'])
            self.assertFalse(result['model_confirmed'])
            self.assertEqual(len(network.sent), 1)
            network.check_closed(self)
            for secret in (packet.hex(), 'SYNTHETIC_PRIVATE_UID', '192.0.2.1', 'response_hex'):
                self.assertNotIn(secret, json.dumps(result))
            self.assertEqual('records' in result, detailed)

    async def test_bad_record_and_other_source_not_accepted(self):
        record = synthetic_record()
        record[0x64:0x68] = bytes([192, 0, 2, 9])
        events = [(synthetic_packet(record), ('192.0.2.1', 5000), 5003),
                  (synthetic_packet(), ('192.0.2.2', 5000), 5003),
                  (b'bad', ('192.0.2.1', 5000), 5001)]
        network = FakeNetwork(events)
        with patch.object(qv, '_open_listener', side_effect=network.open), patch.object(qv, 'TIMEOUT', .01):
            result = await module.discover('192.0.2.1')
        self.assertEqual(result['decoded_records'], 0)
        self.assertEqual(result['decode_errors'], {'address_mismatch': 1, 'unsupported_prefix': 1})
        network.check_closed(self)

    async def test_cancel_closes_both_ports(self):
        network = FakeNetwork()
        with patch.object(qv, '_open_listener', side_effect=network.open):
            task = asyncio.create_task(module.discover('192.0.2.1'))
            await network.sending.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        network.check_closed(self)
