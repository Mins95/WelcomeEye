"""Register the bundled card in Home Assistant's persistent resource collection."""
import asyncio
import logging
from urllib.parse import urlsplit

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection

from .const import DOMAIN, VERSION

CARD_PATH = "/welcomeeye_local/welcomeeye-card.js"
CARD_URL = f"{CARD_PATH}?v={VERSION}"
_LOGGER = logging.getLogger(__name__)
_LOCK_KEY = f"{DOMAIN}_card_resource_lock"


def _is_bundled_card(url: str) -> bool:
    """Only manage this integration's relative URL, never another host's script."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return not parts.scheme and not parts.netloc and parts.path == CARD_PATH


async def async_register_card_resource(hass) -> bool:
    """Create/update one module resource, preserving unrelated and YAML resources.

    Loading through async_get_info is important: an unloaded collection otherwise
    looks empty, even when the user already registered the card manually.
    Failure must not prevent the camera or the fallback frontend loader starting.
    """
    lock = hass.data.setdefault(_LOCK_KEY, asyncio.Lock())
    async with lock:
        try:
            lovelace = hass.data[LOVELACE_DATA]
            resources = (
                lovelace.get("resources") if isinstance(lovelace, dict)
                else lovelace.resources
            )
            if not isinstance(resources, ResourceStorageCollection):
                _LOGGER.info(
                    "WelcomeEye card resources are YAML-managed; automatic frontend "
                    "loading remains enabled. Configure the module in YAML if needed"
                )
                return False

            await resources.async_get_info()
            matches = [
                item for item in resources.async_items()
                if _is_bundled_card(item.get("url", ""))
            ]
            if not matches:
                await resources.async_create_item(
                    {"url": CARD_URL, "res_type": "module"}
                )
            else:
                current = matches[0]
                if current["url"] != CARD_URL or current.get("type") != "module":
                    await resources.async_update_item(
                        current["id"], {"url": CARD_URL, "res_type": "module"}
                    )
                for duplicate in matches[1:]:
                    await resources.async_delete_item(duplicate["id"])
            return True
        except Exception as exc:
            # Do not expose storage contents or resource URLs in the log.
            _LOGGER.warning(
                "Cannot register WelcomeEye dashboard resource (%s); "
                "automatic frontend loading remains enabled. "
                "If the card is missing, add its JavaScript module manually",
                type(exc).__name__,
            )
            return False
