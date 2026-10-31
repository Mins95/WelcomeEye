"""Admin-only HA WebSocket photo operations and supported custom panel."""
import asyncio
from hashlib import sha256
from pathlib import Path

import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig

from .const import DOMAIN
from .photo_files import MAX_PHOTOS, PhotoError
from .photo_library import manager
from .localization import configured_language
from .photo_settings import show_photo_library

PANEL_PATH = 'welcomeeye-photos'
MODULE_PATH = '/welcomeeye_local/photos.js'


@websocket_api.websocket_command({
    vol.Required('type'): 'welcomeeye_local/photos',
    vol.Required('operation'): vol.In(('devices', 'list', 'prepare', 'delete', 'cancel')),
    vol.Optional('entry_id'): vol.All(str, vol.Length(min=1, max=128)),
    vol.Optional('day'): vol.All(str, vol.Length(min=10, max=10)),
    vol.Optional('scope'): vol.In(('selection', 'day', 'device', 'empty')),
    vol.Optional('ids'): vol.All([str], vol.Length(max=MAX_PHOTOS)),
    vol.Optional('token'): vol.All(str, vol.Length(min=1, max=128)),
})
@websocket_api.async_response
async def photos_command(hass, connection, msg):
    if connection.user is None or not connection.user.is_admin:
        connection.send_error(msg['id'], 'unauthorized', 'Administrator required')
        return
    library_manager = manager(hass)
    try:
        operation = msg['operation']
        if operation == 'devices':
            result = [{'entry_id': library.entry.entry_id, 'name': library.entry.title,
                       'language': configured_language(library.entry.data)}
                      for library in library_manager.libraries.values()]
        elif operation == 'delete':
            result = await library_manager.confirm(connection.user.id, msg.get('token'))
        elif operation == 'cancel':
            result = library_manager.cancel(connection.user.id, msg.get('token'))
        elif operation == 'list':
            result = await library_manager.browse(msg.get('entry_id'), msg.get('day'))
        else:
            result = await library_manager.prepare(connection.user.id, msg.get('entry_id'),
                msg.get('scope'), msg.get('day'), msg.get('ids'))
    except PhotoError as exc:
        connection.send_error(msg['id'], str(exc), 'WelcomeEye photo operation unavailable')
    except OSError:
        connection.send_error(msg['id'], 'file_error', 'WelcomeEye storage unavailable')
    else:
        connection.send_result(msg['id'], result)


async def async_setup_photos(hass):
    # The API and its assets remain available when the shared sidebar is hidden.
    # Wait for loaded entries before deciding visibility; never flash the panel
    # during startup when all saved preferences have explicitly disabled it.
    async with _panel_lock(hass):
        if hass.data.get(DOMAIN + '_photos_registered'):
            return
        asset = Path(__file__).parent / 'frontend' / 'welcomeeye-photos.js'
        revision = await hass.async_add_executor_job(lambda: sha256(asset.read_bytes()).hexdigest()[:16])
        await hass.http.async_register_static_paths([StaticPathConfig(MODULE_PATH, str(asset), False)])
        websocket_api.async_register_command(hass, photos_command)
        hass.data[DOMAIN + '_photos_revision'] = revision
        hass.data[DOMAIN + '_photos_registered'] = True
        hass.data[DOMAIN + '_photos_panel_visible'] = False


def _panel_lock(hass):
    return hass.data.setdefault(DOMAIN + '_photos_panel_lock', asyncio.Lock())


def _panel_requested(hass):
    """A single shared panel is visible while any loaded device requests it."""
    return any(show_photo_library(library.entry.data)
               for library in manager(hass).libraries.values())


def remove_panel_if_empty(hass):
    """Remove the panel when no loaded device wants it, preserving storage/API."""
    if not _panel_requested(hass) and hass.data.get(DOMAIN + '_photos_panel_visible'):
        frontend.async_remove_panel(hass, PANEL_PATH)
        hass.data[DOMAIN + '_photos_panel_visible'] = False


async def ensure_panel(hass):
    # Concurrent entry setup must not register the same shared panel twice.
    async with _panel_lock(hass):
        remove_panel_if_empty(hass)
        if (not hass.data.get(DOMAIN + '_photos_registered')
                or not _panel_requested(hass)
                or hass.data.get(DOMAIN + '_photos_panel_visible')):
            return
        revision = hass.data[DOMAIN + '_photos_revision']
        await panel_custom.async_register_panel(hass, PANEL_PATH, 'welcomeeye-photos',
            sidebar_title='Photos WelcomeEye', sidebar_icon='mdi:image-multiple',
            module_url=MODULE_PATH + '?v=' + revision, require_admin=True)
        hass.data[DOMAIN + '_photos_panel_visible'] = True
        # Recheck after the await in case an entry unloaded during registration.
        remove_panel_if_empty(hass)
