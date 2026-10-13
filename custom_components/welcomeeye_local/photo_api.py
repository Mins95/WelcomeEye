"""Admin-only HA WebSocket photo operations and supported custom panel."""
from hashlib import sha256
from pathlib import Path

import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig

from .const import DOMAIN
from .photo_files import MAX_PHOTOS, PhotoError
from .photo_library import manager

PANEL_PATH = 'welcomeeye-photos'
MODULE_PATH = '/welcomeeye_local/photos.js'


@websocket_api.websocket_command({
    vol.Required('type'): 'welcomeeye_local/photos',
    vol.Required('operation'): vol.In(('devices', 'list', 'prepare', 'delete', 'cancel')),
    vol.Optional('entry_id'): vol.All(str, vol.Length(min=1, max=128)),
    vol.Optional('day'): vol.All(str, vol.Length(min=10, max=10)),
    vol.Optional('scope'): vol.In(('selection', 'day', 'device')),
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
            result = [{'entry_id': library.entry.entry_id, 'name': library.entry.title}
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
    if hass.data.get(DOMAIN + '_photos_registered'):
        return
    asset = Path(__file__).parent / 'frontend' / 'welcomeeye-photos.js'
    revision = await hass.async_add_executor_job(lambda: sha256(asset.read_bytes()).hexdigest()[:16])
    await hass.http.async_register_static_paths([StaticPathConfig(MODULE_PATH, str(asset), False)])
    websocket_api.async_register_command(hass, photos_command)
    await panel_custom.async_register_panel(hass, PANEL_PATH, 'welcomeeye-photos',
        sidebar_title='Photos WelcomeEye', sidebar_icon='mdi:image-multiple',
        module_url=MODULE_PATH + '?v=' + revision, require_admin=True)
    hass.data[DOMAIN + '_photos_registered'] = True
    hass.data[DOMAIN + '_photos_panel_visible'] = True


def remove_panel_if_empty(hass):
    if not manager(hass).libraries and hass.data.get(DOMAIN + '_photos_panel_visible'):
        frontend.async_remove_panel(hass, PANEL_PATH)
        hass.data[DOMAIN + '_photos_panel_visible'] = False


async def ensure_panel(hass):
    # Static assets and command registration live for the HA process. The panel
    # follows entry unloading/reloading without duplicating the WebSocket API.
    if (hass.data.get(DOMAIN + '_photos_registered')
            and not hass.data.get(DOMAIN + '_photos_panel_visible')):
        asset = Path(__file__).parent / 'frontend' / 'welcomeeye-photos.js'
        revision = await hass.async_add_executor_job(lambda: sha256(asset.read_bytes()).hexdigest()[:16])
        await panel_custom.async_register_panel(hass, PANEL_PATH, 'welcomeeye-photos',
            sidebar_title='Photos WelcomeEye', sidebar_icon='mdi:image-multiple',
            module_url=MODULE_PATH + '?v=' + revision, require_admin=True)
        hass.data[DOMAIN + '_photos_panel_visible'] = True
