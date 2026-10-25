"""Real HA/HomeKit source lookup and FFmpeg, with synthetic decoded media only.

Run in the HA runtime images used by the other verify_* tools. The HomeKit
requirements must be installed, and FFMPEG_BINARY or ffmpeg on PATH must exist
(imageio-ffmpeg is also accepted for isolated local developer environments).
No intercom, cloud endpoint, microphone, relay or camera discovery is used.
This verifies software interoperability, not an Apple Home hardware pairing.
"""
import asyncio
import base64
from fractions import Fraction
import importlib
import importlib.metadata
from io import BytesIO
import json
import math
import os
from pathlib import Path
import shutil
import socket
import struct
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
from urllib.parse import urlsplit

from homeassistant.components import camera as ha_camera
from homeassistant.components.camera.const import DATA_COMPONENT
from homeassistant.components.homekit.type_cameras import Camera as HomeKitCamera
from homeassistant.components.homekit.type_cameras import CONFIG_DEFAULTS
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er


async def wait_for(predicate, timeout=10):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.01)


def ffmpeg_binary():
    if binary := os.environ.get('FFMPEG_BINARY') or shutil.which('ffmpeg'):
        return binary
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError('Install FFmpeg or set FFMPEG_BINARY for this runtime check') from exc


def synthetic_frame(av, number, channel):
    frame = av.VideoFrame(64, 48, 'yuv420p')
    # Deliberately distinct luma ranges identify the selected channel after
    # H.264 decoding and HomeKit's transcode, not just the entity label.
    luma = (40 if channel == 1 else 150) + number % 10
    for index, plane in enumerate(frame.planes):
        plane.update(bytes((luma if index == 0 else 128,)) * plane.buffer_size)
    frame.pts, frame.time_base = number, Fraction(1, 20)
    return frame


def synthetic_audio(av, start, samples, channel):
    frame = av.AudioFrame(format='s16', layout='mono', samples=samples)
    frame.sample_rate = 8000
    frequency = 440 if channel == 1 else 880
    frame.planes[0].update(struct.pack('<' + 'h' * samples, *[
        round(7000 * math.sin(2 * math.pi * frequency * (start + i) / 8000))
        for i in range(samples)]))
    frame.pts, frame.time_base = start, Fraction(1, 8000)
    return frame


def install_legacy_producer(hub, av):
    """Replace only the device worker; real leases, dispatch and TS proxy run."""
    starts = []

    def worker(generation, stop):
        channel = hub._active_media_channel
        starts.append(channel)
        name, wire_channel, stream, mode = hub._profile_order()[0]
        hub.current_profile = dict(name=name, channel=wire_channel, stream=stream, mode=mode)

        class Sink:
            def write(self, data):
                hub.loop.call_soon_threadsafe(hub._dispatch, generation, hub._ts, bytes(data))
                return len(data)

        container = av.open(Sink(), mode='w', format='mpegts')
        encoded = container.add_stream('libx264', rate=20)
        encoded.width, encoded.height, encoded.pix_fmt = 64, 48, 'yuv420p'
        encoded.codec_context.gop_size = 20
        encoded.options = {'preset': 'ultrafast', 'tune': 'zerolatency'}
        encoded_audio = container.add_stream('aac', rate=8000)
        encoded_audio.layout = 'mono'
        encoded_audio.bit_rate = 24000
        hub.loop.call_soon_threadsafe(hub._dispatch, generation, hub._state, True)
        number = 0
        try:
            while not stop.wait(.05):
                frame = synthetic_frame(av, number, channel)
                jpeg = BytesIO()
                frame.to_image().save(jpeg, format='JPEG')
                hub.loop.call_soon_threadsafe(hub._dispatch, generation, hub._frame, 'video', frame)
                hub.loop.call_soon_threadsafe(hub._dispatch, generation, hub._image, jpeg.getvalue())
                if channel == 1:
                    for packet in encoded.encode(frame):
                        container.mux(packet)
                audio = synthetic_audio(av, number * 400, 400, channel)
                hub.loop.call_soon_threadsafe(hub._dispatch, generation, hub._frame, 'audio', audio)
                if channel == 1:
                    for packet in encoded_audio.encode(audio):
                        container.mux(packet)
                number += 1
        finally:
            container.close()

    hub._worker = worker
    return starts


def install_qv_producer(hub, av):
    """Replace only device acquisition/decoding; real LiveMedia leases run."""
    starts = []
    from custom_components.welcomeeye_local.connect3.protocol import MediaFrame
    for source in (hub, hub.channel2):
        live = source.live

        async def run(source=source, live=live):
            starts.append(source.channel)
            live.session_count += 1
            live.connected = True
            live.observation = {'stage': 'streaming', 'decoded_frames': 0}
            live._ready.set_result(None)
            encoder = av.CodecContext.create('libx264', 'w')
            encoder.width, encoder.height, encoder.pix_fmt = 64, 48, 'yuv420p'
            encoder.time_base, encoder.framerate = Fraction(1, 30), Fraction(30)
            encoder.thread_count, encoder.gop_size, encoder.max_b_frames = 1, 15, 0
            encoder.options = {'preset': 'ultrafast', 'tune': 'zerolatency',
                'x264-params': 'scenecut=0:repeat-headers=1'}
            number = 0
            try:
                while True:
                    frame = synthetic_frame(av, number, source.channel)
                    jpeg = BytesIO()
                    frame.to_image().save(jpeg, format='JPEG')
                    live._publish_image(jpeg.getvalue())
                    live.observation['decoded_frames'] += 1
                    for listener in tuple(source.frame_listeners):
                        listener('video', frame)
                    encoded_frame = synthetic_frame(av, number, source.channel)
                    encoded_frame.pts, encoded_frame.time_base = number, Fraction(1, 30)
                    for packet in encoder.encode(encoded_frame):
                        native = MediaFrame(1 if packet.is_keyframe else 0, 1,
                            64, 48, 30, 0, 0, bytes(packet))
                        for listener in tuple(source.video_packet_listeners):
                            listener(native)
                    # A real QV audio packet can deliver several PCM frames
                    # together. Keep their source PTS and the selected channel.
                    audio_count = (number + 1) * 8000 // 30 - number * 8000 // 30
                    audio = synthetic_audio(av, number * 8000 // 30, audio_count, source.channel)
                    for listener in tuple(source.frame_listeners):
                        listener('audio', audio)
                    number += 1
                    await asyncio.sleep(1 / 30)
            finally:
                encoder.encode(None)
                live.connected = False
                live.observation['stage'] = 'closed'

        live._run = run
    return starts


async def transcode(binary, source, destination, av, channel, *, legacy_primary):
    """Use HomeKit's default codec/tuning against the actual returned source."""
    process = await asyncio.create_subprocess_exec(binary, '-nostdin', '-hide_banner',
        '-loglevel', 'info', '-i', source, '-map', '0:v:0', '-map', '0:a:0',
        '-t', '1', '-c:v', 'libx264', '-profile:v', 'baseline', '-tune', 'zerolatency',
        '-c:a', 'pcm_s16le', '-f', 'matroska', '-y', str(destination),
        '-map', '0:v:0', '-an', '-c:v', 'copy', '-frames:v', '8', '-f', 'framehash', 'pipe:1',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(25):
            source_timing, errors = await process.communicate()
        # Do not print the private loopback bearer URL if FFmpeg reports it.
        safe_error = errors.decode(errors='replace').replace(source, '[private loopback source]')[-3000:]
        assert process.returncode == 0, f'FFmpeg failed with exit {process.returncode}: {safe_error}'
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()

    def decode():
        with av.open(str(destination)) as container:
            assert container.streams.video[0].codec_context.name == 'h264'
            frames = list(container.decode(video=0))
            assert len(frames) >= 8
            luma = bytes(frames[0].reformat(format='yuv420p').planes[0])[0]
            assert (20 < luma < 90) if channel == 1 else (110 < luma < 200), luma
        with av.open(str(destination)) as container:
            assert container.streams.audio
            audio = list(container.decode(audio=0))
            assert audio and sum(frame.samples for frame in audio) > 5000
            # FFT identifies the intended source, not just the audio label.
            import numpy as np
            samples = np.concatenate([frame.to_ndarray().reshape(-1) for frame in audio])
            assert np.sqrt(np.mean(samples.astype(float) ** 2)) > 500
            spectrum = np.abs(np.fft.rfft(samples.astype(float)))
            frequency = np.argmax(spectrum[1:]) + 1
            frequency *= audio[0].sample_rate / len(samples)
            assert abs(frequency - (440 if channel == 1 else 880)) < 20, frequency
        return len(frames)

    # The second output copies the incoming timestamped H.264 packets. Unlike
    # MPJPEG's guessed 25 fps, the declared timing must match the source's
    # paced 20 fps legacy or native 30 fps, without guessed MJPEG timing.
    lines = source_timing.decode().splitlines()
    time_base = Fraction(next(line.split(':', 1)[1].strip() for line in lines if line.startswith('#tb 0:')))
    pts = [int(line.split(',')[2]) for line in lines if line and not line.startswith('#')]
    assert len(pts) == 8 and all(right > left for left, right in zip(pts, pts[1:]))
    average_step = float((pts[-1] - pts[0]) * time_base / (len(pts) - 1))
    if legacy_primary:
        assert .045 <= average_step <= .055, average_step
    else:
        assert .025 <= average_step <= .065, average_step
    return await asyncio.to_thread(decode), round(average_step, 4)


class HomeKitFixture:
    """Real HomeKit media lifecycle, without network discovery or HAP pairing."""

    _async_get_stream_source = HomeKitCamera._async_get_stream_source
    _async_ffmpeg_watch = HomeKitCamera._async_ffmpeg_watch
    _async_stop_ffmpeg_watch = HomeKitCamera._async_stop_ffmpeg_watch
    start_stream = HomeKitCamera.start_stream
    stop_stream = HomeKitCamera.stop_stream

    def __init__(self, hass, entity, binary):
        self.hass, self.entity_id = hass, entity.entity_id
        self.display_name = 'Synthetic HomeKit camera'
        self.config = dict(CONFIG_DEFAULTS)
        self.config.update(support_audio=True, audio_packet_size=384, video_codec='copy')
        self._ffmpeg = SimpleNamespace(binary=binary)
        self.sessions = {}
        self.unexpected_stop = False
        self.video_output = self.audio_output = self.conversion_failed = False

    async def _async_log_stderr_stream(self, reader):
        fixture = self
        class ObservedReader:
            async def readline(self):
                line = await reader.readline()
                fixture.video_output |= b'Output #0' in line
                fixture.audio_output |= b'Output #1' in line
                fixture.conversion_failed |= (b'Conversion failed!' in line
                    or b'too large for max RTP payload' in line)
                return line
        await HomeKitCamera._async_log_stderr_stream(self, ObservedReader())

    def set_streaming_available(self, _):
        self.unexpected_stop = True


async def homekit_lifecycle(hass, entity, binary):
    """Apple-facing FFmpeg/SRTP stays on loopback; only its owner may survive."""
    fixture = HomeKitFixture(hass, entity, binary)
    # HomeKit uses the same port as the local RTP source. No receiver is bound:
    # FFmpeg sends SRTP only to its own loopback socket, never a real accessory.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]
    session = {'id': 'synthetic-homekit', 'stream_idx': 0}
    fixture.sessions[session['id']] = session
    stream_config = {'address': '127.0.0.1', 'v_port': port,
        'v_profile_id': b'\x00', 'v_max_bitrate': 128, 'v_ssrc': 12345,
        'v_srtp_key': base64.b64encode(bytes(range(30))).decode(), 'fps': 20,
        'a_max_bitrate': 24, 'a_port': port + 2, 'a_ssrc': 54321,
        'a_sample_rate': 16, 'a_packet_time': 20,
        'a_srtp_key': base64.b64encode(bytes(range(30))).decode()}
    received = 0

    def observe(kind, _):
        nonlocal received
        if kind == 'video':
            received += 1

    entity.hub.frame_listeners.add(observe)
    process = None
    try:
        assert await fixture.start_stream(session, stream_config)
        process = session['stream'].process
        await wait_for(lambda: received >= 30 and bool(entity.hub.consumers)
            and fixture.video_output and fixture.audio_output, timeout=20)
        await asyncio.sleep(.25)
        assert process.returncode is None and not fixture.conversion_failed
        assert not fixture.unexpected_stop
    finally:
        await fixture.stop_stream(session)
        entity.hub.frame_listeners.discard(observe)
    if process is not None:
        await asyncio.wait_for(process.wait(), 5)
    await wait_for(lambda: not entity.hub.consumers)
    parent = getattr(entity.hub, 'parent', entity.hub)
    if hasattr(parent, 'thread'):
        await wait_for(lambda: parent.thread is None and not parent.queues and not parent.handlers)
    else:
        await wait_for(lambda: entity.hub.live.task is None and parent._media_claim is None)
    assert process is not None and process.returncode is not None
    assert not fixture.unexpected_stop
    return 'real_homekit_av_outputs_start_stop_releases_http_and_last_lease'


async def main(root):
    sys.path.insert(0, str(root))
    import av
    package = 'custom_components.welcomeeye_local'
    cameras = importlib.import_module(package + '.camera')
    legacy = importlib.import_module(package + '.hub')
    qv = importlib.import_module(package + '.connect3.hub')
    r002 = importlib.import_module(package + '.r002.hub')
    binary = ffmpeg_binary()
    reports = []

    with tempfile.TemporaryDirectory() as temporary:
        hass = HomeAssistant(temporary)
        hass.config_entries = ConfigEntries(hass, {})
        if hasattr(dr, 'async_setup'):
            dr.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        registry = er.async_get(hass)
        entities = {}
        # Real camera and HomeKit helpers resolve real entities through HA's
        # component contract; only platform registration is kept lightweight.
        hass.data[DATA_COMPONENT] = SimpleNamespace(get_entity=entities.get)

        for variant in ('connect_v1', 'connect2_r001', 'connect2_r002', 'connect3'):
            is_legacy = variant in ('connect_v1', 'connect2_r001')
            family = ('legacy_owsp' if is_legacy else
                      'r002_experimental' if variant == 'connect2_r002' else 'connect3_qv_experimental')
            entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
                title='Synthetic HomeKit ' + variant, unique_id='synthetic-homekit-' + variant,
                data={'host': '192.0.2.1', 'device_variant': variant, 'protocol_family': family,
                      'username': 'SYNTHETIC_USER', 'password': 'SYNTHETIC_PASSWORD',
                      'auth_code': 'SYNTHETIC_AUTH', 'channel': 16,
                      'experimental_video': True, 'second_channel_enabled': True,
                      'certificate_sha256': 'a' * 64, 'experimental_outputs': False},
                options={}, source='user', subentries_data=None, discovery_keys=MappingProxyType({}))
            hass.config_entries._entries[entry.entry_id] = entry
            hub_type = legacy.WelcomeEyeHub if is_legacy else (
                r002.R002InvestigationHub if variant == 'connect2_r002' else qv.Connect3Hub)
            hub = hub_type(hass, entry)
            entry.runtime_data = hub
            hub.stopped = False
            if is_legacy:
                starts = install_legacy_producer(hub, av)
                # Production legacy setup already exposes this loopback-only
                # MPEG-TS proxy. Skip ring/cloud startup in this focused check.
                hub.server = await asyncio.start_server(hub._serve, '127.0.0.1', 0, limit=8192)
                port = hub.server.sockets[0].getsockname()[1]
                hub.url = f'http://127.0.0.1:{port}{hub.path}'
            else:
                starts = install_qv_producer(hub, av)
            created = []
            await cameras.async_setup_entry(hass, entry, created.extend)
            assert len(created) == 2
            try:
                for channel, entity in enumerate(created, 1):
                    entity.hass = hass
                    expected_id = entry.unique_id + ('_camera' if channel == 1 else '_camera_channel_2')
                    assert entity.unique_id == expected_id
                    registered = registry.async_get_or_create('camera', 'welcomeeye_local',
                        entity.unique_id, config_entry=entry)
                    entity.entity_id = registered.entity_id
                    entities[entity.entity_id] = entity
                    assert entity._supports_native_async_webrtc is (not is_legacy or channel == 2)
                    assert registered.unique_id == expected_id
                    before = len(starts)
                    source = await ha_camera.async_get_stream_source(hass, entity.entity_id)
                    accessory = SimpleNamespace(hass=hass, config={}, entity_id=entity.entity_id)
                    assert await HomeKitCamera._async_get_stream_source(accessory) == source
                    assert source and urlsplit(source).hostname == '127.0.0.1'
                    assert len(starts) == before and not entity.hub.consumers
                    assert source not in json.dumps(entity.extra_state_attributes)
                    if is_legacy and channel == 1:
                        assert source == hub.url

                    # A regular HA viewer and the FFmpeg reader share one
                    # worker/session. HTTP closure must retain the first owner.
                    owner = object()
                    await entity.hub.acquire(owner)
                    started = len(starts)
                    assert started == before + 1
                    decoded, step = await transcode(binary, source,
                        Path(temporary) / f'{variant}-{channel}.mkv', av, channel,
                        legacy_primary=is_legacy and channel == 1)
                    await wait_for(lambda: len(entity.hub.consumers) == 1)
                    assert len(starts) == started and entity.hub.connected
                    await entity.hub.release(owner)
                    await wait_for(lambda: not entity.hub.consumers)
                    if is_legacy:
                        assert hub.thread is None
                    else:
                        assert entity.hub.live.task is None and hub._media_claim is None

                    # Real HomeKit start/stop opens a fresh FFmpeg process and
                    # releases the final device lease without forcing hub.stop.
                    lifecycle = await homekit_lifecycle(hass, entity, binary)
                    assert len(starts) == started + 1
                    reports.append({'variant': variant, 'channel': channel,
                        'source': 'mpegts',
                        'homekit_source_lookup': 'pass', 'ffmpeg_h264_frames': decoded,
                        'source_average_frame_seconds': step,
                        'shared_lease_and_reopen': 'pass', 'native_rtc_preserved': True,
                        'entity_unique_id_preserved': True, 'homekit_lifecycle': lifecycle,
                        'local_transport': (entity.hub._local_video_stream.diagnostics
                            if getattr(entity.hub, '_local_video_stream', None) else
                            {'video_mode': 'existing_mpegts', 'audio_codec': 'aac'})})
                    print(f'HomeKit/FFmpeg audio+video {variant} channel {channel}: passed', flush=True)
            finally:
                for entity in created:
                    await entity.async_will_remove_from_hass()
                    entities.pop(entity.entity_id, None)
                await hub.stop()
            assert not hub.consumers
        await hass.async_stop(force=True)
    print(json.dumps({'validation': 'actual_HA_HomeKit_FFmpeg_synthetic_media',
        'homeassistant': importlib.metadata.version('homeassistant'),
        'hardware_validated': False, 'device_io': 'none', 'physical_commands': 0,
        'apple_home_pairing_tested': False, 'runs': reports}))


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(240):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
