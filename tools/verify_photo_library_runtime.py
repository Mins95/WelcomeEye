"""Actual HA custom panel, WS permissions, private storage and reload; temp only."""
import asyncio
import gc
import importlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import weakref

from homeassistant import core as ha_core
from homeassistant.components import frontend, websocket_api
from homeassistant.components.media_source.models import MediaSourceItem
from homeassistant.core import HomeAssistant


async def main(root):
    with tempfile.TemporaryDirectory() as temporary:
        sys.path.insert(0, str(root))
        api=importlib.import_module('custom_components.welcomeeye_local.photo_api')
        library=importlib.import_module('custom_components.welcomeeye_local.photo_library')
        files=importlib.import_module('custom_components.welcomeeye_local.photo_files')
        media=importlib.import_module('custom_components.welcomeeye_local.media_source')
        from homeassistant.helpers import group, device_registry, entity_registry, issue_registry
        previous_hass=ha_core._hass.hass
        hass=HomeAssistant(temporary)
        hass.config.media_dirs={'local':str(Path(temporary)/'media')}
        await hass.config.async_set_time_zone('UTC')
        paths=[]; registrations=[]
        async def static_paths(items): paths.extend(items)
        hass.http=SimpleNamespace(async_register_static_paths=static_paths)
        with patch.object(websocket_api,'async_register_command',side_effect=lambda h,command:registrations.append(command)):
            await api.async_setup_photos(hass)
        assert len(paths)==1 and Path(paths[0].path).is_file()
        panel=hass.data[frontend.DATA_PANELS][api.PANEL_PATH]
        assert panel.require_admin and panel.config['_panel_custom']['module_url'].startswith(api.MODULE_PATH)
        assert registrations==[api.photos_command]
        hubs=[]
        manager=library.manager(hass)
        for entry_id in ('primary','secondary'):
            hub=SimpleNamespace(entry=SimpleNamespace(entry_id=entry_id,title=entry_id,data={}),
                channel2=None,_notify=lambda:None)
            await manager.attach(hub); hubs.append(hub)
            for channel in ('','_channel_1','_channel_2'):
                folder=Path(hass.config.media_dirs['local'])/'WelcomeEye'/(files.device_slug(entry_id)+channel)/'2026-10-13'
                folder.mkdir(parents=True)
                (folder/'legacy-photo.jpg').write_bytes(b'SYNTHETIC_JPEG')
        sequence=0
        async def call(operation, *, admin=True, user='admin', **kwargs):
            nonlocal sequence
            sequence+=1
            future=asyncio.get_running_loop().create_future()
            def success(identifier,result): future.set_result(('result',result))
            def failure(identifier,code,message): future.set_result(('error',code))
            connection=SimpleNamespace(user=SimpleNamespace(id=user,is_admin=admin) if user else None,
                send_result=success,send_error=failure)
            msg=api.photos_command._ws_schema({'id':sequence,'type':'welcomeeye_local/photos','operation':operation,**kwargs})
            api.photos_command(hass,connection,msg)
            async with asyncio.timeout(5): return await future
        for operation,kwargs in (('devices',{}),('list',{'entry_id':'primary'}),
                ('prepare',{'entry_id':'primary','scope':'device'}),
                ('prepare',{'entry_id':'primary','scope':'empty'}),('delete',{'token':'arbitrary'})):
            assert (await call(operation,admin=False,**kwargs))==('error','unauthorized')
            assert (await call(operation,user=None,**kwargs))==('error','unauthorized')
        assert len((await call('devices'))[1])==2
        listed=(await call('list',entry_id='primary',day='2026-10-13'))[1]
        assert listed['count']==3 and len(listed['photos'])==3
        empty_folders=[]
        for entry_id in ('primary','secondary'):
            folder=Path(hass.config.media_dirs['local'])/'WelcomeEye'/files.device_slug(entry_id)/'2026-10-12'
            folder.mkdir(); empty_folders.append(folder)
        cleanup_plan=(await call('prepare',entry_id='primary',scope='empty'))[1]
        assert cleanup_plan['count']==0 and cleanup_plan['empty_directories']==1
        assert all(folder.exists() for folder in empty_folders)
        cleaned=(await call('delete',token=cleanup_plan['token']))[1]
        assert cleaned['deleted']==0 and cleaned['directories_removed']==1
        assert not empty_folders[0].exists() and empty_folders[1].exists()
        assert (await call('list',entry_id='primary',day='2026-10-13'))[1]['count']==3
        try:
            api.photos_command._ws_schema({'id':99,'type':'welcomeeye_local/photos','operation':'delete','path':'/media'})
        except Exception: pass
        else: raise AssertionError('Arbitrary path accepted')
        prepared=(await call('prepare',entry_id='primary',scope='selection',ids=[listed['photos'][0]['id']]))[1]
        assert (await call('delete',user='other',token=prepared['token']))[0]=='error'
        deleted=(await call('delete',token=prepared['token']))[1]
        assert deleted['deleted']==1 and deleted['failed']==0
        assert (await call('delete',token=prepared['token']))[0]=='error'
        assert (await call('list',entry_id='primary',day='2026-10-13'))[1]['count']==2
        source=media.WelcomeEyeMediaSource(hass)
        browsed=await source.async_browse_media(MediaSourceItem(hass,'welcomeeye_local','',None))
        assert len(browsed.children)==5
        day_plan=(await call('prepare',entry_id='primary',scope='day',day='2026-10-13'))[1]
        assert day_plan['count']==2
        assert (await call('delete',token=day_plan['token']))[1]['deleted']==2
        assert (await call('list',entry_id='secondary',day='2026-10-13'))[1]['count']==3
        await manager.detach('primary')
        assert (await call('list',entry_id='primary'))[0]=='error'
        await manager.attach(hubs[0])
        assert (await call('list',entry_id='primary'))[1]['count']==0
        await manager.detach('primary'); await manager.detach('secondary')
        api.remove_panel_if_empty(hass)
        assert api.PANEL_PATH not in hass.data[frontend.DATA_PANELS]
        await manager.attach(hubs[0]); await api.ensure_panel(hass)
        assert api.PANEL_PATH in hass.data[frontend.DATA_PANELS]
        await manager.detach('primary')
        await hass.async_stop(force=True)
        for getter in (device_registry.async_get,entity_registry.async_get,issue_registry.async_get,group.get_group_entities):
            if clear:=getattr(getter,'cache_clear',None): clear()
        ha_core._hass.hass=previous_hass
        print('Photo runtime: authenticated admin commands, panel, isolation, Media refresh and reload passed; real-device calls=0')
        return weakref.ref(hass)


if __name__=='__main__':
    fixture=asyncio.run(main(Path(__file__).resolve().parents[1]))
    gc.collect()
    assert fixture() is None, 'Photo HA fixture retained after shutdown'
    print('Photo runtime: cleanup complete')
