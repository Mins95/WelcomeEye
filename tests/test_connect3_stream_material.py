"""Synthetic secret lifetime checks; no device response fixture is invented."""
import unittest
from unittest.mock import AsyncMock, patch

from load_integration import load

cgi = load('connect3.cgi')


class MaterialTests(unittest.IsolatedAsyncioTestCase):
    def test_values_retained_privately_not_in_repr_or_summary(self):
        data = (b'<envelope><body><error>0</error><content>'
                b'<key>SYNTHETIC_STREAM_SECRET</key><tdc>SYNTHETIC_TDC</tdc>'
                b'<synctime>SYNTHETIC_TIME</synctime></content></body></envelope>')
        material = cgi._stream_material(data)
        self.assertEqual(material.key, 'SYNTHETIC_STREAM_SECRET')
        self.assertFalse(hasattr(material, 'tdc'))
        self.assertNotIn('SYNTHETIC', repr(material))
        self.assertNotIn('SYNTHETIC', repr(cgi.streamkey_summary(data)))
        material.clear()
        self.assertEqual(material.key, '')

    async def test_internal_path_calls_same_access_once(self):
        private = cgi.StreamMaterial('SYNTHETIC_PRIVATE')
        async def read(*args, **kwargs):
            self.assertEqual(kwargs['operation'], 'access')
            kwargs['_key_receiver'](private)
            return {'streamkey_received': True}
        with patch.object(cgi, 'read_device', new=AsyncMock(side_effect=read)) as method:
            result = await cgi.read_stream_material('192.0.2.1', 'SYNTHETIC_AUTH')
        self.assertIs(result, private)
        method.assert_awaited_once()

    async def test_failure_drops_retained_references(self):
        private = cgi.StreamMaterial('SYNTHETIC_PRIVATE')
        async def read(*args, **kwargs):
            kwargs['_key_receiver'](private)
            raise TimeoutError
        with patch.object(cgi, 'read_device', side_effect=read):
            with self.assertRaises(TimeoutError):
                await cgi.read_stream_material('192.0.2.1', 'SYNTHETIC_AUTH')
        self.assertEqual(private.key, '')

    def test_missing_duplicate_and_unbounded_key_fail(self):
        for content in ('<key/>', '<key>a</key><key>b</key>', '<key>'+'a'*1025+'</key>'):
            with self.assertRaises(cgi.CGIError):
                cgi._stream_material(('<envelope><body><error>0</error><content>' +
                                     content + '</content></body></envelope>').encode())


if __name__ == '__main__':
    unittest.main()
