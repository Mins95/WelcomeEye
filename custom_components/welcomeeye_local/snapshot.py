"""Fresh still-image capture on the WelcomeEye shared media lease."""
import asyncio
import time


DEFAULT_SNAPSHOT_TIMEOUT = 15.0


async def capture_fresh_image(hub, *, timeout=DEFAULT_SNAPSHOT_TIMEOUT):
    """Return a frame generated after this request, using one shared worker.

    The caller owns no media session outside this function.  A unique lease is
    acquired so an idle camera can start media temporarily, while an existing
    HLS/WebRTC/talkback consumer keeps its worker alive.  A cancelled request is
    deliberately re-raised; ``finally`` still releases its lease.
    """
    requested_generation = hub.image_generation
    consumer = object()
    started = time.monotonic()
    hub.snapshot_requests += 1
    had_media = bool(hub.consumers)
    try:
        await hub.acquire(consumer)
        if had_media:
            hub.snapshot_reused_media += 1
        else:
            hub.snapshot_started_media += 1
        image = await hub.wait_for_image(requested_generation, timeout)
        if image is None:
            hub.snapshot_timeouts += 1
            return None
        hub.snapshot_successes += 1
        hub.snapshot_wait_elapsed_ms = round((time.monotonic() - started) * 1000)
        return image
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        hub.snapshot_errors += 1
        hub.snapshot_last_error_type = type(exc).__name__
        return None
    finally:
        try:
            await hub.release(consumer, reason="snapshot_release")
        except Exception as exc:
            # A failed worker join must not turn an otherwise clean snapshot
            # result into a Home Assistant camera exception. Keep the failure
            # visible through the payload-free diagnostics counters.
            hub.snapshot_errors += 1
            hub.snapshot_last_error_type = type(exc).__name__
