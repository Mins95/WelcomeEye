"""Fresh secondary LT stills from the selected live worker, with no acquisition."""
import asyncio

from .legacy_channel import profile_binding
from .manual_snapshot import ManualSnapshotCapture

SNAPSHOT_TIMEOUT = 4.0


class LegacySnapshotCapture(ManualSnapshotCapture):
    def __init__(self, hub):
        super().__init__(hub)
        self.source = 'active_stream_channel_2'
        self.storage.device_slug += '_channel_2'
        self._waiters = set()
        self.diagnostics.update(channel=2, mode='existing_live_only',
            media_sessions_started=0, last_error_reason=None)

    @property
    def response_context(self):
        return {'channel': 2, 'source': self.source}

    def _context_valid(self, session, generation):
        hub, parent = self.hub, self.hub.parent
        return (not hub.stopped and hub.connected and bool(hub.consumers)
            and parent.session is session and session is not None
            and not session.closed.is_set()
            and parent.generation == generation and not parent.stop_event.is_set()
            and parent.current_profile == {'name': 'lt_second_panel', 'channel': 17, 'stream': 1, 'mode': 2}
            and parent._media_profile_binding == profile_binding(parent.entry.data))

    async def _capture_image(self):
        parent = self.hub.parent
        session, generation = parent.session, parent.generation
        self.diagnostics['last_error_reason'] = None
        if not self._context_valid(session, generation):
            self.diagnostics['last_error_reason'] = 'active_video_required'
            raise RuntimeError('Active secondary video required')
        waiter = asyncio.get_running_loop().create_future()
        self._waiters.add(waiter)
        try:
            async with asyncio.timeout(SNAPSHOT_TIMEOUT):
                image = await waiter
            if not self._context_valid(session, generation):
                raise RuntimeError('Secondary video session changed')
            return image
        except TimeoutError:
            self.diagnostics['last_error_reason'] = 'fresh_image_timeout'
            raise
        except RuntimeError:
            self.diagnostics['last_error_reason'] = 'video_session_closed'
            raise
        finally:
            self._waiters.discard(waiter)
            if not waiter.done():
                waiter.cancel()

    def publish(self, jpeg):
        """Called only by the parent's generation-checked channel-2 callback."""
        for waiter in tuple(self._waiters):
            if not waiter.done():
                waiter.set_result(jpeg)

    def media_closed(self):
        for waiter in tuple(self._waiters):
            if not waiter.done():
                waiter.set_exception(RuntimeError('Secondary video session closed'))

    async def close(self):
        self.media_closed()
        await super().close()
