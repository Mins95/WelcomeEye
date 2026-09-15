"""WelcomeEye output control with automatic temporary video-session warmup."""
import threading
import time
import asyncio
import logging

from .client import Session
from .protected import ProtocolError, build_unlock_request, decode_unlock_reply
from .protocol import encode_password
from .v1_control import V1MediaOutput

_LOGGER = logging.getLogger(__name__)


class ControlFailure(RuntimeError):
    """Safe HA boundary error, preserved by asyncio's executor conversion."""

    def __init__(self, error_type, uncertain):
        super().__init__(error_type)
        self.physical_request_uncertain = uncertain


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
        self.request_send_attempt_count = 0
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
        self.v1_media = V1MediaOutput(self)
        self.native_control_path = None
        self.cleanup_error_type = None
        self._v1_future = None
        self.physical_result_uncertain = False

    def _record_error(self, exc, stage):
        self.last_error_type = type(exc).__name__
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

    def unlock_for_ha(self, output):
        try:
            self.unlock(output)
        except Exception as exc:
            # asyncio.wrap_future reconstructs TimeoutError and drops custom
            # attributes. Wrap on the executor side, before crossing to HA.
            raise ControlFailure(type(exc).__name__, getattr(exc, 'physical_request_uncertain', False)) from exc

    def unlock(self, output):
        if output not in (0, 1) or isinstance(output, bool):
            raise ValueError("Invalid output")
        if not self.lock.acquire(blocking=False):
            raise ProtocolError("Une commande est déjà en cours")

        session = None
        is_v1 = getattr(self.hub, 'device_model', None) == 'WelcomeEye Connect V1'
        stage = "preparing"
        self.command_count += 1
        self.last_output = output
        self.last_result = None
        self.last_reason = None
        self._clear_error()
        self.cleanup_error_type = None
        attempts_before = self.request_send_attempt_count
        self.physical_result_uncertain = False

        try:
            if self.closed.is_set():
                raise ProtocolError("Intégration arrêtée")
            if time.monotonic() - self.last_command < 3:
                raise ProtocolError("Attendre trois secondes avant une nouvelle commande")

            data = self.entry.data
            stage = "opening_session"

            if is_v1:
                self.native_control_path = 'v1_live_channel_16_1_2'
                future = asyncio.run_coroutine_threadsafe(self.v1_media.execute(output), self.hub.loop)
                self._v1_future = future
                try:
                    future.result(timeout=80)
                finally:
                    if not future.done():
                        future.cancel()
                    self._v1_future = None
                self._clear_error()
                return

            self.native_control_path = 'connect2_baseline'
            if self.hub.connected:
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
            self.request_send_attempt_count += 1
            session.send_packet(packet)
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
            self.physical_result_uncertain = (
                self.request_send_attempt_count > attempts_before and self.last_result is None
            )
            # Attach to this failure, not global state potentially replaced by
            # a subsequent click before the HA executor callback resumes.
            exc.physical_request_uncertain = self.physical_result_uncertain
            self._record_error(exc, self.v1_media.stage if is_v1 and stage == 'opening_session' else stage)
            if self.physical_result_uncertain:
                lifecycle = getattr(self.hub, 'lifecycle', {})
                _LOGGER.warning(
                    'WelcomeEye output uncertain: stage=%s physical_request_attempted=true '
                    'confirmation_valid=false worker_exit_reason=%s; verify on site before retrying',
                    self.last_error_stage, lifecycle.get('worker_exit_reason'),
                )
            if is_v1:
                # Never propagate network/library messages containing endpoints.
                self.last_error_message = None
            raise
        finally:
            try:
                if session is not None:
                    self.framing_diagnostics = session.framing_diagnostics()
            finally:
                try:
                    if self.session:
                        self.session.close()
                        self.session = None
                finally:
                    self.lock.release()

    def close(self):
        self.closed.set()
        self.v1_media.close()
        if self._v1_future is not None:
            self._v1_future.cancel()
        session = self.session
        if session:
            session.close()
