"""Memory-only ring photos, serialized on the existing shared media worker."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import time

from .snapshot import capture_fresh_image, _finish_task

CAPTURE_TIMEOUT = 20.0
RING_IMAGE_FALLBACK_DELAY = 4.0
NATIVE_TIMEOUT = RING_IMAGE_FALLBACK_DELAY
# Explicit product choice, not a protocol-level capture-completion guarantee.
MEDIA_FALLBACK_ENABLED = True


@dataclass(frozen=True)
class NativeRingPhoto:
    """A provider must correlate the returned photo with this specific ring."""

    sequence: int
    jpeg: bytes


async def native_ring_photo(hub, sequence, message):
    """No proven local LT visitor-memory retrieval exists yet.

    recordSubResUrl is supplied by the cloud alarm API; set.facepic.snap is
    a separate face-capture CGI, not a visitor-memory download. Do not send
    either speculatively or acquire a second media session here.
    """
    return None


def validate_jpeg(data):
    """Decode off the HA loop; never retain or log malformed payloads."""
    from PIL import Image

    if not isinstance(data, bytes) or not 4 <= len(data) <= 10 * 1024 * 1024:
        raise ValueError('Invalid image size')
    with Image.open(BytesIO(data)) as image:
        if image.format != 'JPEG' or image.width * image.height > 16_000_000:
            raise ValueError('Invalid JPEG')
        image.load()
    return data


class RingImageCapture:
    """One capture at a time; newer rings supersede uncommitted results."""

    def __init__(self, hub):
        self.hub = hub
        self.jpeg = None
        self.updated = None
        self.source = None
        self.image_sequence = None
        self.sequence = 0
        self.status = 'idle'
        self.entity_id = None
        self._pending = None
        self._task = None
        self._closed = False
        self._clock = time.monotonic
        self._call_later = hub.loop.call_later
        self._wake = asyncio.Event()
        self._timer = None
        self.diagnostics = {
            'ring_capture_requests': 0,
            'ring_capture_successes': 0,
            'ring_capture_native_successes': 0,
            'ring_capture_fallback_successes': 0,
            'ring_capture_failures': 0,
            'ring_capture_superseded': 0,
            'last_ring_capture_source': None,
            'last_ring_capture_elapsed_ms': None,
            'ring_image_generation': 0,
            'native_photo_capability': 'local_retrieval_not_identified',
            'native_error_type': None,
            'last_error_type': None,
            'media_fallback_enabled': MEDIA_FALLBACK_ENABLED,
            'fallback_delay_seconds': RING_IMAGE_FALLBACK_DELAY,
            'last_fallback_started_ms': None,
        }

    def request(self, sequence, message):
        """Called after the immediate ring event; no I/O and no await."""
        if self._closed:
            return
        self.sequence = sequence
        self.diagnostics['ring_capture_requests'] += 1
        if self._pending is not None:
            self.diagnostics['ring_capture_superseded'] += 1
        self._pending = (sequence, message, self._clock())
        self._wake.set()
        self.status = 'pending'
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name='welcomeeye-ring-image')

    async def _wait_fallback(self, sequence, deadline):
        """An interruptible timer, never a sleep in the ring receive path."""
        while not self._closed and sequence == self.sequence:
            remaining = deadline - self._clock()
            if remaining <= 0:
                return True
            self._wake.clear()
            self._timer = self._call_later(remaining, self._wake.set)
            try:
                await self._wake.wait()
            finally:
                self._timer.cancel()
                self._timer = None
        return False

    async def _run(self):
        while self._pending is not None and not self._closed:
            sequence, message, started = self._pending
            self._pending = None
            data, source = None, None
            self.diagnostics['native_error_type'] = None
            self.diagnostics['last_error_type'] = None
            self.diagnostics['last_fallback_started_ms'] = None
            try:
                remaining = CAPTURE_TIMEOUT - (self._clock() - started)
                if remaining <= 0:
                    raise TimeoutError
                try:
                    native_budget = min(NATIVE_TIMEOUT, remaining,
                                        started + RING_IMAGE_FALLBACK_DELAY - self._clock())
                    async with asyncio.timeout(max(0, native_budget)):
                        native = (await native_ring_photo(self.hub, sequence, message)
                                  if native_budget > 0 else None)
                        if native is not None:
                            if native.sequence != sequence:
                                raise ValueError('Uncorrelated native image')
                            data = await self.hub.hass.async_add_executor_job(validate_jpeg, native.jpeg)
                            source = 'native'
                except Exception as exc:
                    self.diagnostics['native_error_type'] = type(exc).__name__
                # Finish the prior operation safely, but do not open media for
                # an already superseded ring.
                if sequence != self.sequence or self._closed:
                    self.diagnostics['ring_capture_superseded'] += 1
                    continue
                if data is None:
                    if not MEDIA_FALLBACK_ENABLED:
                        raise RuntimeError('Automatic media fallback disabled')
                    if not await self._wait_fallback(sequence, started + RING_IMAGE_FALLBACK_DELAY):
                        self.diagnostics['ring_capture_superseded'] += 1
                        continue
                    remaining = CAPTURE_TIMEOUT - (self._clock() - started)
                    if remaining <= 0:
                        raise TimeoutError
                    # One acquisition at or after this ring's deadline. Existing
                    # viewers keep their shared session; no retry on failure.
                    self.diagnostics['last_fallback_started_ms'] = round((self._clock() - started) * 1000)
                    data = await capture_fresh_image(
                        self.hub, timeout=min(15.0, remaining), single_session_attempt=True,
                    )
                    if data is None:
                        raise TimeoutError
                    data = await self.hub.hass.async_add_executor_job(validate_jpeg, data)
                    source = 'fresh_snapshot'
                if self._clock() - started > CAPTURE_TIMEOUT:
                    raise TimeoutError
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                data = None
                self.diagnostics['last_error_type'] = type(exc).__name__
            if self._closed or self.hub.stopped:
                return
            if sequence != self.sequence:
                self.diagnostics['ring_capture_superseded'] += 1
                continue
            self.diagnostics['last_ring_capture_elapsed_ms'] = round((self._clock() - started) * 1000)
            if data is None:
                self.status = 'failed'
                self.diagnostics['ring_capture_failures'] += 1
            else:
                self.jpeg, self.source, self.image_sequence = data, source, sequence
                self.updated = datetime.now(timezone.utc)
                self.status = 'ready'
                self.diagnostics['ring_capture_successes'] += 1
                key = 'ring_capture_native_successes' if source == 'native' else 'ring_capture_fallback_successes'
                self.diagnostics[key] += 1
                self.diagnostics['last_ring_capture_source'] = source
                self.diagnostics['ring_image_generation'] += 1
            # Entity state is written before the event so automations see it.
            self.hub._notify()
            if data is not None and self.entity_id is not None:
                self.hub.hass.bus.async_fire('welcomeeye_local.ring_image', {
                    'entry_id': self.hub.entry.entry_id,
                    'image_entity_id': self.entity_id,
                    'ring_sequence': sequence,
                    'source': source,
                })

    async def close(self):
        """Suppress publication immediately and drain the owned lease safely."""
        self._closed = True
        self.entity_id = None
        self._pending = None
        self._wake.set()
        if self._task is not None:
            # Let an in-flight network operation reach its bounded completion;
            # snapshot teardown must not be abandoned by unload cancellation.
            await _finish_task(self._task, cancel_on_cancel=False)
            self._task = None
