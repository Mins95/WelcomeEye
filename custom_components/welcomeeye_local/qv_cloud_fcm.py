"""Explicit QV Firebase profiles over the existing bounded FCM transport.

Public resources.arsc configuration from com.extel.philipsdoorconnect
1.0.123.3(2) and com.extel.philipswelcomeeye 6.1.58.24. Both use tdkvdp,
but their Android application IDs differ. These public app IDs do not prove
Google accepts independent web registration or real hardware ring delivery.
"""
from collections import deque
from copy import deepcopy

from .qv_cloud_events import (
    PAYLOAD_BRANCHES, PAYLOAD_REJECTION_SUBREASONS, REQUIRED_FIELDS,
    inspect_qv_notification_data,
)
from .qv_cloud_structure import summarize_payload_structure
from .v1_cloud_fcm import DEFAULT_PROFILE, FCMError, FCMReceiver, FirebaseAppProfile


REJECTION_REASONS = frozenset((
    'sender_mismatch', 'subtype_mismatch', 'deleted_notice', 'qv_payload_rejected',
    'malformed_payload', 'unsupported_encoding', 'callback_failed',
    'category_mismatch', 'unexpected_transport',
))


DOOR_CONNECT_PROFILE = FirebaseAppProfile(
    project_id='tdkvdp',
    app_id='1:1004874260689:android:8c49e83613eafdc495e5f0',
    api_key='AIzaSyDJi1uweWGBp_094ci3wMc7Kj2KH1SNWgE',
    sender_id='1004874260689',
    bundle_id='welcomeeye.homeassistant.connect3',
)
WELCOMEEYE_QV_PROFILE = FirebaseAppProfile(
    project_id=DEFAULT_PROFILE.project_id,
    app_id=DEFAULT_PROFILE.app_id,
    api_key=DEFAULT_PROFILE.api_key,
    sender_id=DEFAULT_PROFILE.sender_id,
    bundle_id='welcomeeye.homeassistant.r002',
)


class QVFCMReceiver(FCMReceiver):
    """Same callback/lifecycle as V1, with an explicit model and QV data parser."""

    def __init__(self, session, on_notification, on_credentials, credentials=None,
                 persistent_ids=None, on_state=None, *, variant='connect3'):
        profiles = {'connect3': DOOR_CONNECT_PROFILE,
                    'connect2_r002': WELCOMEEYE_QV_PROFILE}
        if variant not in profiles:
            raise ValueError('unsupported_qv_variant')
        super().__init__(session, on_notification, on_credentials, credentials,
                         persistent_ids, on_state, profile=profiles[variant])
        self._receive_stage = 'not_started'
        self._receive_failure_stage = None
        self._last_message_disposition = 'unobserved'
        self._last_rejection_reason = None
        self._rejection_counts = dict.fromkeys(sorted(REJECTION_REASONS), 0)
        self._payload_rejection = None
        self._payload_structure = None
        self._payload_shapes = {}
        self._payload_wire = None
        self._recent_payload_rejections = deque(maxlen=3)
        self._last_payload_subreason = None
        self._last_payload_branch = None
        self._last_payload_missing_fields = ()
        self._payload_rejection_counts = dict.fromkeys(sorted(PAYLOAD_REJECTION_SUBREASONS), 0)
        self._payload_branch_counts = dict.fromkeys(sorted(PAYLOAD_BRANCHES), 0)
        self._payload_missing_field_counts = dict.fromkeys(sorted(REQUIRED_FIELDS), 0)

    def diagnostics(self):
        return {**super().diagnostics(), 'receive_stage': self._receive_stage,
            'receiver_ready': self.connected,
            'receive_failure_stage': self._receive_failure_stage,
            'last_message_disposition': self._last_message_disposition,
            'last_rejection_reason': self._last_rejection_reason,
            'rejection_counts': dict(self._rejection_counts),
            'last_payload_subreason': self._last_payload_subreason,
            'last_payload_branch': self._last_payload_branch,
            'last_payload_missing_fields': list(self._last_payload_missing_fields),
            'payload_rejection_counts': dict(self._payload_rejection_counts),
            'payload_branch_counts': dict(self._payload_branch_counts),
            'payload_missing_field_counts': dict(self._payload_missing_field_counts),
            'payload_structure_version': 1,
            'last_payload_wire': deepcopy(self._payload_wire),
            'last_payload_shapes': deepcopy(self._payload_shapes),
            'recent_payload_rejections': deepcopy(list(self._recent_payload_rejections))}

    async def _tls_context(self):
        # The same context helper serves HTTP enrollment. Only an active MCS
        # receive attempt changes this phase; token creation is not readiness.
        if self._run_task is not None:
            self._receive_stage = 'tls_connect'
        return await super()._tls_context()

    async def _send(self, tag, message, *, first=False):
        if tag == 2 and first:
            self._receive_stage = 'mcs_login'
        return await super()._send(tag, message, first=first)

    async def _state(self, connected):
        if connected:
            self._receive_stage = 'receiving'
            self._receive_failure_stage = None
        elif self._diag['last_error'] is not None:
            if self._receive_stage not in ('failed', 'closed'):
                self._receive_failure_stage = self._receive_stage
            self._receive_stage = 'closed' if self._closed else 'failed'
        else:
            self._receive_stage = 'closed' if self._closed else 'disconnected'
        # Publish the phase before the existing state callback runs.
        await super()._state(connected)

    async def run(self):
        if self._closed or self._run_task or self._register_task:
            return await super().run()
        self._receive_stage = 'validating'
        self._receive_failure_stage = None
        return await super().run()

    async def close(self):
        self._receive_stage = 'closed'
        return await super().close()

    def _message_target_error(self, message):
        """Transport-specific destination checks, before decoding any payload."""
        return None

    def _payload(self, message):
        self._payload_rejection = None
        self._payload_structure = None
        self._payload_shapes = {}
        self._payload_wire = {
            'transport': 'encrypted_web' if message.raw_data else 'android_data',
            'app_data_fields': min(len(message.app_data), 65),
            'app_data_limit_exceeded': len(message.app_data) > 64,
            'raw_data_present': bool(message.raw_data),
        }
        target_error = self._message_target_error(message)
        if target_error is not None:
            self._diag.update(timestamp_source='none', timestamp_status='unobserved',
                mcs_sent_present=message.HasField('sent'),
                mcs_sent_digits=len(str(abs(message.sent))) if message.HasField('sent') else 0,
                google_sent_time_present=False, google_sent_time_valid=False)
            self._payload_rejection = (target_error if target_error in REJECTION_REASONS
                                       else 'malformed_payload')
            return None
        try:
            decoded = super()._payload(message)
        except FCMError as exc:
            self._payload_rejection = ('unsupported_encoding' if exc.reason == 'unsupported_encoding'
                                       else 'malformed_payload')
            raise
        except Exception:
            self._payload_rejection = 'malformed_payload'
            raise
        if decoded is None and self._payload_rejection is None:
            # Classify only checks actually applied by the unchanged parser.
            # Its early sender check precedes its app_data bounds check.
            if not message.raw_data and getattr(message, 'from') != self._profile.sender_id:
                self._payload_rejection = 'sender_mismatch'
            else:
                data = {item.key: item.value for item in message.app_data}
                self._payload_rejection = ('deleted_notice' if data.get('message_type') == 'deleted_messages'
                    else 'subtype_mismatch' if (message.raw_data or 'subtype' in data)
                        and data.get('subtype') != self._credentials['gcm']['app_id']
                    else 'qv_payload_rejected')
        return decoded

    def _observe_payload_structure(self, stage, data):
        # No raw data survives this call. Diagnostic failure must not change
        # delivery, acknowledgement or filtering of a real notification.
        if stage not in ('transport_data', 'decrypted_envelope', 'parser_input'):
            return
        try:
            self._payload_shapes[stage] = summarize_payload_structure(data)
        except Exception:
            self._payload_shapes[stage] = {'status': 'inspection_failed'}

    async def _data_message(self, message):
        before = {key: self._diag[key] for key in ('delivered', 'duplicates', 'malformed', 'ignored', 'callback_errors')}
        self._payload_rejection = None
        self._payload_structure = None
        self._payload_shapes = {}
        self._payload_wire = None
        try:
            await super()._data_message(message)
        finally:
            reason = None
            if self._diag['duplicates'] > before['duplicates']:
                self._last_message_disposition = 'duplicate'
            elif self._diag['delivered'] > before['delivered']:
                self._last_message_disposition = 'delivered_to_filter'
            elif self._diag['callback_errors'] > before['callback_errors']:
                reason = 'callback_failed'
            elif self._diag['malformed'] > before['malformed']:
                reason = self._payload_rejection or 'malformed_payload'
            elif self._diag['ignored'] > before['ignored']:
                reason = self._payload_rejection or 'qv_payload_rejected'
            elif self._closed:
                self._last_message_disposition = 'receiver_closed'
            if reason in REJECTION_REASONS:
                self._last_message_disposition = 'rejected'
                self._rejection_counts[reason] += 1
            self._last_rejection_reason = reason if reason in REJECTION_REASONS else None
            self._last_payload_subreason = None
            self._last_payload_branch = None
            self._last_payload_missing_fields = ()
            if self._payload_structure is not None:
                subreason, branch, missing_fields = self._payload_structure
                self._last_payload_branch = branch
                self._payload_branch_counts[branch] += 1
                if reason == 'qv_payload_rejected':
                    self._last_payload_subreason = subreason
                    self._last_payload_missing_fields = missing_fields
                    self._payload_rejection_counts[subreason] += 1
                    for name in missing_fields:
                        self._payload_missing_field_counts[name] += 1
            if reason in REJECTION_REASONS:
                # Later housekeeping messages or duplicates must not erase the
                # useful rejected-call structure. Keep only three safe records.
                self._recent_payload_rejections.append({
                    'message_number': self._diag['messages'],
                    'reason': reason,
                    'subreason': self._last_payload_subreason,
                    'wire': deepcopy(self._payload_wire),
                    'stages': deepcopy(self._payload_shapes),
                })

    def _notification_data(self, data, *, has_notification=False):
        inspected = inspect_qv_notification_data(data, has_notification=has_notification)
        # Retain only allowlisted structure, never payload fields or their values.
        self._payload_structure = (inspected.subreason, inspected.branch, inspected.missing_fields)
        if inspected.data is None:
            self._payload_rejection = 'qv_payload_rejected'
        return inspected.data
