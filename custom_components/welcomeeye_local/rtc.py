"""Native WebRTC video/audio with per-viewer cleanup and bounded frame queues."""
import asyncio
from dataclasses import dataclass, field
import logging
import json
import time

import av
from aiortc import (
    MediaStreamTrack,
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.mediastreams import MediaStreamError
from aiortc.sdp import candidate_from_sdp
from homeassistant.components.camera.webrtc import WebRTCAnswer, WebRTCError
from homeassistant.components.web_rtc import async_get_ice_servers

_LOGGER = logging.getLogger(__name__)

_DEFAULT_STUN_URLS = [
    "stun:stun.home-assistant.io:3478",
    "stun:stun.home-assistant.io:80",
]


def _ice_configuration(hass):
    """Build an aiortc configuration from Home Assistant's current ICE servers."""
    source = "home_assistant"
    try:
        servers = async_get_ice_servers(hass)
    except (AttributeError, KeyError, RuntimeError):
        servers = []
        source = "home_assistant_default_fallback"

    if not servers:
        return RTCConfiguration(
            iceServers=[RTCIceServer(urls=_DEFAULT_STUN_URLS)]
        ), {
            "ice_server_source": source,
            "ice_server_count": 1,
            "stun_server_count": len(_DEFAULT_STUN_URLS),
            "turn_server_count": 0,
            "turn_available": False,
        }

    converted = []
    stun_count = 0
    turn_count = 0
    for server in servers:
        urls = getattr(server, "urls", [])
        if isinstance(urls, str):
            url_list = [urls]
        elif urls:
            url_list = list(urls)
        else:
            continue
        for url in url_list:
            value = str(url).lower()
            if value.startswith("stun:"):
                stun_count += 1
            elif value.startswith(("turn:", "turns:")):
                turn_count += 1
        converted.append(
            RTCIceServer(
                urls=urls,
                username=getattr(server, "username", None),
                credential=getattr(server, "credential", None),
            )
        )

    if not converted:
        converted = [RTCIceServer(urls=_DEFAULT_STUN_URLS)]
        stun_count = len(_DEFAULT_STUN_URLS)
        source = "home_assistant_default_fallback"

    return RTCConfiguration(iceServers=converted), {
        "ice_server_source": source,
        "ice_server_count": len(converted),
        "stun_server_count": stun_count,
        "turn_server_count": turn_count,
        "turn_available": turn_count > 0,
    }


def _candidate_metadata_from_sdp(sdp):
    """Return candidate types/protocols without retaining addresses or ports."""
    types = set()
    protocols = set()
    for raw_line in (sdp or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("a=candidate:"):
            continue
        parts = line.split()
        if len(parts) > 2 and parts[2].lower() in ('udp', 'tcp'):
            protocols.add(parts[2].lower())
        try:
            index = parts.index("typ")
        except ValueError:
            continue
        if index + 1 < len(parts) and parts[index + 1].lower() in ('host', 'srflx', 'prflx', 'relay'):
            types.add(parts[index + 1].lower())
    return sorted(types), sorted(protocols)


class DeviceTrack(MediaStreamTrack):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.queue = asyncio.Queue(maxsize=2 if kind == "video" else 10)

    def feed(self, frame):
        if self.readyState != "live":
            return
        while self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(frame)

    async def recv(self):
        if self.readyState != "live":
            raise MediaStreamError
        try:
            frame = await asyncio.wait_for(self.queue.get(), 15)
        except TimeoutError as exc:
            raise MediaStreamError from exc
        if frame is None:
            raise MediaStreamError
        if self.kind == "video":
            copy = av.VideoFrame.from_ndarray(
                frame.to_ndarray(format="yuv420p"), format="yuv420p"
            )
        else:
            copy = av.AudioFrame.from_ndarray(
                frame.to_ndarray(),
                format=frame.format.name,
                layout=frame.layout.name,
            )
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
    jobs: set = field(default_factory=set)
    mic_generation: int = 0
    mic_enabled: bool = False
    mic_id: int | None = None
    control_channel: object | None = None


class WebRTCManager:
    def __init__(self, hub):
        self.hub = hub
        self.viewers = {}
        self.tasks = set()
        self.offers = set()
        self.closed = False
        self._close_all_task = None
        hub.frame_listeners.add(self._frame)
        hub.close_listeners.add(self.close_all)

    def _diag(self, **values):
        self.hub.webrtc_diagnostics.update(values)
        self.hub.webrtc_diagnostics["active_viewers"] = len(self.viewers)
        self.hub.webrtc_diagnostics['viewer_states'] = [
            {'connection': v.pc.connectionState, 'ice': v.pc.iceConnectionState}
            for v in self.viewers.values()
        ]

    def _frame(self, kind, frame):
        for viewer in tuple(self.viewers.values()):
            if track := viewer.tracks.get(kind):
                track.feed(frame)

    async def offer(self, sdp, session_id, send_message, *, allow_talk=False):
        configuration, ice_diag = _ice_configuration(self.hub.hass)
        self._diag(
            stage="offer_received",
            negotiation_ok=False,
            failed_at_stage=None,
            last_exception_type=None,
            requested_tracks=[],
            created_tracks=[],
            candidate_event=None,
            ice_connection_state=None,
            ice_gathering_state=None,
            signaling_state=None,
            local_candidate_types=[],
            local_candidate_protocols=[],
            remote_candidate_types=[],
            remote_candidate_protocols=[],
            **ice_diag,
        )
        if self.closed or session_id in self.viewers or len(self.viewers) >= 4 or self.hub.stopped:
            self._diag(stage="rejected_busy")
            send_message(
                WebRTCError(
                    code="busy",
                    message="Nombre maximal de lecteurs atteint ou caméra arrêtée",
                )
            )
            return

        viewer = Viewer(RTCPeerConnection(configuration))
        self.viewers[session_id] = viewer

        def microphone_state(enabled, error=None):
            viewer.mic_enabled = enabled
            channel = viewer.control_channel
            if channel and channel.readyState == 'open':
                channel.send(json.dumps({'type': 'microphone', 'id': viewer.mic_id,
                                         'enabled': enabled, 'error': error}))

        def job(coro):
            task = asyncio.create_task(coro)
            viewer.jobs.add(task)
            def finished(task):
                viewer.jobs.discard(task)
                if not task.cancelled() and task.exception() is not None:
                    self._diag(microphone_error_type=type(task.exception()).__name__)
            task.add_done_callback(finished)

        @viewer.pc.on('track')
        def inbound(track):
            if track.kind == 'audio':
                async def consume():
                    try:
                        while self.viewers.get(session_id) is viewer:
                            frame = await track.recv()
                            if allow_talk:
                                await self.hub.talkback.feed(viewer, frame)
                    except MediaStreamError:
                        pass
                    finally:
                        if allow_talk:
                            await self.hub.talkback.stop(viewer)
                            microphone_state(False, 'Piste microphone interrompue')
                job(consume())

        @viewer.pc.on('datachannel')
        def data_channel(channel):
            if channel.label != 'welcomeeye-control' or viewer.control_channel is not None:
                return
            viewer.control_channel = channel
            @channel.on('close')
            def channel_closed():
                if allow_talk and self.viewers.get(session_id) is viewer:
                    self.hub.talkback.disable(viewer)
                    job(self.hub.talkback.stop(viewer))
            def reply(value):
                if channel.readyState == 'open':
                    channel.send(json.dumps(value))
            @channel.on('message')
            def command(raw):
                if (self.viewers.get(session_id) is not viewer or not allow_talk
                        or not isinstance(raw, str) or len(raw) > 256):
                    return
                try:
                    value = json.loads(raw)
                except (ValueError, TypeError):
                    return
                if not isinstance(value, dict):
                    return
                if value.get('type') == 'heartbeat':
                    self.hub.talkback.heartbeat(viewer)
                    return
                if value.get('type') != 'microphone' or type(value.get('enabled')) is not bool:
                    return
                if type(value.get('id')) is not int or not 0 <= value['id'] <= 2**53 - 1:
                    return
                viewer.mic_id = value['id']
                viewer.mic_generation += 1
                generation = viewer.mic_generation
                enabled = value['enabled']
                if not enabled:
                    self.hub.talkback.disable(viewer)
                async def apply():
                    if generation != viewer.mic_generation:
                        return
                    try:
                        if enabled:
                            await self.hub.talkback.start(viewer)
                            if generation != viewer.mic_generation:
                                await self.hub.talkback.stop(viewer)
                        else:
                            await self.hub.talkback.stop(viewer)
                        viewer.mic_enabled = (generation == viewer.mic_generation
                                              and self.hub.talkback.active
                                              and self.hub.talkback.owner is viewer)
                        reply({'type': 'microphone', 'id': value.get('id'),
                               'enabled': generation == viewer.mic_generation and self.hub.talkback.active
                               and self.hub.talkback.owner is viewer})
                    except Exception:
                        if generation == viewer.mic_generation:
                            viewer.mic_enabled = False
                        reply({'type': 'microphone', 'id': value.get('id'), 'enabled': False,
                               'error': 'Microphone indisponible ou format audio non pris en charge'})
                job(apply())

        if allow_talk:
            async def microphone_watchdog():
                while self.viewers.get(session_id) is viewer:
                    await asyncio.sleep(1)
                    talk = self.hub.talkback
                    if talk.owner is viewer and talk.active and time.monotonic() - talk.last_heartbeat > 3:
                        await talk.stop(viewer)
                    if viewer.mic_enabled and (talk.owner is not viewer or not talk.active):
                        await talk.stop(viewer)
                        microphone_state(False, 'Micro coupé : connexion audio interrompue')
            job(microphone_watchdog())
        self._diag(
            stage="peer_connection_created",
            connection_state=viewer.pc.connectionState,
            ice_connection_state=viewer.pc.iceConnectionState,
            ice_gathering_state=viewer.pc.iceGatheringState,
            signaling_state=viewer.pc.signalingState,
        )

        @viewer.pc.on("connectionstatechange")
        async def state_changed():
            if self.viewers.get(session_id) is not viewer:
                return
            state = viewer.pc.connectionState
            self._diag(
                connection_state=state,
                ice_connection_state=viewer.pc.iceConnectionState,
                signaling_state=viewer.pc.signalingState,
            )
            if state == "connected":
                self._diag(stage="connection_connected")
                if viewer.timeout:
                    viewer.timeout.cancel()
            elif state in ("failed", "closed"):
                self._diag(stage=f"connection_{state}")
                await self.close(session_id, expected=viewer)

        @viewer.pc.on("iceconnectionstatechange")
        async def ice_state_changed():
            if self.viewers.get(session_id) is viewer:
                self._diag(ice_connection_state=viewer.pc.iceConnectionState)

        @viewer.pc.on("icegatheringstatechange")
        async def ice_gathering_state_changed():
            if self.viewers.get(session_id) is viewer:
                self._diag(ice_gathering_state=viewer.pc.iceGatheringState)

        @viewer.pc.on("signalingstatechange")
        async def signaling_state_changed():
            if self.viewers.get(session_id) is viewer:
                self._diag(signaling_state=viewer.pc.signalingState)

        async def expire_unconnected():
            await asyncio.sleep(60)
            if self.viewers.get(session_id) is viewer and viewer.pc.connectionState != "connected":
                self._diag(
                    stage="connection_timeout",
                    connection_state=viewer.pc.connectionState,
                    ice_connection_state=viewer.pc.iceConnectionState,
                    ice_gathering_state=viewer.pc.iceGatheringState,
                    signaling_state=viewer.pc.signalingState,
                )
                await self.close(session_id, expected=viewer)

        viewer.timeout = asyncio.create_task(expire_unconnected())
        offer_task = asyncio.current_task()
        self.offers.add(offer_task)
        try:
            async with asyncio.timeout(50):
                self._diag(stage="setting_remote_description")
                await viewer.pc.setRemoteDescription(
                    RTCSessionDescription(sdp=sdp, type="offer")
                )
                if self.viewers.get(session_id) is not viewer:
                    return
                requested = [
                    f"{transceiver.kind}:{transceiver.direction}"
                    for transceiver in viewer.pc.getTransceivers()
                ]
                self._diag(
                    stage="remote_description_set",
                    requested_tracks=requested,
                    signaling_state=viewer.pc.signalingState,
                )

                for transceiver in viewer.pc.getTransceivers():
                    if (
                        transceiver.kind in ("video", "audio")
                        and transceiver.direction in ("recvonly", "sendrecv")
                    ):
                        track = DeviceTrack(transceiver.kind)
                        viewer.tracks[transceiver.kind] = track
                        viewer.pc.addTrack(track)
                created = sorted(viewer.tracks)
                self._diag(stage="tracks_created", created_tracks=created)
                if not viewer.tracks:
                    raise ValueError("Offer does not request media")
                if self.viewers.get(session_id) is not viewer:
                    return

                self._diag(stage="acquiring_media")
                await self.hub.acquire(viewer.lease)
                self._diag(stage="media_acquired")
                if self.viewers.get(session_id) is not viewer:
                    await self.hub.release(viewer.lease)
                    return

                self._diag(stage="creating_answer")
                answer = await viewer.pc.createAnswer()
                if self.viewers.get(session_id) is not viewer:
                    return
                self._diag(stage="answer_created")
                await viewer.pc.setLocalDescription(answer)
                if self.viewers.get(session_id) is not viewer:
                    return
                local_types, local_protocols = _candidate_metadata_from_sdp(
                    viewer.pc.localDescription.sdp
                )
                self._diag(
                    stage="local_description_set",
                    ice_connection_state=viewer.pc.iceConnectionState,
                    ice_gathering_state=viewer.pc.iceGatheringState,
                    signaling_state=viewer.pc.signalingState,
                    local_candidate_types=local_types,
                    local_candidate_protocols=local_protocols,
                )
                send_message(WebRTCAnswer(answer=viewer.pc.localDescription.sdp))
                self._diag(stage="answer_sent", negotiation_ok=True)
        except asyncio.CancelledError:
            self._diag(stage="cancelled")
            await self.close(session_id, expected=viewer)
            raise
        except Exception as exc:
            if self.viewers.get(session_id) is not viewer:
                return
            failed_stage = self.hub.webrtc_diagnostics.get("stage")
            self._diag(
                stage="failed",
                failed_at_stage=failed_stage,
                last_exception_type=type(exc).__name__,
                connection_state=viewer.pc.connectionState,
                ice_connection_state=viewer.pc.iceConnectionState,
                ice_gathering_state=viewer.pc.iceGatheringState,
                signaling_state=viewer.pc.signalingState,
            )
            _LOGGER.warning(
                "Cannot start WelcomeEye WebRTC (%s at %s)",
                type(exc).__name__,
                failed_stage,
            )
            await self.close(session_id, expected=viewer)
            send_message(
                WebRTCError(
                    code="stream_failed",
                    message="Impossible de démarrer la vidéo WelcomeEye",
                )
            )
        finally:
            self.offers.discard(offer_task)

    async def candidate(self, session_id, candidate):
        if not (viewer := self.viewers.get(session_id)):
            return
        if not candidate.candidate:
            self._diag(candidate_event="end_of_candidates")
            await viewer.pc.addIceCandidate(None)
            return

        ice = candidate_from_sdp(candidate.candidate.removeprefix("candidate:"))
        ice.sdpMid = candidate.sdp_mid
        ice.sdpMLineIndex = candidate.sdp_m_line_index

        remote_types = set(
            self.hub.webrtc_diagnostics.get("remote_candidate_types", [])
        )
        remote_protocols = set(
            self.hub.webrtc_diagnostics.get("remote_candidate_protocols", [])
        )
        if ice.type in ('host', 'srflx', 'prflx', 'relay'):
            remote_types.add(str(ice.type).lower())
        if ice.protocol.lower() in ('udp', 'tcp'):
            remote_protocols.add(str(ice.protocol).lower())
        self._diag(
            candidate_event="candidate_received",
            remote_candidate_types=sorted(remote_types),
            remote_candidate_protocols=sorted(remote_protocols),
        )
        await viewer.pc.addIceCandidate(ice)

    def schedule_close(self, session_id):
        viewer = self.viewers.get(session_id)
        if viewer is None:
            return
        task = asyncio.create_task(self.close(session_id, expected=viewer))
        self.tasks.add(task)
        task.add_done_callback(self._cleanup_done)

    def _cleanup_done(self, task):
        self.tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            self._diag(cleanup_error_type=type(error).__name__)
            _LOGGER.warning('WelcomeEye WebRTC cleanup failed (%s)', type(error).__name__)

    async def close(self, session_id, *, expected=None):
        if expected is not None and self.viewers.get(session_id) is not expected:
            return
        if not (viewer := self.viewers.pop(session_id, None)):
            self._diag()
            return
        caller = asyncio.current_task()
        task = asyncio.create_task(self._close_viewer(viewer, caller))
        self.tasks.add(task)
        task.add_done_callback(self._cleanup_done)
        # Cancellation of an HA callback must not abandon lease/PC cleanup.
        await asyncio.shield(task)

    async def _close_viewer(self, viewer, caller):
        self.hub.talkback.disable(viewer)
        for task in tuple(viewer.jobs):
            task.cancel()
        if viewer.jobs:
            await asyncio.gather(*tuple(viewer.jobs), return_exceptions=True)
        await self.hub.talkback.stop(viewer)
        if viewer.timeout and viewer.timeout is not caller:
            viewer.timeout.cancel()
            await asyncio.gather(viewer.timeout, return_exceptions=True)
        for track in viewer.tracks.values():
            track.stop()
        try:
            await self.hub.release(viewer.lease, reason='webrtc_release')
        finally:
            state = viewer.pc.connectionState
            ice_state = viewer.pc.iceConnectionState
            gathering_state = viewer.pc.iceGatheringState
            signaling_state = viewer.pc.signalingState
            await asyncio.wait_for(viewer.pc.close(), 10)
            self._diag(
                connection_state=state,
                ice_connection_state=ice_state,
                ice_gathering_state=gathering_state,
                signaling_state=signaling_state,
            )

    async def close_all(self):
        if self._close_all_task is None:
            self._close_all_task = asyncio.create_task(self._close_all())
        await asyncio.shield(self._close_all_task)

    async def _close_all(self):
        self.closed = True
        self.hub.frame_listeners.discard(self._frame)
        self.hub.close_listeners.discard(self.close_all)
        offers = tuple(t for t in self.offers if t is not asyncio.current_task())
        for task in offers:
            task.cancel()
        if offers:
            await asyncio.gather(*offers, return_exceptions=True)
        results = await asyncio.gather(
            *(self.close(key) for key in tuple(self.viewers)), return_exceptions=True
        )
        if self.tasks:
            results.extend(await asyncio.gather(*tuple(self.tasks), return_exceptions=True))
        errors = [r for r in results if isinstance(r, Exception)]
        if errors:
            raise ExceptionGroup('WebRTC cleanup failed', errors)
