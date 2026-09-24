"""Fresh still-image capture on the WelcomeEye shared media lease."""
import asyncio
import time


DEFAULT_SNAPSHOT_TIMEOUT = 15.0


class SnapshotLease:
    """Ring-only acquisitions must not cycle through media sessions on failure."""

    def __init__(self, single_session_attempt):
        self.single_session_attempt = single_session_attempt


async def capture_fresh_image(hub, *, timeout=DEFAULT_SNAPSHOT_TIMEOUT, single_session_attempt=False):
    """Acquire one shared lease and return only a post-request JPEG.

    The deadline includes acquisition. Normal device teardown can take longer
    than the capture deadline. Drain it even if the caller cancels repeatedly;
    no detached task may retain this request's media lease.
    """
    generation = hub.image_generation
    hub.snapshot_requests += 1
    task = asyncio.create_task(_capture(hub, generation, timeout, single_session_attempt))
    return await _finish_task(task, cancel_on_cancel=True)


async def _finish_task(task, *, cancel_on_cancel):
    """Propagate cancellation only after the owned operation has settled."""
    cancelled = False
    while not task.done():
        try:
            # wait() never cancels the owned task. Unlike a cancelled shield
            # future, it cannot log the inner exception before we retrieve it.
            await asyncio.wait({task})
        except asyncio.CancelledError:
            if cancel_on_cancel and not cancelled:
                task.cancel()
            cancelled = True
    if cancelled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


async def _capture(hub, generation, timeout, single_session_attempt):
    consumer = SnapshotLease(single_session_attempt)
    started = time.monotonic()
    hub.snapshot_last_error_type = None
    try:
        async with asyncio.timeout(timeout):
            reused = await hub.acquire(consumer)
            if reused:
                hub.snapshot_reused_media += 1
            else:
                hub.snapshot_started_media += 1
            image = await hub.wait_for_image(generation, timeout)
            if image is None:
                raise TimeoutError
            hub.snapshot_successes += 1
            return image
    except TimeoutError:
        hub.snapshot_timeouts += 1
        hub.snapshot_last_error_type = 'TimeoutError'
        return None
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        hub.snapshot_errors += 1
        hub.snapshot_last_error_type = type(exc).__name__
        return None
    finally:
        hub.snapshot_wait_elapsed_ms = round((time.monotonic() - started) * 1000)
        try:
            await _finish_task(
                asyncio.create_task(hub.release(consumer, reason='snapshot_release')),
                cancel_on_cancel=False,
            )
        except Exception as exc:
            hub.snapshot_errors += 1
            hub.snapshot_last_error_type = type(exc).__name__
