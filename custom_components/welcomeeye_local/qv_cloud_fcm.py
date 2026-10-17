"""Explicit QV Firebase profiles over the existing bounded FCM transport.

Public resources.arsc configuration from com.extel.philipsdoorconnect
1.0.123.3(2) and com.extel.philipswelcomeeye 6.1.58.24. Both use tdkvdp,
but their Android application IDs differ. These public app IDs do not prove
Google accepts independent web registration or real hardware ring delivery.
"""
from .qv_cloud_events import qv_notification_data
from .v1_cloud_fcm import DEFAULT_PROFILE, FCMReceiver, FirebaseAppProfile


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

    def _notification_data(self, data, *, has_notification=False):
        return qv_notification_data(data, has_notification=has_notification)
