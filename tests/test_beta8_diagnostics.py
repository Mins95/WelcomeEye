"""Failure context must survive teardown without leaking exception payloads."""
import asyncio
import importlib
import json
import types
from unittest.mock import patch

from beta8_helpers import entry, load_hub
from test_beta8_lifecycle import av_response, harness
from test_control_coordination import PACKAGE


def test_diagnostics_redact_arbitrary_exception_text_and_keep_failure_context():
    async def run():
        hub = load_hub().WelcomeEyeHub(types.SimpleNamespace(), entry())
        hub.entry.runtime_data = hub
        secret = 'SENSITIVE_UID_PASSWORD_192.0.2.55'
        hub.last_error_message = hub.control.last_error_message = hub.ring_listener.last_error_message = secret
        hub.lifecycle.update(worker_exit_reason='remote_tcp_eof', pending_output_sent=True,
                             pending_output_state_at_exit='sent', tcp_closed=True)
        diagnostics = importlib.import_module(PACKAGE + '.diagnostics')
        result = await diagnostics.async_get_config_entry_diagnostics(hub.hass, hub.entry)
        serialized = json.dumps(result)
        assert secret not in serialized and 'TESTUID000' not in serialized
        assert 'unused' not in serialized and 'test-code' not in serialized
        assert result['media']['lifecycle']['worker_exit_reason'] == 'remote_tcp_eof'
        assert result['media']['lifecycle']['v1_device_release_complete'] is None
        assert result['webrtc']['status']['TURN_USED'] is None
    asyncio.run(run())


def test_5010_received_is_recorded_without_asserting_physical_release():
    async def run():
        hub, devices, patches = harness()
        with patches:
            await hub.acquire('viewer')
            hub._record_lt_stop_av_response(hub.session, av_response())
            await hub.release('viewer')
            assert hub.lifecycle['stop_av_response_received']
            assert hub.lifecycle['stop_av_result'] == 1
            assert hub.lifecycle['v1_device_release_complete'] is None
    asyncio.run(run())


def test_framing_snapshot_error_does_not_prevent_tcp_or_pipeline_close():
    async def run():
        hub, devices, patches = harness()
        with patches:
            await hub.acquire('viewer')
            with patch.object(hub.session, 'framing_diagnostics', side_effect=RuntimeError('private data')):
                await hub.release('viewer')
            assert devices[0].closed and hub.session is None and hub.thread is None
            assert hub.lifecycle['cleanup_errors'] == [{'stage': 'framing_snapshot', 'type': 'RuntimeError'}]
            assert 'private data' not in json.dumps(hub.lifecycle)
            assert hub.previous_lifecycles[-1]['cleanup_errors'] == hub.lifecycle['cleanup_errors']
    asyncio.run(run())


def test_codec_counters_survive_new_pipeline_without_double_counting():
    async def run():
        hub = load_hub().WelcomeEyeHub(types.SimpleNamespace(), entry())
        first = types.SimpleNamespace(video_decode_errors=2, video_decoder_resets=2,
                                      video_dropped_until_keyframe=4)
        second = types.SimpleNamespace(video_decode_errors=1, video_decoder_resets=1,
                                       video_dropped_until_keyframe=3)
        hub._record_codec(first)
        hub._record_codec(first)
        hub._record_codec(second)
        assert hub.codec_totals == {'video_decode_errors': 3, 'video_decoder_resets': 3,
                                     'video_dropped_until_keyframe': 7}
    asyncio.run(run())
