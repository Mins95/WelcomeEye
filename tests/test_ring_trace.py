"""Passive instrumentation privacy, limits and unchanged socket reads."""
import ast
from collections import deque
import importlib.util
from pathlib import Path
import struct
import time
import unittest

COMP = Path(__file__).resolve().parents[1] / 'custom_components/welcomeeye_local'
spec = importlib.util.spec_from_file_location('ring_trace_under_test', COMP / 'ring_trace.py')
trace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace)


class TraceTests(unittest.TestCase):
    def test_disabled_by_default(self):
        observer = trace.RingTrace()
        observer.record('ring')
        self.assertEqual(observer.snapshot()['events'], [])
        self.assertFalse(trace.PASSIVE_TRACE_ENABLED)

    def test_relative_clock_order_and_privacy(self):
        now = [800.0]
        observer = trace.RingTrace(enabled=True, clock=lambda: now[0])
        observer.record('session_open', payload=b'secret', host='private')
        now[0] += 20
        observer.record('ring')
        now[0] += 0.25
        observer.record('inner_tlv', kind=14854, length=158, count='secret')
        rows = observer.snapshot()['events']
        self.assertEqual([r['order'] for r in rows], [1, 2, 3])
        self.assertEqual(rows[-1]['t_ms'], 250)
        self.assertEqual(rows[-1]['observer_ms'], 20250)
        self.assertEqual(rows[-1]['ring_sequence'], 1)
        self.assertEqual(rows[-1]['session'], 1)
        self.assertNotIn('secret', str(rows))
        self.assertNotIn('host', str(rows))
        rows[0]['event'] = 'tampered'
        self.assertEqual(observer.snapshot()['events'][0]['event'], 'session_open')

    def test_bounded_count_and_duration(self):
        now = [0]
        for observer in (trace.RingTrace(enabled=True, max_events=2),
                         trace.RingTrace(enabled=True, clock=lambda: now[0], duration=1)):
            observer.record('ring')
            now[0] = 2
            for _ in range(10):
                observer.record('owsp_zero', length=0)
            self.assertFalse(observer.enabled)
            self.assertLessEqual(len(observer.snapshot()['events']), 2)

    def test_sink_failure_is_contained(self):
        def broken(row):
            raise OSError('private data')
        observer = trace.RingTrace(enabled=True, sink=broken)
        observer.record('ring')
        self.assertEqual(observer.snapshot()['sink_errors'], 1)
        self.assertNotIn('private', str(observer.snapshot()))

    def test_single_reader_zero_words_and_frame_order(self):
        # Exercise actual Session.read with a pre-existing exact reader.
        tree = ast.parse((COMP / 'client.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Session')
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef)
                   and n.name in ('read', '_observe_control')]
        scope = dict(time=time, struct=struct, ProtocolError=ValueError,
                     V1IdleTimeout=TimeoutError, parse_tlvs=lambda data: [(57, data)])
        exec(compile(ast.Module(methods, type_ignores=[]), '<Session>', 'exec'), scope)
        observer = trace.RingTrace(enabled=True)
        calls = []
        data = deque([bytes(4), struct.pack('>I', 8), bytes(8)])
        class Session:
            v1_video_receive = False
            media_observer = None
            control_observer = observer
            _login_deadline = None
            last_keepalive = time.monotonic()
            zero_frame_count = read_count = 0
            read = scope['read']
            _observe_control = scope['_observe_control']
            def _exact(self, size):
                calls.append(size)
                return data.popleft()
        self.assertEqual(Session().read(), [(57, bytes(4))])
        self.assertEqual(calls, [4, 4, 8])
        self.assertEqual([r['event'] for r in observer.snapshot()['events']],
                         ['owsp_zero', 'owsp_header', 'owsp_complete'])


if __name__ == '__main__':
    unittest.main()
