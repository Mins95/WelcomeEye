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

        try:
            if self.closed.is_set():
                raise ProtocolError("Intégration arrêtée")
            if time.monotonic() - self.last_command < 3:
                raise ProtocolError("Attendre trois secondes avant une nouvelle commande")

            data = self.entry.data

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

            packet = build_unlock_request(
                session.info.uid,
                session.encryption_profile,
                session.device_now(),
                encode_password(data["password"]),
                output,
            )

            session.sock.settimeout(5)
            self.last_command = time.monotonic()
            session.sock.sendall(packet)

            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                for kind, body in session.read():
                    if kind != 506:
                        continue
                    _, result, reason = decode_unlock_reply(session.info.uid, body)
                    if result != 1:
                        raise ProtocolError(
                            f"Ouverture refusée (code {result}, motif {reason})"
                        )
                    return

            raise TimeoutError("Aucune confirmation du visiophone")
        finally:
            if self.session:
                self.session.close()
                self.session = None
            self.lock.release()

    def close(self):
        self.closed.set()
        session = self.session
        if session:
            session.close()
