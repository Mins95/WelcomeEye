"""Native CRC32C + real SCTP/bidirectional audio/video, without device access."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.metadata
import json
import platform
import random
import time
import warnings

# Capture for assertions, never suppress a production warning.
with warnings.catch_warnings(record=True) as observed:
    warnings.simplefilter('always')
    import google_crc32c
    import google_crc32c.cext
    from google_crc32c.python import value as python_crc
    from aiortc import AudioStreamTrack, VideoStreamTrack, RTCPeerConnection, RTCConfiguration

assert google_crc32c.implementation == 'c'
assert not any('pure python' in str(w.message).lower() for w in observed)
for warning in observed:
    print(str(warning.message))

vectors = [(b'', 0), (b'123456789', 0xE3069283),
           (bytes(32), 0x8A9136AA), (bytes([255]) * 32, 0x62A8AB43),
           (bytes(range(32)), 0x46DD794E)]
for data, crc in vectors:
    assert google_crc32c.value(data) == python_crc(data) == crc
    split = len(data) // 2
    assert google_crc32c.extend(google_crc32c.value(data[:split]), data[split:]) == crc
    checksum = google_crc32c.Checksum()
    checksum.update(data[:split])
    checksum.update(data[split:])
    assert checksum.digest() == crc.to_bytes(4, 'big')

rng = random.Random(412)
samples = [rng.randbytes(n) for n in (1, 31, 32, 255, 1200, 4096, 65536)]
expected = [python_crc(data) for data in samples]
with ThreadPoolExecutor(max_workers=4) as pool:
    assert list(pool.map(google_crc32c.value, samples * 100)) == expected * 100

data = samples[4]
timings = {}
for name, fn, count in [('native', google_crc32c.value, 100000), ('python', python_crc, 1000)]:
    started = time.perf_counter()
    for _ in range(count):
        fn(data)
    timings[name + '_us_per_1200_bytes'] = (time.perf_counter() - started) * 1e6 / count


async def loopback():
    a, b = [RTCPeerConnection(RTCConfiguration(iceServers=[])) for _ in range(2)]
    done = asyncio.get_running_loop().create_future()
    consumers = []
    counts = {'video': 0, 'down_audio': 0, 'mic_audio': 0}
    channel = a.createDataChannel('crc32c-sctp-proof')
    @channel.on('open')
    def send():
        channel.send(b'123456789')
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
    def browser_microphone(track):
        consumers.append(asyncio.create_task(consume(track, 'mic_audio')))
    @b.on('track')
    def device_media(track):
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
            assert await done == b'123456789'
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
        'backend': google_crc32c.implementation,
        'google_crc32c': importlib.metadata.version('google-crc32c'),
        'aiortc': importlib.metadata.version('aiortc'),
        'homeassistant': importlib.metadata.version('homeassistant'),
        'python': platform.python_version(), 'architecture': platform.machine(),
        'reference_vectors': len(vectors), 'threaded_checks': len(samples) * 100,
        'timings': timings, 'webrtc_cycles': cycles,
    }, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
