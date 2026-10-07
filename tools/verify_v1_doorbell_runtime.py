"""Real HA V1 manual service dispatch and lifecycle; no device I/O.

Run in the supported HA Core images. The real trial owns its background worker
and locks; only its device-session boundary is replaced with a synthetic peer.
The requested 300-second observation is explicitly stopped, never waited out.
"""
import asyncio
import json
from pathlib import Path
import queue
import struct
import sys
import tempfile
import threading
import time
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import Context, HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import DATA_DOMAIN_PLATFORM_ENTITIES

# Import after HA selects its supported schema implementation.
import voluptuous as vol


class SyntheticSession:
    """Never opens a socket; exposes only the production session boundary."""
    instances = []

    def __init__(self, host, username, password, channel, *, stream, mode,
                 on_parts, cancel_event):
        assert (host, username, password) == ('192.0.2.1', 'PRIVATE_USER', 'PRIVATE_PASSWORD')
        self.profile = (channel, stream, mode)
        self.on_parts, self.cancelled = on_parts, cancel_event
        self.authenticated = False
        self.sock = None
        self.info = SimpleNamespace(uid='PRIVATE_SYNTHETIC_UID')
        self.connection_stage = 'tcp_connecting'
        self.closed = threading.Event()
        self.network_deadline = time.monotonic() + 12
        self.received_bytes = self.read_count = self.zero_frame_count = self.keepalive_count = 0
        self.partial_frame_at_end = False
        self.sent_counts = {}
        self.duration = None
        self.cleanup_started = False
        self.pending = queue.Queue()
        self.instances.append(self)

    def connect(self):
        self.sock = object()
        self.sent_counts.update({40: 1, 501: 1})
        self.on_parts([(502, b'PRIVATE_LOGIN_REPLY')], False)
        self.authenticated = True
        self.connection_stage = 'authenticated'

    def begin_observation(self, duration):
        self.duration = duration
        self.network_deadline = time.monotonic() + duration

    def read(self):
        if not self.read_count:
            self.read_count += 1
            body = b'PRIVATE_NON_ALARM_PAYLOAD'
            self.received_bytes += len(body)
            self.on_parts([(57, body)], True)
        deadline = time.monotonic() + 15
        while not self.cancelled.is_set():
            if time.monotonic() >= deadline:
                raise AssertionError('Synthetic observation was not explicitly stopped')
            try:
                parts, received = self.pending.get(timeout=.05)
            except queue.Empty:
                continue
            self.read_count += 1
            self.received_bytes += sum(len(body) for _, body in parts)
            self.on_parts(parts, True)
            received.set()
            return parts
        raise ConnectionAbortedError('PRIVATE_CANCEL_MESSAGE')

    def cancel(self):
        self.cancelled.set()

    def begin_cleanup(self):
        self.cleanup_started = True

    def send_session_stop(self):
        assert self.cleanup_started and self.sent_counts.get(5005, 0) == 0
        self.sent_counts[5005] = 1

    def close(self):
        self.sock = None
        self.closed.set()


def identity(*, admin=True, control=True):
    return SimpleNamespace(id='synthetic-admin', is_admin=admin,
        permissions=SimpleNamespace(check_entity=Mock(return_value=control)))


def invalid_json_reply(protected):
    """Synthetic encrypted 510 containing a valid 14854 envelope and bad JSON."""
    malformed = b'{"PRIVATE_JSON_VALUE":'
    payload = struct.pack('<I', len(malformed)) + malformed
    inner = protected.owsp(protected.tlv(14854, payload))
    first, last = b'A' * 16, b'B' * 16
    key = first[4:13] + last[5:10] + bytes(2)
    iv = last[3:9] + bytes(10)
    encrypted = protected.aes_cfb(key, iv, struct.pack('<Q', 1) + inner)
    return protected.rc4(b'PRIVATE_SYNTHETIC_UID', struct.pack('<I', 1) + first + encrypted + last)


async def main(root):
    sys.path.insert(0, str(root / 'tests'))
    from load_integration import load
    services, backend = load('services'), load('v1_doorbell_trial')
    hubs, diagnostics, cap = load('hub'), load('diagnostics'), load('capabilities')
    protected = load('protected')

    class SyntheticCamera(Camera):
        def __init__(self, hub):
            super().__init__()
            self.hub = hub

    with tempfile.TemporaryDirectory() as temporary:
        hass = HomeAssistant(temporary)
        hass.config_entries = ConfigEntries(hass, {})
        entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
            title='Synthetic V1 manual trial', unique_id='PRIVATE_SYNTHETIC_UID',
            data={'host': '192.0.2.1', 'username': 'PRIVATE_USER', 'password': 'PRIVATE_PASSWORD',
                  'device_variant': cap.DeviceVariant.V1.value,
                  'protocol_family': cap.ProtocolFamily.LEGACY.value,
                  'detected_model': 'WelcomeEye Connect V1'},
            options={}, source='user', subentries_data=None, discovery_keys=MappingProxyType({}))
        hass.config_entries._entries[entry.entry_id] = entry
        hub = hubs.WelcomeEyeHub(hass, entry)
        entry.runtime_data = hub
        camera = SyntheticCamera(hub)
        camera.hass, camera.entity_id = hass, 'camera.synthetic_v1_trial'
        incompatible = []
        for variant, family in ((cap.DeviceVariant.R001, cap.ProtocolFamily.LEGACY),
                (cap.DeviceVariant.LEGACY_UNKNOWN, cap.ProtocolFamily.LEGACY),
                (cap.DeviceVariant.R002, cap.ProtocolFamily.R002),
                (cap.DeviceVariant.CONNECT3, cap.ProtocolFamily.CONNECT3)):
            target = SyntheticCamera(SimpleNamespace(variant=variant, protocol_family=family))
            target.hass, target.entity_id = hass, f'camera.synthetic_{variant.value}'
            incompatible.append(target)
        hass.data[DATA_DOMAIN_PLATFORM_ENTITIES] = {('camera', 'welcomeeye_local'):
            {target.entity_id: target for target in [camera, *incompatible]}}
        services.async_setup_services(hass)
        admin = identity()
        hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=admin))
        before = json.dumps([dict(entry.data), dict(entry.options)])
        before_attributes = json.dumps(camera.extra_state_attributes)
        original_capabilities = hub.capabilities

        async def action(operation, fields=None, *, entity_id=None, context=None):
            return await hass.services.async_call('welcomeeye_local', 'v1_observe_doorbell',
                {'entity_id': entity_id or camera.entity_id, 'operation': operation, **(fields or {})},
                blocking=True, return_response=True,
                context=context if context is not None else Context(user_id=admin.id))

        async def diagnostic():
            with patch.object(diagnostics, 'get_crc32c_diagnostics', return_value={'status': 'synthetic'}):
                return await diagnostics.async_get_config_entry_diagnostics(hass, entry)

        def private_free(value):
            encoded = json.dumps(value)
            for private in ('192.0.2.1', 'PRIVATE_', 'response_hex', 'raw_payload'):
                assert private not in encoded, private

        async def inject(session, parts):
            received = threading.Event()
            session.pending.put((parts, received))
            assert await asyncio.to_thread(received.wait, 2), 'Synthetic reader did not consume its fixture'

        SyntheticSession.instances = []
        try:
            assert hass.services.supports_response('welcomeeye_local', 'v1_observe_doorbell') is SupportsResponse.ONLY
            with patch.object(backend, '_TrialSession', side_effect=SyntheticSession) as factory, \
                    patch.object(hubs, 'Session', side_effect=AssertionError('Media device session forbidden')) as media:
                await hub.start()  # Only the existing loopback HTTP server may start.
                assert not hasattr(hub, '_v1_doorbell_trial')
                assert hub.session is None and hub.thread is None and not hub.consumers
                initial = await diagnostic()
                assert initial['v1_doorbell_trial'] == {'status': 'not_started'}
                private_free(initial)
                factory.assert_not_called()
                for operation in ('start', 'mark', 'status', 'stop'):
                    for who, context in ((None, Context()), (None, Context(user_id='deleted')),
                            (identity(admin=False), Context(user_id='nonadmin')),
                            (identity(control=False), Context(user_id='admin-no-control'))):
                        hass.auth.async_get_user.return_value = who
                        try:
                            await action(operation, {'confirm': True}, context=context)
                        except (HomeAssistantError, PermissionError):
                            pass
                        else:
                            raise AssertionError('V1 action bypassed identified-admin/control checks')
                        assert not hasattr(hub, '_v1_doorbell_trial')
                hass.auth.async_get_user.return_value = admin
                for target in incompatible:
                    try:
                        await action('start', {'confirm': True}, entity_id=target.entity_id)
                    except HomeAssistantError:
                        pass
                    else:
                        raise AssertionError('V1 action accepted an incompatible family/variant')
                    assert not hasattr(target.hub, '_v1_doorbell_trial')
                for fields in ({'duration': 29}, {'duration': 301}, {'duration': 'invalid'},
                               {'profile': 'video'}, {'operation': 'automatic'}):
                    try:
                        await action('start', {'confirm': True, **fields})
                    except (HomeAssistantError, ValueError, vol.Invalid):
                        pass
                    else:
                        raise AssertionError('V1 schema accepted an invalid duration/profile/operation')
                try:
                    await hass.services.async_call('welcomeeye_local', 'v1_observe_doorbell',
                        {'entity_id': camera.entity_id}, blocking=True, return_response=True,
                        context=Context(user_id=admin.id))
                except (HomeAssistantError, ValueError, vol.Invalid):
                    pass
                else:
                    raise AssertionError('V1 schema accepted a missing operation')
                try:
                    unknown = await action('start', {'confirm': True}, entity_id='camera.synthetic_missing')
                except HomeAssistantError:
                    pass
                else:
                    assert camera.entity_id not in unknown
                for duration in (30, 300):
                    result = await action('start', {'duration': duration})
                    assert result[camera.entity_id]['reason'] == 'explicit_confirmation_required'
                    downloaded = (await diagnostic())['v1_doorbell_trial']
                    assert downloaded['last_denial']['operation'] == 'start'
                    assert downloaded['last_denial']['reason'] == 'explicit_confirmation_required'
                    assert downloaded['last_denial']['elapsed_since_controller_created_ms'] >= 0
                    assert downloaded['denial_count'] >= 1
                    assert not downloaded['active']
                    private_free(downloaded)
                for operation in ('status', 'mark', 'stop'):
                    private_free(await action(operation))
                downloaded = (await diagnostic())['v1_doorbell_trial']
                assert downloaded['last_denial']['operation'] == 'mark'
                assert downloaded['last_denial']['reason'] == 'not_observing'
                previous_denials = downloaded['denial_count']
                factory.assert_not_called()
                assert not hub._v1_doorbell_trial.active
                # Actual schema supplies control/300/confirm defaults. The real
                # backend returns only after the synthetic login has completed.
                result = (await action('start', {'confirm': True}))[camera.entity_id]
                assert result['status'] == 'observing' and result['login_accepted']
                assert result['duration_seconds'] == 300 and result['keepalive_interval_seconds'] == 5
                assert hub._v1_doorbell_trial.active and hub.lock.locked() and hub.control.lock.locked()
                assert len(SyntheticSession.instances) == 1
                first = SyntheticSession.instances[0]
                assert first.profile == (0, 3, 0) and first.duration == 300
                # Flush the initial synthetic read before comparing reports.
                await inject(first, [])
                before_busy = (await diagnostic())['v1_doorbell_trial']
                assert before_busy['denial_count'] == previous_denials
                assert before_busy['last_denial'] == downloaded['last_denial']
                assert (await action('start', {'confirm': True}))[camera.entity_id]['reason'] == 'busy'
                after_busy = (await diagnostic())['v1_doorbell_trial']
                assert after_busy['denial_count'] == previous_denials + 1
                assert after_busy['last_denial']['operation'] == 'start'
                assert after_busy['last_denial']['reason'] == 'busy'
                for key in ('status', 'profile', 'duration_seconds', 'login_accepted',
                            'events', 'events_total', 'events_dropped', 'markers'):
                    assert after_busy[key] == before_busy[key], key
                assert after_busy['active'] and len(SyntheticSession.instances) == 1
                marked = (await action('mark'))[camera.entity_id]
                assert len(marked['markers']) == 1 and marked['markers'][0]['sequence'] == 1
                assert marked['alarm_candidates'] == 0 and hub.ring_count == 0 and not hub.ringing
                # Exercise overflow through the real worker callback, then a
                # late message and a real decoder failure with private JSON.
                await inject(first, [(57, b'PRIVATE_FILLER_PAYLOAD')] * 160)
                await inject(first, [(70, b'PRIVATE_LATE_PAYLOAD')])
                bad_reply = invalid_json_reply(protected)
                await inject(first, [(510, bad_reply)])
                observed = (await action('status'))[camera.entity_id]
                downloaded = (await diagnostic())['v1_doorbell_trial']
                for report in (observed, downloaded):
                    total = report['events_total']
                    assert total > 160 and len(report['events']) == 128
                    assert report['events_dropped'] == total - 128
                    sequences = [event['sequence'] for event in report['events']]
                    assert sequences[:32] == list(range(1, 33))
                    assert sequences[32:] == list(range(total - 95, total + 1))
                    assert any(event['kind'] == 70 for event in report['events'])
                    assert any(event.get('event') == 'inner_tlv' and event.get('inner_kind') == 14854
                               for event in report['events'])
                    assert report['decode_failures'] == 1
                    assert report['decode_error_counts'] == {'invalid_json': 1}
                    error = report['last_decode_error']
                    assert error['category'] == 'invalid_json' and error['error_type'] == 'JSONDecodeError'
                    assert error['kind'] == 510 and error['phase'] == 'observation'
                    assert error['decode_phase'] == 'inner_payload' and error['elapsed_ms'] >= 0
                    latest = report['events'][-1]
                    assert latest['event'] == 'decode_error' and latest['category'] == 'invalid_json'
                    assert report['alarm_candidates'] == report['ring_events_emitted'] == 0
                    assert report['last_denial']['reason'] == 'busy'
                    assert bad_reply.hex() not in json.dumps(report)
                    private_free(report)
                assert observed['events'] == downloaded['events']
                assert before == json.dumps([dict(entry.data), dict(entry.options)])
                assert before_attributes == json.dumps(camera.extra_state_attributes)
                private_free(await action('status'))
                private_free(await diagnostic())
                try:
                    await asyncio.wait_for(hub.acquire('synthetic-viewer'), .25)
                except ConnectionError:
                    pass
                else:
                    raise AssertionError('Media acquired while the trial owned its session')
                assert hub.thread is None and hub.session is None and not hub.consumers
                stopped = (await action('stop'))[camera.entity_id]
                assert stopped['status'] == 'stopped' and not stopped['active']
                assert stopped['cleanup']['tcp_closed'] and stopped['cleanup']['session_stop_sent']
                assert first.closed.is_set() and first.sent_counts[5005] == 1
                assert not hub.lock.locked() and not hub.control.lock.locked()
                assert (await action('status'))[camera.entity_id] == stopped
                # A second explicit candidate profile is stopped by real hub
                # unload, which must join its worker and release both locks.
                result = (await action('start', {'confirm': True, 'profile': 'long_connection'}))[camera.entity_id]
                assert result['status'] == 'observing' and result['duration_seconds'] == 300
                assert len(SyntheticSession.instances) == 2
                second = SyntheticSession.instances[-1]
                assert second.profile == (0, 7, 0)
                await hub.stop()
                final = hub._v1_doorbell_trial.snapshot()
                assert final['status'] == 'stopped' and not final['active']
                assert final['cleanup']['tcp_closed'] and second.closed.is_set()
                assert second.sent_counts[5005] == 1
                assert hub._v1_doorbell_trial._task.done() and hub._v1_doorbell_trial._session is None
                assert not hub.lock.locked() and not hub.control.lock.locked()
                assert hub.thread is None and hub.session is None and not hub.consumers
                assert hub.capabilities == original_capabilities and not hub.capabilities.local_ring
                assert before == json.dumps([dict(entry.data), dict(entry.options)])
                assert before_attributes == json.dumps(camera.extra_state_attributes)
                private_free(final)
                private_free(await diagnostic())
                media.assert_not_called()
        finally:
            await hub.stop()
            await hass.async_stop(force=True)
    print('REAL_HA_V1_DOORBELL_300S_OPT_IN_DENIAL_HISTORY_DECODE_PRIVACY_AND_UNLOAD_OK')


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(60):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
