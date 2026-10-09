"""WelcomeEye output control with automatic temporary video-session warmup."""
import threading
import time
import asyncio
import logging

from .client import Session
from .capabilities import DeviceVariant
from .protected import ProtocolError, build_unlock_request, decode_unlock_reply
from .protocol import encode_password
from .v1_control import V1MediaOutput

_LOGGER = logging.getLogger(__name__)

# LT chooses the panel with liveChannel metadata (channel + 15), then sends
# output - 1 on that same channel. QvLtPlayerCore.startPlaying/unlock, APK
# 6.1.58.24; these are LT routes, not Connect 3 transparent commands.
OUTPUT_TARGETS = {
    'strike_1': (1, 0), 'gate_1': (1, 1),
    'strike_2': (2, 0), 'gate_2': (2, 1),
}
SECONDARY_OUTPUT_OPTIONS = {
    'strike_2': 'channel2_strike_trial_enabled',
    'gate_2': 'channel2_gate_trial_enabled',
}
_TARGET_CONFIGURATION_KEYS = ('host', 'username', 'password', 'device_variant',
    'protocol_family', 'channel', 'second_channel_enabled',
    'channel2_strike_trial_enabled', 'channel2_gate_trial_enabled')


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
        self._target_task = self._target_lease = self._target_session = None
        self._active_target = self._target_configuration = None
        self._targets = {target: dict(command_count=0, request_send_attempt_count=0,
            request_sent_count=0, response_count=0, last_result=None,
            last_error_type=None, last_error_stage=None,
            physical_request_uncertain=False, physical_activation_verified=False,
            native_ack_received=False, native_ack_accepted=False)
            for target in OUTPUT_TARGETS}

    def target_status(self, target):
        """Describe explicit LT routing policy without opening a connection."""
        route = OUTPUT_TARGETS.get(target) if isinstance(target, str) else None
        if route is None:
            return dict(enabled=False, reason='invalid_output_target', channel=None, output=None)
        channel, output = route
        reason = None
        if channel == 2:
            data = self.entry.data
            if (self.hub.variant not in (DeviceVariant.V1, DeviceVariant.R001)
                    or data.get('second_channel_enabled') is not True
                    or getattr(self.hub, 'channel2', None) is None):
                reason = 'output_channel_not_supported'
            elif data.get(SECONDARY_OUTPUT_OPTIONS[target]) is not True:
                reason = 'channel2_output_trial_disabled'
        return dict(enabled=reason is None, reason=reason, channel=channel, output=output + 1)

    def target_enabled(self, target):
        return self.target_status(target)['enabled']

    def diagnostics(self):
        return {'targets': {target: {**observation, **self.target_status(target)}
                           for target, observation in self._targets.items()}}

    def owns_media_acquisition(self, consumer):
        """The exact action lease may acquire channel 2 while holding our lock."""
        return (consumer is self._target_lease and self._target_lease is not None
                and self._target_task is asyncio.current_task()
                and self.lock.locked() and self._active_target in SECONDARY_OUTPUT_OPTIONS)

    def authorize_target_session(self, target, session):
        """Recheck the immutable action route immediately before a worker send."""
        return (target in SECONDARY_OUTPUT_OPTIONS and target == self._active_target
                and self._target_session is session and self.hub.session is session
                and self.lock.locked() and not self.closed.is_set() and not self.hub.stopped
                and not session.closed.is_set() and self.hub._active_media_channel == 2
                and (session.channel, session.stream, session.mode) == (17, 1, 2)
                and session.info.uid == self.entry.unique_id and self.target_enabled(target)
                and self._target_configuration == tuple(
                    self.entry.data.get(key) for key in _TARGET_CONFIGURATION_KEYS))

    async def unlock_target(self, target):
        """One named action; secondary outputs use only the selected LT session."""
        route = self.target_status(target)
        if not route['enabled']:
            raise ProtocolError(route['reason'])
        channel, output = OUTPUT_TARGETS[target]
        if channel == 1:
            return await self.hub.hass.async_add_executor_job(self.unlock_for_ha, output)
        if not self.lock.acquire(blocking=False):
            raise ProtocolError('Une commande est déjà en cours')
        self._target_task = asyncio.current_task()
        self._target_lease = object()
        self._active_target = target
        self._target_configuration = tuple(self.entry.data.get(key) for key in _TARGET_CONFIGURATION_KEYS)
        obs = self._targets[target]
        before = (self.request_send_attempt_count, self.request_sent_count, self.response_count)
        self.command_count += 1
        self.last_output, self.last_result, self.last_reason = output, None, None
        self.physical_result_uncertain = False
        self.cleanup_error_type = None
        self._clear_error()
        obs['command_count'] += 1
        obs.update(last_result=None, last_error_type=None, last_error_stage=None,
            physical_request_uncertain=False, native_ack_received=False, native_ack_accepted=False)
        self.native_control_path = 'legacy_live_channel_17_1_2'
        try:
            if self.closed.is_set() or self.hub.stopped:
                raise ProtocolError('Intégration arrêtée')
            if time.monotonic() - self.last_command < 3:
                raise ProtocolError('Attendre trois secondes avant une nouvelle commande')
            await self.v1_media.execute(output, channel=2, target=target)
        except BaseException as exc:
            self.physical_result_uncertain = (
                self.request_send_attempt_count > before[0] and self.last_result is None)
            exc.physical_request_uncertain = self.physical_result_uncertain
            self._record_error(exc, self.v1_media.stage)
            raise
        finally:
            obs.update(last_result=self.last_result, last_error_type=self.last_error_type,
                last_error_stage=self.last_error_stage,
                physical_request_uncertain=self.physical_result_uncertain,
                native_ack_received=self.last_result is not None,
                native_ack_accepted=self.last_result == 1)
            for key, value, previous in zip(('request_send_attempt_count', 'request_sent_count', 'response_count'),
                    (self.request_send_attempt_count, self.request_sent_count, self.response_count), before):
                obs[key] += value - previous
            self._target_task = self._target_lease = self._target_session = None
            self._active_target = self._target_configuration = None
            self.lock.release()

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
        if self.closed.is_set():
            session.close()
            raise ProtocolError("Intégration arrêtée")
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
        if self.closed.is_set():
            session.close()
            raise ProtocolError("Intégration arrêtée")

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
        is_v1 = self.hub.variant == DeviceVariant.V1
        stage = "preparing"
        self.command_count += 1
        self.last_output = output
        self.last_result = None
        self.last_reason = None
        self._clear_error()
        self.cleanup_error_type = None
        attempts_before = self.request_send_attempt_count
        sent_before, responses_before = self.request_sent_count, self.response_count
        target_obs = self._targets['strike_1' if output == 0 else 'gate_1']
        target_obs['command_count'] += 1
        self.physical_result_uncertain = False

        try:
            if self.closed.is_set():
                raise ProtocolError("Intégration arrêtée")
            if getattr(self.hub, '_active_media_channel', 1) != 1:
                raise ProtocolError("Fermez la seconde platine avant une commande")
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
                    if self.closed.is_set():
                        raise
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
            if self.closed.is_set():
                raise ProtocolError("Intégration arrêtée")
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
            target_obs.update(last_result=self.last_result, last_error_type=self.last_error_type,
                last_error_stage=self.last_error_stage,
                physical_request_uncertain=self.physical_result_uncertain,
                native_ack_received=self.last_result is not None,
                native_ack_accepted=self.last_result == 1)
            target_obs['request_send_attempt_count'] += self.request_send_attempt_count - attempts_before
            target_obs['request_sent_count'] += self.request_sent_count - sent_before
            target_obs['response_count'] += self.response_count - responses_before
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
        if self._target_task is not None:
            self.hub.loop.call_soon_threadsafe(self._target_task.cancel)
        if self._v1_future is not None:
            self._v1_future.cancel()
        session = self.session
        if session:
            session.close()
