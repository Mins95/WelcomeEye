"""Authenticated HA signaling and bundled intercom card registration."""
import asyncio
from pathlib import Path
import uuid

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import frontend, websocket_api
from homeassistant.components.camera.helper import get_camera_from_entity_id
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, VERSION


def camera_for(hass, connection, entity_id):
    if not connection.user.permissions.check_entity(entity_id, POLICY_READ):
        return None
    camera = get_camera_from_entity_id(hass, entity_id)
    if getattr(getattr(camera, 'hub', None), 'entry', None) is None:
        return None
    if camera.hub.entry.domain != DOMAIN:
        return None
    return camera


@websocket_api.websocket_command({vol.Required('type'): 'welcomeeye_local/player_config',
                                 vol.Required('entity_id'): cv.entity_id})
@websocket_api.async_response
async def player_config(hass, connection, msg):
    camera = camera_for(hass, connection, msg['entity_id'])
    if camera is None:
        connection.send_error(msg['id'], 'unauthorized', 'Caméra inaccessible')
        return
    registry = er.async_get(hass)
    buttons = {}
    for name, output in (('strike', 1), ('gate', 2)):
        entity_id = registry.async_get_entity_id('button', DOMAIN,
                                                f'{camera.hub.entry.unique_id}_open_output_{output}')
        buttons[name] = entity_id if entity_id and connection.user.permissions.check_entity(entity_id, POLICY_CONTROL) else None
    config = camera.async_get_webrtc_client_configuration().to_frontend_dict()
    connection.send_result(msg['id'], {**config, 'buttons': buttons,
        'microphone_allowed': connection.user.permissions.check_entity(msg['entity_id'], POLICY_CONTROL)})


@websocket_api.websocket_command({vol.Required('type'): 'welcomeeye_local/player_offer',
                                 vol.Required('entity_id'): cv.entity_id,
                                 vol.Required('offer'): vol.All(str, vol.Length(max=131072))})
@websocket_api.async_response
async def player_offer(hass, connection, msg):
    camera = camera_for(hass, connection, msg['entity_id'])
    if camera is None:
        connection.send_error(msg['id'], 'unauthorized', 'Caméra inaccessible')
        return
    session_id = uuid.uuid4().hex
    task = None
    @callback
    def close():
        if task and not task.done():
            task.cancel()
        camera.rtc.schedule_close(session_id)
    connection.subscriptions[msg['id']] = close
    connection.send_result(msg['id'])
    @callback
    def send(message):
        if msg['id'] in connection.subscriptions:
            connection.send_event(msg['id'], message.as_dict())
    task = asyncio.create_task(camera.rtc.offer(msg['offer'], session_id, send,
        allow_talk=connection.user.permissions.check_entity(msg['entity_id'], POLICY_CONTROL)))
    try:
        await task
    except asyncio.CancelledError:
        close()


async def async_setup_player(hass):
    url = '/welcomeeye_local/welcomeeye-card.js'
    await hass.http.async_register_static_paths([
        StaticPathConfig(url, str(Path(__file__).parent / 'frontend' / 'welcomeeye-card.js'), False)
    ])
    frontend.add_extra_js_url(hass, f'{url}?v={VERSION}')
    websocket_api.async_register_command(hass, player_config)
    websocket_api.async_register_command(hass, player_offer)
