"""Per-stream JPEG warmup, independent of live video and ring scheduling."""

SNAPSHOT_STABILIZATION_SECONDS = 4.0


class SnapshotReadiness:
    """Accept a newly decoded image only after the camera has had time to settle.

    A clock alone never publishes a cached image. The caller must observe the
    new frame being considered, using a monotonic timestamp. Each new stream
    owns a fresh instance (or resets it); established streams do not wait again.
    Night-mode monochrome images remain valid after the bounded warmup.
    """

    def __init__(self, delay_seconds=SNAPSHOT_STABILIZATION_SECONDS):
        self.delay_seconds = delay_seconds
        self.reset()

    def reset(self):
        self.first_frame_at = None
        self.ready = False

    def observe(self, now):
        if self.first_frame_at is None:
            self.first_frame_at = now
        self.ready = now - self.first_frame_at >= self.delay_seconds
        return self.ready
