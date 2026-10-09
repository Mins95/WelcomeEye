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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import config_validation as cv

from .card_resource import CARD_PATH, CARD_URL, async_register_card_resource
from .const import DOMAIN


def _channel_cameras(hass, connection, camera):
    """Resolve exact camera identities only on explicitly supported parent hubs."""
    channel = getattr(camera, '_welcomeeye_channel', None)
    if channel not in (1, 2):
        return [], camera
    hub = camera.hub
    parent = getattr(hub, 'parent', hub)
    if getattr(parent, 'supports_multichannel_player', False) is not True:
        return [], camera
    registry = er.async_get(hass)
    confirmed = getattr(parent, 'confirmed_media_channels', frozenset())
    channels = []
    primary = None
    for number, suffix in ((1, 'camera'), (2, 'camera_channel_2')):
        entity_id = registry.async_get_entity_id('camera', DOMAIN, f'{hub.entry.unique_id}_{suffix}')
        if not entity_id:
            continue
        candidate = camera_for(hass, connection, entity_id)
        if candidate is None or candidate.hub.entry.entry_id != hub.entry.entry_id:
            continue
        if getattr(candidate.hub, 'parent', candidate.hub) is not parent:
            continue
        if getattr(candidate, '_welcomeeye_channel', None) != number:
            continue
        if number == 1:
            primary = candidate
        channels.append({'channel': number, 'entity_id': entity_id,
                         'label': f'Entrée {number}', 'confirmed': number in confirmed})
    return channels, primary


def camera_for(hass, connection, entity_id):
    if not connection.user.permissions.check_entity(entity_id, POLICY_READ):
        return None
    try:
        camera = get_camera_from_entity_id(hass, entity_id)
    except HomeAssistantError:
        return None
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
    channels, primary = _channel_cameras(hass, connection, camera)
    control_hub = primary.hub if primary is not None else None
    buttons = {}
    for name, output in (('strike', 1), ('gate', 2)):
        entity_id = registry.async_get_entity_id('button', DOMAIN,
                                                f'{camera.hub.entry.unique_id}_open_output_{output}')
        buttons[name] = entity_id if (control_hub is not None and getattr(control_hub.capabilities, name) and entity_id
            and connection.user.permissions.check_entity(entity_id, POLICY_CONTROL)) else None
    config = camera.async_get_webrtc_client_configuration().to_frontend_dict()
    connection.send_result(msg['id'], {**config, 'buttons': buttons,
        'channels': channels, 'primary_entity_id': primary.entity_id if primary is not None else None,
        'buttons_channel': 1 if channels else None, 'stop_supported': True,
        'microphone_allowed': camera.hub.capabilities.talkback and connection.user.permissions.check_entity(msg['entity_id'], POLICY_CONTROL)})


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
    state = {'entity_id': msg['entity_id'], 'session_id': session_id,
             'offer_task': None, 'cleanup_task': None}
    async def cleanup():
        task = state['offer_task']
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await camera.rtc.close(session_id)
    def start_cleanup():
        if state['cleanup_task'] is None:
            state['cleanup_task'] = asyncio.create_task(cleanup())
            state['cleanup_task'].add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None)
        return state['cleanup_task']
    @callback
    def close():
        start_cleanup()
    close.welcomeeye_session = state
    close.welcomeeye_stop = start_cleanup
    connection.subscriptions[msg['id']] = close
    connection.send_result(msg['id'])
    connection.send_event(msg['id'], {'type': 'session', 'session_id': session_id})
    @callback
    def send(message):
        if msg['id'] in connection.subscriptions:
            connection.send_event(msg['id'], message.as_dict())
    task = state['offer_task'] = asyncio.create_task(camera.rtc.offer(msg['offer'], session_id, send,
        allow_talk=camera.hub.capabilities.talkback and connection.user.permissions.check_entity(msg['entity_id'], POLICY_CONTROL)))
    try:
        await task
    except asyncio.CancelledError:
        close()


@websocket_api.websocket_command({vol.Required('type'): 'welcomeeye_local/player_stop',
                                 vol.Required('entity_id'): cv.entity_id,
                                 vol.Required('session_id'): vol.All(str, vol.Length(min=32, max=32))})
@websocket_api.async_response
async def player_stop(hass, connection, msg):
    """Acknowledge release only for this WebSocket's own requested viewer."""
    if camera_for(hass, connection, msg['entity_id']) is None:
        connection.send_error(msg['id'], 'unauthorized', 'Caméra inaccessible')
        return
    for close in tuple(connection.subscriptions.values()):
        state = getattr(close, 'welcomeeye_session', None)
        if state and state['session_id'] == msg['session_id'] and state['entity_id'] == msg['entity_id']:
            try:
                async with asyncio.timeout(30):
                    await asyncio.shield(close.welcomeeye_stop())
            except (Exception, asyncio.CancelledError):
                connection.send_error(msg['id'], 'cleanup_failed', 'Fermeture du lecteur non confirmée')
            else:
                connection.send_result(msg['id'], {'stopped': True})
            return
    connection.send_error(msg['id'], 'unauthorized', 'Cette session ne vous appartient pas')


async def async_setup_player(hass):
    await hass.http.async_register_static_paths([
        StaticPathConfig(CARD_PATH, str(Path(__file__).parent / 'frontend' / 'welcomeeye-card.js'), False)
    ])
    await async_register_card_resource(hass)
    frontend.add_extra_js_url(hass, CARD_URL)
    websocket_api.async_register_command(hass, player_config)
    websocket_api.async_register_command(hass, player_offer)
    websocket_api.async_register_command(hass, player_stop)
