"""Shared photo preferences, preserving existing installations by default."""
import math

SHOW_PHOTO_LIBRARY = 'show_photo_library'
RING_IMAGE_DELAY_SECONDS = 'ring_image_delay_seconds'
DEFAULT_RING_IMAGE_DELAY_SECONDS = 4
MAX_RING_IMAGE_DELAY_SECONDS = 120


def show_photo_library(data):
    """Only an explicit opt-out hides an entry's shared sidebar library."""
    return data.get(SHOW_PHOTO_LIBRARY, True) is not False


def validated_ring_image_delay(value):
    """HA number selectors can return integral floats; reject booleans/strings."""
    if (type(value) not in (int, float) or not 0 <= value <= MAX_RING_IMAGE_DELAY_SECONDS
            or not math.isfinite(value) or int(value) != value):
        raise ValueError('invalid_ring_image_delay')
    return int(value)


def ring_image_delay_seconds(data):
    """Invalid legacy values cannot create an unbounded or negative timer."""
    try:
        return validated_ring_image_delay(data.get(
            RING_IMAGE_DELAY_SECONDS, DEFAULT_RING_IMAGE_DELAY_SECONDS))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_RING_IMAGE_DELAY_SECONDS
