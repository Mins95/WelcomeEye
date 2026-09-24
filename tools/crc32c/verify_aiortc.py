"""Prove the actual SCTP CRC callable, native CRC parity and bidirectional media."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.metadata
import inspect
import json
import platform
import random
import sys
import time
import warnings

with warnings.catch_warnings(record=True) as observed:
    warnings.simplefilter('always')
    import crc32c
    from aiortc import AudioStreamTrack, VideoStreamTrack, RTCPeerConnection, RTCConfiguration
    from aiortc.rtcsctptransport import crc32c as sctp_crc

assert sctp_crc is crc32c.crc32c and inspect.isbuiltin(sctp_crc)
assert 'google_crc32c' not in sys.modules, 'aiortc must not import the old backend'
assert not any('crc32c' in str(w.message).lower() for w in observed), observed

# Independent bitwise Castagnoli reference, never used by the integration.
def reference(data):
    value = 0xFFFFFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0x82F63B78 if value & 1 else 0)
    return value ^ 0xFFFFFFFF


vectors = [(b'', 0), (b'123456789', 0xE3069283),
           (bytes(32), 0x8A9136AA), (bytes([255]) * 32, 0x62A8AB43),
           (bytes(range(32)), 0x46DD794E)]
for data, expected in vectors:
    assert sctp_crc(data) == reference(data) == expected
    split = len(data) // 2
    assert sctp_crc(data[split:], sctp_crc(data[:split])) == expected
rng = random.Random(412)
samples = [rng.randbytes(n) for n in (1, 31, 32, 255, 1200, 4096, 65536)]
expected = [reference(data) for data in samples]
with ThreadPoolExecutor(max_workers=4) as pool:
    assert list(pool.map(sctp_crc, samples * 100)) == expected * 100

timings = {}
for label, fn, count in [('native', sctp_crc, 100000), ('python_reference', reference, 100)]:
    started = time.perf_counter()
    for _ in range(count):
        fn(samples[4])
    timings[label + '_us_per_1200_bytes'] = (time.perf_counter() - started) * 1e6 / count


async def loopback():
    a, b = [RTCPeerConnection(RTCConfiguration(iceServers=[])) for _ in range(2)]
    done = asyncio.get_running_loop().create_future()
    consumers = []
    counts = {'video': 0, 'down_audio': 0, 'mic_audio': 0}
    channel = a.createDataChannel('native-crc-proof')
    @channel.on('open')
    def send():
        channel.send(b'123456789' * 1024)
    @b.on('datachannel')
    def receive(ch):
        @ch.on('message')
        def message(value):
            if not done.done():
                done.set_result(value)
    async def consume(track, name):
        for _ in range(5):
            await track.recv()
            counts[name] += 1
    @a.on('track')
    def microphone(track):
        consumers.append(asyncio.create_task(consume(track, 'mic_audio')))
    @b.on('track')
    def media(track):
        consumers.append(asyncio.create_task(consume(track, 'video' if track.kind == 'video' else 'down_audio')))
    a.addTrack(VideoStreamTrack())
    a.addTrack(AudioStreamTrack())
    b.addTrack(AudioStreamTrack())
    try:
        async with asyncio.timeout(25):
            await a.setLocalDescription(await a.createOffer())
            await b.setRemoteDescription(a.localDescription)
            await b.setLocalDescription(await b.createAnswer())
            await a.setRemoteDescription(b.localDescription)
            assert await done == b'123456789' * 1024
            await asyncio.gather(*consumers)
            assert counts == {'video': 5, 'down_audio': 5, 'mic_audio': 5}, counts
    finally:
        for task in consumers:
            task.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)
        await asyncio.gather(a.close(), b.close())
    assert a.connectionState == b.connectionState == 'closed'
    return counts


async def main():
    cycles = [await loopback() for _ in range(3)]
    print(json.dumps({
        'backend': 'crc32c native C', 'crc32c': importlib.metadata.version('crc32c'),
        'hardware_accelerated': crc32c.hardware_based,
        'aiortc': importlib.metadata.version('aiortc'),
        'python': platform.python_version(), 'architecture': platform.machine(),
        'reference_vectors': len(vectors), 'threaded_checks': 700,
        'crc_warning_count': 0, 'timings': timings, 'webrtc_cycles': cycles,
    }, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
