"""Read-only presentation of the existing per-device cloud listener state."""
from .capabilities import DeviceVariant


_PROVIDERS = {
    DeviceVariant.V1: ('v1_cloud', 'v1_cloud_doorbell_enabled'),
    DeviceVariant.R002: ('qv_cloud', 'qv_cloud_enabled'),
    DeviceVariant.CONNECT3: ('qv_cloud', 'qv_cloud_enabled'),
}
SUBSCRIPTION_STATES = (
    'disabled', 'not_started', 'registering', 'accepted', 'confirmed',
    'unverified', 'mismatch', 'cleanup_pending', 'unknown',
)


def supports_cloud_status(hub):
    """Keep diagnostics present when cloud is disabled, without opting in."""
    return getattr(hub, 'variant', None) in _PROVIDERS


def _provider(hub):
    provider = _PROVIDERS.get(getattr(hub, 'variant', None))
    if provider is None:
        return None, False
    attribute, option = provider
    return getattr(hub, attribute, None), hub.entry.data.get(option) is True


def cloud_connected(hub):
    listener, enabled = _provider(hub)
    return bool(not hub.stopped and enabled and listener is not None
                and listener.connected is True)


def cloud_subscription_status(hub):
    """Last subscription result, independent of current receiver connectivity.

    No network, credentials, diagnostics payload or inferred ring delivery is
    exposed. A confirmed subscription can coexist with a disconnected receiver.
    """
    listener, enabled = _provider(hub)
    status = getattr(listener, 'subscription_status', 'not_started')
    if not enabled and status != 'cleanup_pending':
        return 'disabled'
    return status if type(status) is str and status in SUBSCRIPTION_STATES else 'unknown'
