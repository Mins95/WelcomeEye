"""Fixed, in-memory enrollment outcomes; never store account or server data."""

_DATA_KEY = 'welcomeeye_local_qv_cloud_enrollment'
STAGES = frozenset({'not_started', 'service_discovery', 'region_discovery',
                    'account_login', 'device_list', 'device_selection', 'complete'})
STATUSES = frozenset({'not_started', 'running', 'failed', 'awaiting_device',
                      'cancelled', 'configured'})
ERROR_CODES = frozenset({
    'invalid_input', 'invalid_response', 'response_too_large', 'authentication_failed',
    'provider_rejected', 'region_redirect', 'unauthorized_device', 'invalid_channel',
    'connection_failed', 'request_timeout', 'closed', 'not_authenticated',
    'not_registered', 'invalid_service', 'no_devices', 'cancelled', 'unexpected_error',
})


def error_code(error):
    """Never return exception messages, arbitrary attributes or class names."""
    code = getattr(error, 'code', None)
    return code if isinstance(code, str) and code in ERROR_CODES else 'unexpected_error'


def record(hass, entry_id, status, stage, code=None):
    """Keep only one bounded outcome for this entry until HA restarts."""
    result = {
        'status': status if status in STATUSES else 'failed',
        'stage': stage if stage in STAGES else 'not_started',
        'error_code': code if isinstance(code, str) and code in ERROR_CODES else None,
    }
    data = getattr(hass, 'data', None)
    if isinstance(data, dict):
        data.setdefault(_DATA_KEY, {})[entry_id] = result
    return dict(result)


def snapshot(hass, entry_id):
    """Return a copy; no serialized entry data or runtime credentials are read."""
    result = getattr(hass, 'data', {}).get(_DATA_KEY, {}).get(entry_id)
    return dict(result) if result is not None else {
        'status': 'not_started', 'stage': 'not_started', 'error_code': None}


def forget(hass, entry_id):
    """Discard deleted-entry outcomes, while retaining them across reloads."""
    data = getattr(hass, 'data', {})
    outcomes = data.get(_DATA_KEY)
    if isinstance(outcomes, dict):
        outcomes.pop(entry_id, None)
        if not outcomes:
            data.pop(_DATA_KEY, None)
