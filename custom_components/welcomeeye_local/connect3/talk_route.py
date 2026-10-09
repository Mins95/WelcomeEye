"""Explicit secondary microphone trial using the APK's selected live context.

Door Connect selects the live channel, then opens its ordinary talk/65535
connection on the focused player. This preserves the SDK selector; it does
not prove which physical speaker a particular firmware routes it to.
"""
from ..capabilities import DeviceVariant
from .channels import media_profile_binding


def secondary_talk_enabled(hub):
    """A separate opt-in, never enabled by video/channel discovery alone."""
    parent = getattr(hub, 'parent', None)
    data = hub.entry.data
    return (getattr(hub, 'channel', None) == 2
        and getattr(hub, 'variant', None) == DeviceVariant.CONNECT3
        and data.get('experimental_channel2_microphone') is True
        and getattr(getattr(parent, 'capabilities', None), 'talkback', False) is True
        and (data.get('media_transport', 'tls') == 'tls'
            or (data.get('media_transport') == 'connect3_tcp'
                and data.get('experimental_tcp_controls') is True
                and data.get('media_tcp_approved') is True)))


def secondary_talk_context_valid(hub, session):
    """Do not follow a replacement session or send to an unselected channel."""
    if not secondary_talk_enabled(hub) or session is None:
        return False
    live = hub.live
    observation = live.observation
    binding = getattr(live, '_session_profile_binding', None)
    return (not hub.stopped and live.connected and live.session is session
        and getattr(hub.parent, '_media_claim', None) is live
        and bool(live.consumers)
        and live.channel == 2 and getattr(session, '_channel', None) == 2
        and getattr(session, '_close_task', None) is None
        and observation.get('play_accepted') is True
        and observation.get('cgi_https_verified') is True
        and observation.get('selected_channel') == 2
        and observation.get('decoded_frames', 0) > 0
        and binding is not None and binding == media_profile_binding(hub.entry.data))
