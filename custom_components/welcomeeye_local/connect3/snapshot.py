"""User snapshots of an already opened QV live session; never start a call."""
from ..manual_snapshot import ManualSnapshotCapture


class LiveSnapshotCapture(ManualSnapshotCapture):
    """Keep each camera's JPEG and Media files separate without acquiring media."""

    def __init__(self, hub):
        super().__init__(hub)
        self.channel = hub.channel
        self.source = f'active_stream_channel_{self.channel}'
        self.storage.device_slug += f'_channel_{self.channel}'
        self.diagnostics.update(channel=self.channel, mode='existing_live_only',
            media_sessions_started=0, last_error_reason=None)

    @property
    def response_context(self):
        return {'channel': self.channel, 'source': self.source}

    async def _capture_image(self):
        self.diagnostics['last_error_reason'] = None
        try:
            return await self.hub.live.capture_active_image()
        except TimeoutError:
            self.diagnostics['last_error_reason'] = 'fresh_image_timeout'
            raise
        except RuntimeError:
            self.diagnostics['last_error_reason'] = 'active_video_required'
            raise RuntimeError('Active video required') from None
