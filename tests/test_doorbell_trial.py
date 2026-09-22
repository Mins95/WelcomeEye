"""Offline, targeted tests. Run: python test_doorbell_trial.py /path/to/checkout

Loads actual source ASTs; HA, sockets and the private decryption/TLV boundary
are simulated. This is not the project's full regression suite or hardware
validation. No network connection or physical output is used.
"""
import ast
import asyncio
from collections import deque
from dataclasses import dataclass
import heapq
import json
import logging
from pathlib import Path
import struct
import sys
import threading
import time
import types
import unittest
from unittest.mock import AsyncMock, Mock

ROOT = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else Path.cwd()
COMP = ROOT / 'custom_components' / 'welcomeeye_local'

class ProtocolError(Exception):
    pass

class AuthenticationError(Exception):
    pass

class Entity:
    def __init__(self, hub, key):
        self.hub = hub

class BinarySensorEntity:
    pass

def parse_fixture_tlvs(data):
    """Simulated framing boundary; not a test of the production TLV parser."""
    result = []
    while data:
        if len(data) < 4:
            raise ProtocolError('fixture length')
        kind, size = struct.unpack('<HH', data[:4])
        if len(data) < size + 4:
            raise ProtocolError('fixture length')
        result.append((kind, data[4:size+4]))
        data = data[size+4:]
    return result

def load_source(name, path, namespace):
    tree = ast.parse(path.read_text())
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    mod.__dict__.update(namespace)
    exec(compile(tree, str(path), 'exec'), mod.__dict__)
    return mod

common = dict(asyncio=asyncio, deque=deque, dataclass=dataclass, json=json,
              logging=logging, struct=struct, threading=threading, time=time,
              AuthenticationError=AuthenticationError, ProtocolError=ProtocolError)
const = load_source('trial_const', COMP/'const.py', {})
ring = load_source('trial_ring', COMP/'ring.py', dict(common,
    decode_private_reply=lambda uid, body: (0, body), parse_tlvs=parse_fixture_tlvs))
hub_module = load_source('trial_hub', COMP/'hub.py', dict(common,
    DOMAIN=const.DOMAIN, RING_HOLD_SECONDS=const.RING_HOLD_SECONDS))
sensors = load_source('trial_sensors', COMP/'binary_sensor.py', dict(
    WelcomeEyeEntity=Entity, BinarySensorEntity=BinarySensorEntity,
    BinarySensorDeviceClass=types.SimpleNamespace(CONNECTIVITY='connectivity'),
    EntityCategory=types.SimpleNamespace(DIAGNOSTIC='diagnostic'),
    RING_HOLD_SECONDS=const.RING_HOLD_SECONDS))

class Handle:
    def __init__(self, callback, args):
        self.callback, self.args, self.cancelled = callback, args, False
    def cancel(self):
        self.cancelled = True

class Clock:
    def __init__(self):
        self.now, self.seq, self.queue = 0.0, 0, []
    def call_later(self, delay, callback, *args):
        handle = Handle(callback, args)
        self.seq += 1
        heapq.heappush(self.queue, (self.now + delay, self.seq, handle))
        return handle
    def call_soon_threadsafe(self, callback, *args):
        callback(*args)
    def advance(self, delta):
        end = self.now + delta
        while self.queue and self.queue[0][0] <= end:
            self.now, _, h = heapq.heappop(self.queue)
            if not h.cancelled:
                h.callback(*h.args)
        self.now = end

V1 = 'WelcomeEye Connect V1'
V2 = 'WelcomeEye Connect 2'

def make_hub(model):
    hub = hub_module.WelcomeEyeHub.__new__(hub_module.WelcomeEyeHub)
    hub.device_model = model
    hub.stopped = False
    hub.ringing = False
    hub.ring_connected = True
    hub.ring_timer = None
    hub.ring_error = None
    hub.ring_count = 0
    hub.loop = Clock()
    hub.entry = types.SimpleNamespace(entry_id='fixture-entry', unique_id='fixture-device',
        data={'host': 'fixture-host', 'username': 'fixture-user', 'password': 'fixture-password'})
    hub.hass = types.SimpleNamespace(bus=types.SimpleNamespace(async_fire=Mock()))
    hub.listeners = set()
    hub.ring_listener = ring.RingListener(hub.loop, hub.entry, hub._ring, hub._ring_state)
    return hub

def alarm(stamp='20260101000000', alarm_type=7, **overrides):
    param = dict(channel=1, timestamp=stamp, timestamp_svr=1, alarm_type=alarm_type)
    param.update(overrides)
    js = json.dumps(dict(name='reportAlarm', mode='set', param=param)).encode()
    payload = struct.pack('<I', len(js)) + js
    inner = struct.pack('<HH', 14854, len(payload)) + payload
    return struct.pack('>I', len(inner)+4) + bytes(4) + inner

class PulseTests(unittest.TestCase):
    def test_five_seconds_both_models(self):
        for model in (V1,V2):
            with self.subTest(model=model):
                hub = make_hub(model)
                hub._ring(types.SimpleNamespace(channel=1))
                self.assertTrue(hub.ringing)
                hub.loop.advance(4.999)
                self.assertTrue(hub.ringing)
                hub.loop.advance(.002)
                self.assertFalse(hub.ringing)
                self.assertIsNone(hub.ring_timer)

    def test_distinct_press_extends_pulse_without_off_edge(self):
        hub = make_hub(V1)
        states=[]
        hub.listeners.add(lambda: states.append(hub.ringing))
        hub.ring_listener._record_alarm_parts('fixture-device', 510, alarm())
        hub.loop.advance(4)
        first_timer=hub.ring_timer
        hub.ring_listener._record_alarm_parts('fixture-device', 510, alarm('20260101000001'))
        self.assertTrue(first_timer.cancelled)
        hub.loop.advance(4.9)
        self.assertTrue(hub.ringing)
        self.assertEqual(states,[True,True])
        hub.loop.advance(.2)
        self.assertFalse(hub.ringing)
        self.assertEqual(hub.hass.bus.async_fire.call_count,2)

    def test_duplicate_does_not_extend_or_emit(self):
        hub=make_hub(V1)
        hub.ring_listener._record_alarm_parts('fixture-device',510,alarm())
        handle=hub.ring_timer
        hub.loop.advance(4)
        hub.ring_listener._record_alarm_parts('fixture-device',510,alarm())
        self.assertIs(hub.ring_timer,handle)
        hub.loop.advance(1.01)
        self.assertFalse(hub.ringing)
        self.assertEqual(hub.ring_count,1)
        hub.hass.bus.async_fire.assert_called_once_with('welcomeeye_local.ring',
            {'entry_id':'fixture-entry','channel':1})

    def test_keepalives_and_login_do_not_ring(self):
        hub=make_hub(V1)
        for kind in (40,57,70,502):
            hub.ring_listener._record_alarm_parts('fixture-device',kind,b'fixture')
        self.assertEqual(hub.ring_count,0)
        self.assertFalse(hub.ringing)
        self.assertIsNone(hub.ring_timer)

    def test_all_existing_alarm_types_use_same_path(self):
        for model in (V1,V2):
            for kind in (7,14,19,47):
                with self.subTest(model=model,kind=kind):
                    hub=make_hub(model)
                    hub.ring_listener._record_alarm_parts('fixture-device',510,alarm(alarm_type=kind))
                    self.assertTrue(hub.ringing)
                    self.assertEqual(hub.ring_count,1)

    def test_unknown_alarm_type_observed_but_not_ring(self):
        hub=make_hub(V1)
        hub.ring_listener._record_alarm_parts('fixture-device',510,alarm(alarm_type=123))
        self.assertEqual(hub.ring_listener.alarm_type_counts,{123:1})
        self.assertEqual(hub.ring_count,0)

    def test_malformed_private_envelope_counted_not_ring(self):
        hub=make_hub(V1)
        hub.ring_listener._record_alarm_parts('fixture-device',510,b'bad')
        self.assertEqual(hub.ring_listener.decode_failures,1)
        self.assertEqual(hub.ring_count,0)

    def test_malformed_alarm_identity_not_ring(self):
        hub=make_hub(V1)
        hub.ring_listener._record_alarm_parts('fixture-device',510,alarm(timestamp_svr=0))
        self.assertEqual(hub.ring_count,0)

    def test_sensor_stays_available_during_received_pulse_on_disconnect(self):
        for model in (V1,V2):
            with self.subTest(model=model):
                hub=make_hub(model)
                sensor=sensors.WelcomeEyeRing(hub)
                hub._ring(types.SimpleNamespace(channel=1))
                hub.loop.advance(1)
                hub._ring_state(False,'ConnectionError')
                self.assertTrue(sensor.available)
                self.assertTrue(sensor.is_on)
                hub.loop.advance(4.01)
                self.assertFalse(sensor.is_on)
                self.assertFalse(sensor.available)

    def test_idle_disconnection_is_not_hidden(self):
        hub=make_hub(V1)
        hub._ring_state(False,'TimeoutError')
        sensor=sensors.WelcomeEyeRing(hub)
        self.assertFalse(sensor.available)
        self.assertFalse(sensor.is_on)

    def test_stopped_hub_ignores_ring(self):
        hub=make_hub(V1)
        hub.stopped=True
        hub._ring(types.SimpleNamespace(channel=1))
        self.assertFalse(hub.ringing)
        self.assertFalse(sensors.WelcomeEyeRing(hub).available)
        hub.hass.bus.async_fire.assert_not_called()

    def test_safe_trial_attributes(self):
        for model,mode in ((V1,'experimental_connect2_path_on_v1'),(V2,'connect2_path')):
            with self.subTest(model=model):
                attrs=sensors.WelcomeEyeRing(make_hub(model)).extra_state_attributes
                self.assertEqual(attrs,{'ring_hold_seconds':5.0,'listener_mode':mode})

    def test_late_callback_ignored_after_listener_close(self):
        hub=make_hub(V1)
        hub.ring_listener.close()
        hub.ring_listener._accept(ring.RingMessage(1,'20260101000000',1))
        self.assertEqual(hub.ring_count,0)

    def test_close_listener_does_not_close_media_session(self):
        hub=make_hub(V1)
        hub.session=Mock()
        hub.ring_listener.session=Mock()
        ring_session=hub.ring_listener.session
        hub.ring_listener.close()
        ring_session.close.assert_called_once()
        hub.session.close.assert_not_called()

    def test_worker_uses_exact_connect2_profile_without_output_or_startav(self):
        for model in (V1,V2):
            with self.subTest(model=model):
                hub=make_hub(model)
                listener=hub.ring_listener
                instances=[]
                class Socket:
                    pass
                class Session:
                    def __init__(self,host,user,password,**kwargs):
                        self.params=kwargs
                        self.info=types.SimpleNamespace(uid=hub.entry.unique_id)
                        self.sock=Socket()
                        self.last_keepalive=time.monotonic()-11
                        self.keepalive_count=self.zero_frame_count=0
                        self.is_closed=False
                        instances.append(self)
                    def connect(self):
                        return [(502,b'fixture'),(57,b'fixture'),(510,alarm())]
                    def send_keepalive(self):
                        self.keepalive_count+=1
                        self.last_keepalive=time.monotonic()
                    def read(self):
                        listener.closed.set()
                        return []
                    def close(self):
                        self.is_closed=True
                    def framing_diagnostics(self):
                        return {'login_frames_seen':1}
                old_session=ring.__dict__.get('Session')
                old_select=ring.__dict__.get('select')
                ring.Session=Session
                ring.select=types.SimpleNamespace(select=lambda r,w,x,t:(r,[],[]))
                try:
                    listener._worker()
                finally:
                    ring.Session=old_session
                    ring.select=old_select
                self.assertEqual(len(instances),1)
                self.assertEqual(instances[0].params,dict(channel=0,stream=3,mode=0))
                self.assertTrue(instances[0].is_closed)
                self.assertEqual(instances[0].keepalive_count,1)
                self.assertEqual(hub.ring_count,1)
                self.assertIsNone(listener.session)

class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_cancels_pulse_and_clears_state(self):
        hub=make_hub(V1)
        hub.control=types.SimpleNamespace(close=Mock())
        hub.close_listeners=set()
        hub.server=None
        hub.handlers=set()
        hub.lock=asyncio.Lock()
        hub.consumers=set()
        # The real constructor supplies these media/snapshot shutdown events.
        hub.ready=asyncio.Event()
        hub.image_event=asyncio.Event()
        hub._halt_media=AsyncMock()
        hub._ring(types.SimpleNamespace(channel=1))
        handle=hub.ring_timer
        await hub._stop()
        self.assertTrue(handle.cancelled)
        self.assertFalse(hub.ringing)
        self.assertIsNone(hub.ring_timer)
        hub.loop.advance(10)
        self.assertFalse(hub.ringing)

    async def test_setup_retains_original_listener_on_both_models(self):
        fn=next(n for n in ast.parse((COMP/'__init__.py').read_text()).body
                if isinstance(n,ast.AsyncFunctionDef) and n.name=='async_setup_entry')
        # Annotations are not part of the behavior under test.
        fn.returns=None
        for arg in fn.args.args:
            arg.annotation=None
        for model in (V1,V2):
            with self.subTest(model=model):
                real_hub=make_hub(model)
                listener=real_hub.ring_listener
                real_hub.start=AsyncMock()
                real_hub.stop=AsyncMock()
                hass=types.SimpleNamespace(async_add_executor_job=AsyncMock(),
                    config_entries=types.SimpleNamespace(async_forward_entry_setups=AsyncMock()),
                    bus=types.SimpleNamespace(async_listen_once=Mock(return_value=lambda:None)))
                entry=types.SimpleNamespace(async_on_unload=Mock())
                scope=dict(WelcomeEyeHub=lambda h,e:real_hub, _preload_dns_types=lambda:None,
                    AuthenticationError=AuthenticationError, ConfigEntryAuthFailed=RuntimeError,
                    ConfigEntryNotReady=RuntimeError, PLATFORMS=[], EVENT_HOMEASSISTANT_STOP='stop')
                exec(compile(ast.fix_missing_locations(ast.Module([fn],type_ignores=[])),
                             '<actual setup source>','exec'),scope)
                self.assertTrue(await scope['async_setup_entry'](hass,entry))
                self.assertIs(real_hub.ring_listener,listener)
                self.assertFalse(real_hub.v1_doorbell_standby)
                real_hub.start.assert_awaited_once()
                self.assertFalse(listener.closed.is_set())

if __name__=='__main__':
    unittest.main(verbosity=2)
