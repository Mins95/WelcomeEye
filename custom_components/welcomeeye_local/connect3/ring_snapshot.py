"""Opt-in QV ring photos using one lease on the selected shared video session."""
import asyncio

from ..ring_image import RingImageCapture
from ..snapshot import SnapshotLease, _finish_task


class _CaptureUnavailable(RuntimeError):
    """A fixed category, without endpoint, device or exception details."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__('Ring image capture unavailable')


def _running(task):
    return task is not None and not task.done()


class QVRingImageCapture(RingImageCapture):
    """Keep each channel's last ring photo and private Media files independent."""

    default_enabled = False

    def __init__(self, hub):
        if type(hub.channel) is not int or hub.channel not in (1, 2):
            raise ValueError('Unsupported ring image channel')
        self.channel = hub.channel
        self.source_name = f'fresh_snapshot_channel_{self.channel}'
        super().__init__(hub)
        self.storage.device_slug += f'_channel_{self.channel}'
        self.diagnostics.update(channel=self.channel, mode='single_shared_session',
            media_sessions_started=0, media_sessions_reused=0, last_error_reason=None)

    @property
    def event_context(self):
        return {'channel': self.channel}

    def _check_available(self, live):
        hub = self.hub
        parent = getattr(hub, 'parent', hub)
        if self._closed or not self.enabled or hub.stopped:
            raise _CaptureUnavailable('capture_disabled')
        if (hub.live is not live or live.channel != self.channel
                or not hub.capabilities.live_media):
            raise _CaptureUnavailable('channel_unavailable')
        if getattr(parent, '_media_claim', None) not in (None, live):
            raise _CaptureUnavailable('other_channel_busy')
        if getattr(getattr(parent, 'control', None), '_busy', False):
            raise _CaptureUnavailable('control_busy')
        listener = getattr(parent, 'autonomous_doorbell', None)
        if (_running(getattr(parent, '_task', None))
                or (listener is not None and listener.active)):
            raise _CaptureUnavailable('diagnostic_busy')
        # A stable microphone on this exact live session is harmless: the
        # photo only consumes video. Never initiate media during negotiation,
        # closing, or an active microphone on the other channel.
        channels = (parent, getattr(parent, 'channel2', None))
        for target in channels:
            if target is None:
                continue
            talk = getattr(target, 'talkback', None)
            if talk is None:
                continue
            occupied = talk.owner is not None or talk.active
            transitioning = any(lock is not None and lock.locked() for lock in (
                getattr(talk, '_lock', None), getattr(talk, '_close_lock', None)))
            if not occupied and not transitioning:
                continue
            if (target is not hub or transitioning or not talk.active
                    or not live.connected or live.session is None
                    or getattr(talk, '_live_session', None) is not live.session):
                raise _CaptureUnavailable('microphone_busy')

    async def _capture_image(self, *, timeout):
        # The operation owns its lease; repeated cancellation must drain that
        # operation rather than detach a pending acquisition or cleanup.
        task = asyncio.create_task(self._capture_once(timeout), name='welcomeeye-qv-ring-photo')
        return await _finish_task(task, cancel_on_cancel=True)

    async def _capture_once(self, timeout):
        live = self.hub.live
        lease = SnapshotLease(single_session_attempt=True)
        before = live.session_count
        attempted = acquired = False
        self.diagnostics['last_error_reason'] = None
        try:
            async with asyncio.timeout(timeout):
                self._check_available(live)
                attempted = True
                await self.hub.acquire(lease)
                acquired = True
                self._check_available(live)
                session = live.session
                if (session is None or not live.connected
                        or getattr(session, '_close_task', None) is not None):
                    raise _CaptureUnavailable('session_unavailable')
                image = await live.capture_active_image()
                if (self.hub.live is not live or live.session is not session
                        or not live.connected or live.channel != self.channel):
                    raise _CaptureUnavailable('session_changed')
                return image
        except asyncio.CancelledError:
            self.diagnostics['last_error_reason'] = 'capture_cancelled'
            raise
        except TimeoutError:
            self.diagnostics['last_error_reason'] = 'fresh_image_timeout'
            raise
        except _CaptureUnavailable as exc:
            self.diagnostics['last_error_reason'] = exc.reason
            raise
        except Exception as exc:
            self.diagnostics['last_error_reason'] = (
                'other_channel_busy' if getattr(exc, 'reason', None) == 'other_channel_busy'
                else 'fresh_image_failed' if acquired else 'media_acquisition_failed')
            raise RuntimeError('Ring image capture failed') from None
        finally:
            if attempted:
                started = max(0, live.session_count - before)
                self.diagnostics['media_sessions_started'] += started
                if acquired and not started:
                    self.diagnostics['media_sessions_reused'] += 1
            # Failed acquisition already releases its owner. Unload can also
            # remove it. Do not release an absent owner and accidentally halt
            # a later worker; only this exact lease is ours to drain.
            if lease in live.consumers:
                try:
                    await _finish_task(asyncio.create_task(
                        self.hub.release(lease, reason='ring_snapshot_release')),
                        cancel_on_cancel=False)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.diagnostics['last_error_reason'] = 'release_failed'
                    raise RuntimeError('Ring image cleanup failed') from None
