"""WelcomeEye output control with automatic temporary video-session warmup."""
import threading
import time
import sys

from .client import Session
from .protected import ProtocolError, build_unlock_request, decode_unlock_reply
from .protocol import encode_password

V1_CONTROL_SETTLE_SECONDS = 1.0


class DeviceController:
    def __init__(self, hub):
        self.hub = hub
        self.entry = hub.entry
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.session = None
        self.last_command = float("-inf")
        self.command_count = 0
        self.request_sent_count = 0
        self.response_count = 0
        self.decode_failures = 0
        self.last_output = None
        self.last_result = None
        self.last_reason = None
        self.last_error_type = None
        self.last_error_message = None
        self.last_error_stage = None
        self.tlv_counts = {}
        self.framing_diagnostics = {}
        self.ring_pause_requested = 0
        self.ring_pause_success = 0
        self.ring_pause_timeout = 0
        self.ring_resume_requested = 0
        self.ring_resume_success = 0
        self.command_started_after_ring_release = False
        self.request_send_attempt_count = 0
        self.cleanup_error_type = None
        self.v1_settle_wait_count = 0
        self.v1_settle_requested_ms = 0
        self.v1_settle_elapsed_ms = 0

    def _finish_v1_control(self, session, pause_requested):
        """Always release the command before resuming; preserve uncertain closes."""
        error = None
        released = True
        try:
            if session is not None:
                self.framing_diagnostics = session.framing_diagnostics()
        except Exception as exc:
            self.cleanup_error_type = type(exc).__name__
        try:
            if self.session is not None:
                self.session.close()
                self.session = None
        except Exception as exc:
            error = exc
            released = False
            self.cleanup_error_type = type(exc).__name__
        finally:
            if pause_requested:
                self.ring_resume_requested += 1
                try:
                    if released:
                        time.sleep(0.2)
                    if self.hub.ring_listener.resume_after_control(control_released=released):
                        self.ring_resume_success += 1
                except Exception as exc:
                    self.cleanup_error_type = type(exc).__name__
                    error = error or exc
        return error

    def _record_error(self, exc, stage):
        self.last_error_type = type(exc).__name__
        if isinstance(exc, (ProtocolError, ValueError, RuntimeError, TimeoutError)):
            self.last_error_message = str(exc)
        else:
            self.last_error_message = None
        self.last_error_stage = stage

    def _clear_error(self):
        self.last_error_type = None
        self.last_error_message = None
        self.last_error_stage = None

    def _control_session(self, data):
        session = Session(
            data["host"],
            data["username"],
            data["password"],
            channel=0,
            stream=3,
            mode=0,
        )
        self.session = session
        session.connect()
        return session

    def _video_session(self, data):
        session = Session(
            data["host"],
            data["username"],
            data["password"],
            channel=16,
            stream=1,
            mode=2,
        )
        self.session = session

        parts = session.connect()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for kind, _body in parts:
                if kind == 203:
                    return session
            parts = session.read()
        raise TimeoutError("WelcomeEye video session did not initialize")

    def _wait_v1_control_settle(self):
        """Give real V1 hardware time to release its single control slot.

        This is a bounded pre-session delay, not a retry. No physical output
        packet can be built or sent before it completes.
        """
        self.v1_settle_wait_count += 1
        self.v1_settle_requested_ms = int(V1_CONTROL_SETTLE_SECONDS * 1000)
        started = time.monotonic()
        time.sleep(V1_CONTROL_SETTLE_SECONDS)
        self.v1_settle_elapsed_ms = max(0, int((time.monotonic() - started) * 1000))

    def unlock(self, output):
        if output not in (0, 1) or isinstance(output, bool):
            raise ValueError("Invalid output")
        if not self.lock.acquire(blocking=False):
            raise ProtocolError("Une commande est déjà en cours")

        session = None
        ring_pause_requested = False
        is_v1 = getattr(self.hub, "device_model", None) == "WelcomeEye Connect V1"
        stage = "preparing"
        self.command_count += 1
        self.last_output = output
        self.last_result = None
        self.last_reason = None
        self.command_started_after_ring_release = False
        self.cleanup_error_type = None
        self.v1_settle_requested_ms = 0
        self.v1_settle_elapsed_ms = 0
        self._clear_error()

        try:
            if self.closed.is_set():
                raise ProtocolError("Intégration arrêtée")
            if time.monotonic() - self.last_command < 3:
                raise ProtocolError("Attendre trois secondes avant une nouvelle commande")

            data = self.entry.data
            if is_v1 and self.session is not None:
                raise ProtocolError("La session de commande précédente n'est pas libérée")

            if is_v1:
                stage = "pausing_doorbell"
                ring_pause_requested = True
                self.ring_pause_requested += 1
                timeouts_before = self.hub.ring_listener.control_pause_timeout_count
                if not self.hub.ring_listener.pause_for_control(timeout=5):
                    self.ring_pause_timeout += (
                        self.hub.ring_listener.control_pause_timeout_count - timeouts_before
                    )
                    raise TimeoutError("Impossible de libérer le canal de contrôle WelcomeEye")
                self.ring_pause_success += 1
                if self.closed.is_set() or not self.hub.ring_listener.released_for_control:
                    raise ProtocolError("Canal de contrôle indisponible ou intégration arrêtée")
                self.command_started_after_ring_release = True

                stage = "settling_control_slot"
                self._wait_v1_control_settle()
                if self.closed.is_set() or not self.hub.ring_listener.released_for_control:
                    raise ProtocolError("Canal de contrôle indisponible ou intégration arrêtée")

            stage = "opening_session"
            if is_v1:
                session = self._control_session(data)
            elif self.hub.connected:
                session = self._control_session(data)
            else:
                try:
                    session = self._video_session(data)
                except Exception:
                    if self.session:
                        self.session.close()
                        self.session = None
                    session = self._control_session(data)

            if session.info.uid != self.entry.unique_id:
                raise ProtocolError("Le visiophone ne correspond pas à la configuration")

            stage = "building_request"
            packet = build_unlock_request(
                session.info.uid,
                session.encryption_profile,
                session.device_now(),
                encode_password(data["password"]),
                output,
            )

            session.sock.settimeout(5)
            if is_v1 and self.closed.is_set():
                raise ProtocolError("Intégration arrêtée")
            self.last_command = time.monotonic()
            stage = "sending_request"
            self.request_send_attempt_count += 1
            session.sock.sendall(packet)
            self.request_sent_count += 1

            stage = "waiting_confirmation"
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                for kind, body in session.read():
                    self.tlv_counts[kind] = self.tlv_counts.get(kind, 0) + 1
                    if kind != 506:
                        continue
                    self.response_count += 1
                    stage = "decoding_confirmation"
                    try:
                        _, result, reason = decode_unlock_reply(session.info.uid, body)
                    except Exception:
                        self.decode_failures += 1
                        raise
                    self.last_result = result
                    self.last_reason = reason
                    if result != 1:
                        raise ProtocolError(
                            f"Ouverture refusée (code {result}, motif {reason})"
                        )
                    stage = "confirmed"
                    self._clear_error()
                    return

            raise TimeoutError("Aucune confirmation du visiophone")
        except Exception as exc:
            self._record_error(exc, stage)
            raise
        finally:
            if is_v1:
                exception_active = sys.exc_info()[0] is not None
                try:
                    cleanup_error = self._finish_v1_control(session, ring_pause_requested)
                finally:
                    self.lock.release()
                if cleanup_error is not None and not exception_active:
                    self._record_error(cleanup_error, 'closing_session')
                    raise cleanup_error
            else:
                if session is not None:
                    self.framing_diagnostics = session.framing_diagnostics()
                if self.session:
                    self.session.close()
                    self.session = None
                self.lock.release()

    def close(self):
        self.closed.set()
        session = self.session
        if session:
            session.close()
