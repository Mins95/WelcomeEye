"""V1 output requests on the live media channel, as in QvLtPlayerCore.

The media worker remains the only socket reader/writer. There is one pending
request, never a retry, and no output command survives its original session.
"""
import asyncio
from dataclasses import dataclass
import threading
import time

from .protected import ProtocolError, build_unlock_request, decode_unlock_reply
from .protocol import encode_password

RESPONSE_TIMEOUT = 10.0

@dataclass
class PendingOutput:
    session: object
    packet: bytes
    future: asyncio.Future
    deadline: float
    state: str = 'queued'


class V1MediaOutput:
    def __init__(self, controller):
        self.controller = controller
        self.lock = threading.Lock()
        self.pending = None
        self.uncertain_session = None
        self.stage = 'idle'

    def _resolve(self, request, *, body=None, error=None):
        def deliver():
            if request.future.done():
                return
            if error is not None:
                request.future.set_exception(error)
            else:
                request.future.set_result(body)
        request.future.get_loop().call_soon_threadsafe(deliver)

    async def execute(self, output):
        """Borrow the existing hub lifecycle; release our lease on every path."""
        control = self.controller
        hub = control.hub
        lease = object()
        request = None
        self.stage = 'acquiring_v1_media'
        try:
            await hub.acquire(lease)
            session = hub.session
            self.stage = 'validating_v1_media'
            if control.closed.is_set() or session is None or session.closed.is_set():
                raise ProtocolError('Session média V1 indisponible')
            if session.info.uid != control.entry.unique_id:
                raise ProtocolError('Le visiophone ne correspond pas à la configuration')
            if (session.channel, session.stream, session.mode) != (16, 1, 2):
                raise ProtocolError('Profil média V1 incompatible avec le chemin natif')
            self.stage = 'building_request'
            packet = build_unlock_request(
                session.info.uid, session.encryption_profile, session.device_now(),
                encode_password(control.entry.data['password']), output,
            )
            request = PendingOutput(session, packet, asyncio.get_running_loop().create_future(),
                                    time.monotonic() + RESPONSE_TIMEOUT)
            with self.lock:
                if control.closed.is_set():
                    raise ProtocolError('Intégration arrêtée')
                if self.uncertain_session is session:
                    raise ProtocolError('Confirmation précédente incertaine : fermer puis rouvrir la vidéo')
                if self.pending is not None:
                    raise ProtocolError('Une commande V1 est déjà en cours')
                self.pending = request
                self.stage = 'queued_on_v1_media'
            body = await asyncio.wait_for(asyncio.shield(request.future), RESPONSE_TIMEOUT)
            self.stage = 'decoding_confirmation'
            try:
                _, result, reason = decode_unlock_reply(session.info.uid, body)
            except Exception:
                control.decode_failures += 1
                with self.lock:
                    self.uncertain_session = session
                raise
            control.last_result, control.last_reason = result, reason
            if result != 1:
                raise ProtocolError(f'Ouverture refusée (code {result}, motif {reason})')
            self.stage = 'acknowledged_not_physically_verified'
        except BaseException:
            if request is not None:
                with self.lock:
                    if request.state == 'sent':
                        self.uncertain_session = request.session
            raise
        finally:
            with self.lock:
                if request is not None:
                    request.packet = b''
                    if self.pending is request:
                        self.pending = None
                    request.future.cancel()
                    if request.future.done() and not request.future.cancelled():
                        request.future.exception()
            # This is the normal media release: a viewer's lease keeps video
            # running; without viewers the existing Stop AV cleanup runs.
            try:
                await hub.release(lease)
            except Exception as exc:
                control.cleanup_error_type = type(exc).__name__
                raise

    def observe(self, session, parts):
        """Called only by the media reader, before a queued request is sent."""
        with self.lock:
            request = self.pending
            if request is None or request.session is not session or request.state != 'sent':
                return
            if time.monotonic() >= request.deadline:
                request.state = 'expired'
                self.uncertain_session = session
                self._resolve(request, error=TimeoutError('Confirmation V1 arrivée trop tard'))
                return
            for kind, body in parts:
                counts = self.controller.tlv_counts
                counts[kind] = counts.get(kind, 0) + 1
                if kind == 506:
                    self.controller.response_count += 1
                    request.state = 'answered'
                    self._resolve(request, body=body)
                    return

    def send_pending(self, session):
        """One media-worker send, after processing the previous receive batch."""
        with self.lock:
            request = self.pending
            if request is None or request.session is not session or request.state != 'queued':
                return
            if (self.controller.closed.is_set() or session.closed.is_set()
                    or time.monotonic() >= request.deadline):
                request.state = 'cancelled'
                self._resolve(request, error=TimeoutError('Commande V1 annulée avant envoi'))
                return
            if session.info.uid != self.controller.entry.unique_id:
                request.state = 'cancelled'
                self._resolve(request, error=ProtocolError('Le visiophone ne correspond pas à la configuration'))
                return
            # Claiming marks the start of the only permitted attempt. An
            # in-progress send cannot be recalled or repeated after cancellation.
            request.state = 'sent'
            packet, request.packet = request.packet, b''
            self.stage = 'sending_request'
            self.controller.last_command = time.monotonic()
            self.controller.request_send_attempt_count += 1
        try:
            session.sock.sendall(packet)
        except Exception as exc:
            with self.lock:
                self.uncertain_session = session
                request.state = 'send_failed'
            self._resolve(request, error=exc)
            return
        self.controller.request_sent_count += 1
        self.stage = 'waiting_confirmation'

    def media_closed(self, session):
        with self.lock:
            if self.uncertain_session is session:
                self.uncertain_session = None
            request = self.pending
            if request is not None and request.session is session and request.state in ('queued', 'sent'):
                request.state = 'session_closed'
                self._resolve(request, error=ConnectionError('Session média V1 fermée pendant la commande'))

    def close(self):
        with self.lock:
            request = self.pending
            if request is not None:
                if request.state == 'sent':
                    self.uncertain_session = request.session
                request.state = 'cancelled'
                request.packet = b''
                self._resolve(request, error=ProtocolError('Intégration arrêtée'))
