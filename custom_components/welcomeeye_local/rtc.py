"""Native WebRTC video/audio with per-viewer cleanup and bounded frame queues."""
import asyncio
from dataclasses import dataclass, field
import logging

import av
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription, RTCConfiguration
from aiortc.mediastreams import MediaStreamError
from aiortc.sdp import candidate_from_sdp
from homeassistant.components.camera.webrtc import WebRTCAnswer, WebRTCError

_LOGGER = logging.getLogger(__name__)


class DeviceTrack(MediaStreamTrack):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.queue = asyncio.Queue(maxsize=2 if kind == 'video' else 10)

    def feed(self, frame):
        if self.readyState != 'live':
            return
        while self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(frame)

    async def recv(self):
        if self.readyState != 'live':
            raise MediaStreamError
        try:
            frame = await asyncio.wait_for(self.queue.get(), 15)
        except TimeoutError as exc:
            raise MediaStreamError from exc
        if frame is None:
            raise MediaStreamError
        # Each encoder owns its frame: aiortc may mutate picture type and PTS.
        if self.kind == 'video':
            copy = av.VideoFrame.from_ndarray(frame.to_ndarray(format='yuv420p'), format='yuv420p')
        else:
            copy = av.AudioFrame.from_ndarray(frame.to_ndarray(),
                format=frame.format.name, layout=frame.layout.name)
            copy.sample_rate = frame.sample_rate
        copy.pts, copy.time_base = frame.pts, frame.time_base
        return copy

    def stop(self):
        super().stop()
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queue.put_nowait(None)


@dataclass
class Viewer:
    pc: RTCPeerConnection
    lease: object = field(default_factory=object)
    tracks: dict = field(default_factory=dict)
    timeout: asyncio.Task | None = None


class WebRTCManager:
    def __init__(self, hub):
        self.hub = hub
        self.viewers = {}
        self.tasks = set()
        hub.frame_listeners.add(self._frame)
        hub.close_listeners.add(self.close_all)

    def _diag(self, **values):
        self.hub.webrtc_diagnostics.update(values)
        self.hub.webrtc_diagnostics['active_viewers'] = len(self.viewers)

    def _frame(self, kind, frame):
        for viewer in tuple(self.viewers.values()):
            if track := viewer.tracks.get(kind):
                track.feed(frame)

    async def offer(self, sdp, session_id, send_message):
        self._diag(
            stage='offer_received',
            failed_at_stage=None,
            last_exception_type=None,
            requested_tracks=[],
            created_tracks=[],
            candidate_event=None,
        )
        if session_id in self.viewers or len(self.viewers) >= 4 or self.hub.stopped:
            self._diag(stage='rejected_busy')
            send_message(WebRTCError(code='busy', message='Nombre maximal de lecteurs atteint ou caméra arrêtée'))
            return
        viewer = Viewer(RTCPeerConnection(RTCConfiguration(iceServers=[])))
        self.viewers[session_id] = viewer
        self._diag(stage='peer_connection_created', connection_state=viewer.pc.connectionState)

        @viewer.pc.on('connectionstatechange')
        async def state_changed():
            state = viewer.pc.connectionState
            self._diag(connection_state=state)
            if state == 'connected':
                self._diag(stage='connection_connected')
                if viewer.timeout:
                    viewer.timeout.cancel()
            elif state in ('failed', 'closed'):
                self._diag(stage=f'connection_{state}')
                await self.close(session_id)

        async def expire_unconnected():
            await asyncio.sleep(25)
            if viewer.pc.connectionState != 'connected':
                self._diag(stage='connection_timeout', connection_state=viewer.pc.connectionState)
                await self.close(session_id)

        viewer.timeout = asyncio.create_task(expire_unconnected())
        try:
            async with asyncio.timeout(22):
                self._diag(stage='setting_remote_description')
                await viewer.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type='offer'))
                requested = [
                    f'{transceiver.kind}:{transceiver.direction}'
                    for transceiver in viewer.pc.getTransceivers()
                ]
                self._diag(stage='remote_description_set', requested_tracks=requested)

                # Only respond to media requested by the receiving browser.
                for transceiver in viewer.pc.getTransceivers():
                    if transceiver.kind in ('video', 'audio') and transceiver.direction in ('recvonly', 'sendrecv'):
                        track = DeviceTrack(transceiver.kind)
                        viewer.tracks[transceiver.kind] = track
                        viewer.pc.addTrack(track)
                created = sorted(viewer.tracks)
                self._diag(stage='tracks_created', created_tracks=created)
                if not viewer.tracks:
                    raise ValueError('Offer does not request media')
                if self.viewers.get(session_id) is not viewer:
                    return

                self._diag(stage='acquiring_media')
                await self.hub.acquire(viewer.lease)
                self._diag(stage='media_acquired')
                if self.viewers.get(session_id) is not viewer:
                    await self.hub.release(viewer.lease)
                    return

                self._diag(stage='creating_answer')
                answer = await viewer.pc.createAnswer()
                self._diag(stage='answer_created')
                await viewer.pc.setLocalDescription(answer)
                self._diag(stage='local_description_set')
                send_message(WebRTCAnswer(answer=viewer.pc.localDescription.sdp))
                self._diag(stage='answer_sent')
        except asyncio.CancelledError:
            self._diag(stage='cancelled')
            await self.close(session_id)
            raise
        except Exception as exc:
            failed_stage = self.hub.webrtc_diagnostics.get('stage')
            self._diag(
                stage='failed',
                failed_at_stage=failed_stage,
                last_exception_type=type(exc).__name__,
                connection_state=viewer.pc.connectionState,
            )
            _LOGGER.warning('Cannot start WelcomeEye WebRTC (%s at %s)', type(exc).__name__, failed_stage)
            await self.close(session_id)
            send_message(WebRTCError(code='stream_failed', message='Impossible de démarrer la vidéo WelcomeEye'))

    async def candidate(self, session_id, candidate):
        if not (viewer := self.viewers.get(session_id)):
            return
        if not candidate.candidate:
            self._diag(candidate_event='end_of_candidates')
            await viewer.pc.addIceCandidate(None)
            return
        self._diag(candidate_event='candidate_received')
        ice = candidate_from_sdp(candidate.candidate.removeprefix('candidate:'))
        ice.sdpMid = candidate.sdp_mid
        ice.sdpMLineIndex = candidate.sdp_m_line_index
        await viewer.pc.addIceCandidate(ice)

    def schedule_close(self, session_id):
        task = asyncio.create_task(self.close(session_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def close(self, session_id):
        if not (viewer := self.viewers.pop(session_id, None)):
            self._diag()
            return
        if viewer.timeout and viewer.timeout is not asyncio.current_task():
            viewer.timeout.cancel()
        for track in viewer.tracks.values():
            track.stop()
        try:
            await self.hub.release(viewer.lease)
        finally:
            state = viewer.pc.connectionState
            await viewer.pc.close()
            self._diag(connection_state=state)

    async def close_all(self):
        await asyncio.gather(*(self.close(key) for key in tuple(self.viewers)))
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
