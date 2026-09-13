"""WelcomeEye output control with automatic temporary video-session warmup."""
import threading
import time

from .client import Session
from .protected import ProtocolError, build_unlock_request, decode_unlock_reply
from .protocol import encode_password


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

    def unlock(self, output):
        if output not in (0, 1) or isinstance(output, bool):
            raise ValueError("Invalid output")
        if not self.lock.acquire(blocking=False):
            raise ProtocolError("Une commande est déjà en cours")

        session = None
        ring_paused = False
        stage = "preparing"
        self.command_count += 1
        self.last_output = output
        self.last_result = None
        self.last_reason = None
        self._clear_error()

        try:
            if self.closed.is_set():
                raise ProtocolError("Intégration arrêtée")
            if time.monotonic() - self.last_command < 3:
                raise ProtocolError("Attendre trois secondes avant une nouvelle commande")

            data = self.entry.data

            # The Connect V1 appears to expose a single usable 0/3/0 control slot.
            # Its persistent doorbell listener therefore yields that slot briefly
            # before an explicit user-triggered output command. The command itself
            # is still sent exactly once and is never automatically retried.
            if getattr(self.hub, "device_model", None) == "WelcomeEye Connect V1":
                stage = "pausing_doorbell"
                if not self.hub.ring_listener.pause_for_control(timeout=5):
                    raise TimeoutError("Impossible de libérer le canal de contrôle WelcomeEye")
                ring_paused = True
                time.sleep(0.2)

            stage = "opening_session"

            # V1 control is already known to work on the dedicated 0/3/0
            # channel. Avoid delaying the physical command behind a media
            # warmup which is unrelated to output control on that model.
            if getattr(self.hub, "device_model", None) == "WelcomeEye Connect V1":
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
            self.last_command = time.monotonic()
            stage = "sending_request"
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
            if session is not None:
                self.framing_diagnostics = session.framing_diagnostics()
            if self.session:
                self.session.close()
                self.session = None
            if ring_paused:
                # Ensure the one-shot control TCP session is gone before allowing
                # the persistent ring listener to reclaim the V1 control channel.
                time.sleep(0.2)
                self.hub.ring_listener.resume_after_control()
            self.lock.release()

    def close(self):
        self.closed.set()
        session = self.session
        if session:
            session.close()
