"""Small, bounded retry hints shared by cloud transports; no network I/O."""
import asyncio
from email.utils import parsedate_to_datetime
import errno
import math
import socket
import ssl
import time

MAX_RETRY_AFTER = 86400
RETRY_AFTER_TOO_LONG = -1


def retry_after_seconds(value, *, now=None):
    """Keep only a delay; an excessive server delay forbids an early retry."""
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        return None
    if value.isascii() and value.isdecimal():
        delay = int(value)
    else:
        try:
            stamp = parsedate_to_datetime(value)
            if stamp.tzinfo is None:
                return None
            delay = max(0, math.ceil(stamp.timestamp() - (time.time() if now is None else now)))
        except (ValueError, TypeError, OverflowError):
            return None
    return delay if delay <= MAX_RETRY_AFTER else RETRY_AFTER_TOO_LONG


def transient_transport_error(exc):
    """Recognize socket failures, excluding TLS, parsing and local I/O errors."""
    import aiohttp

    if isinstance(exc, (ssl.SSLError, aiohttp.ClientSSLError,
                        aiohttp.ServerFingerprintMismatch)):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError, asyncio.IncompleteReadError, socket.gaierror,
                        aiohttp.ClientConnectionError)):
        return True
    return isinstance(exc, OSError) and exc.errno in {
        errno.ECONNABORTED, errno.ECONNREFUSED, errno.ECONNRESET,
        errno.EHOSTUNREACH, errno.ENETDOWN, errno.ENETRESET,
        errno.ENETUNREACH, errno.ETIMEDOUT, errno.EPIPE,
    }
