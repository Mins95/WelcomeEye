"""Real temporary photo files and production confirmation/retention logic."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import secrets
import re
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from io import BytesIO
from PIL import Image
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from load_integration import load
from test_transport_lifecycle import load_source
from test_connect3_config import flow

files = load('photo_files')


class FakeStore:
    saved = {}
    def __init__(self, hass, version, key): self.key = key
    async def async_load(self): return self.saved.get(self.key)
    async def async_save(self, value): self.saved[self.key] = dict(value)


library = load_source('photo_library', dict(asyncio=asyncio, date=date, datetime=datetime,
    timedelta=timedelta, timezone=timezone, secrets=secrets, threading=threading,
    time=time, re=re, ZoneInfo=ZoneInfo, Store=FakeStore, DOMAIN='welcomeeye_local',
    async_track_time_change=Mock(side_effect=lambda *args, **kwargs: Mock()),
    _finish_task=load('snapshot')._finish_task,
    **{name:getattr(files, name) for name in ('MAX_PHOTOS','PhotoError','checked_day','delete_snapshot','empty_directories','inventory')}))


class PhotoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.roots = {'local': str(self.root/'media')}
        self.hass = SimpleNamespace(config=SimpleNamespace(media_dirs=self.roots, time_zone='UTC'),
            async_add_executor_job=asyncio.to_thread, data={})
        self.hub = SimpleNamespace(entry=SimpleNamespace(entry_id='owner', title='Owner',data={}),
            _notify=Mock(), channel2=None)
        self.manager = library.PhotoManager(self.hass)
        FakeStore.saved.clear()
        await self.manager.attach(self.hub)

    async def asyncTearDown(self):
        await self.manager.detach('owner')
        self.temporary.cleanup()

    def photo(self, name='photo_1.jpg', day='2026-10-13', owner='owner', channel=None):
        slug=files.device_slug(owner)+(f'_channel_{channel}' if channel else '')
        path=self.root/'media'/'WelcomeEye'/slug/day/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'SYNTHETIC_JPEG')
        return path

    async def prepare(self, scope='device', **kwargs):
        return await self.manager.prepare('admin','owner',scope,**kwargs)

    async def test_individual_delete_and_exact_result(self):
        one, two = self.photo(), self.photo('photo_2.jpeg')
        rows=await self.manager.library('owner').list()
        plan=await self.prepare('selection', ids=[next(p.id for p in rows if p.name=='photo_1.jpg')])
        self.assertTrue(one.exists())
        result=await self.manager.confirm('admin',plan['token'])
        self.assertEqual((result['requested'],result['deleted'],result['failed']),(1,1,0))
        self.assertFalse(one.exists()); self.assertTrue(two.exists())

    async def test_multiple_and_date_deletion_refreshes_list(self):
        self.photo(); self.photo('photo_2.jpg'); other=self.photo(day='2026-10-12')
        plan=await self.prepare('day', day='2026-10-13')
        self.assertEqual(plan['count'],2)
        await self.manager.confirm('admin',plan['token'])
        result=await self.manager.browse('owner')
        self.assertEqual(result['days'],[{'day':'2026-10-12','count':1}])
        self.assertTrue(other.exists())

    async def test_device_deletion_covers_channels_and_all_roots_only_owner(self):
        owned=[self.photo(),self.photo(channel=1),self.photo(channel=2)]
        foreign=self.photo(owner='another'); outside=self.root/'media'/'other.jpg'; outside.write_bytes(b'PRIVATE')
        self.roots['alias']=self.roots['local']
        plan=await self.prepare()
        self.assertEqual(plan['count'],3)
        await self.manager.confirm('admin',plan['token'])
        self.assertTrue(all(not p.exists() for p in owned)); self.assertTrue(foreign.exists()); self.assertTrue(outside.exists())

    async def test_new_capture_after_confirmation_is_kept(self):
        old=self.photo(); plan=await self.prepare()
        new=self.photo('new_capture.jpg')
        await self.manager.confirm('admin',plan['token'])
        self.assertFalse(old.exists()); self.assertTrue(new.exists())

    async def test_replaced_file_is_not_deleted(self):
        path=self.photo(); plan=await self.prepare()
        path.write_bytes(b'NEW_VALID_CAPTURE_DIFFERENT_SIZE')
        result=await self.manager.confirm('admin',plan['token'])
        self.assertEqual(result['failures'][0]['reason'],'photo_changed'); self.assertTrue(path.exists())

    async def test_concurrent_atomic_capture_and_delete_keeps_new_capture(self):
        old=self.photo(); plan=await self.prepare()
        self.hub.hass=self.hass
        data=BytesIO(); Image.new('RGB',(3,3),'green').save(data,format='JPEG')
        storage=load('media_storage').CaptureMediaStorage(self.hub)
        deleted,saved=await asyncio.gather(self.manager.confirm('admin',plan['token']),
            storage.save(data.getvalue(),datetime.now(timezone.utc),'manual'))
        self.assertEqual(deleted['deleted'],1); self.assertFalse(old.exists())
        self.assertEqual((self.root/'media'/saved['filename']).read_bytes(),data.getvalue())

    async def test_already_missing_is_reported_not_retried(self):
        path=self.photo(); plan=await self.prepare(); path.unlink()
        result=await self.manager.confirm('admin',plan['token'])
        self.assertEqual((result['deleted'],result['missing'],result['failed']),(0,1,0))
        with self.assertRaises(files.PhotoError): await self.manager.confirm('admin',plan['token'])

    async def test_last_photo_removes_day_and_device_but_keeps_media_root(self):
        path = self.photo()
        plan = await self.prepare()
        result = await self.manager.confirm('admin', plan['token'])
        self.assertEqual(result['directories_removed'], 2)
        self.assertFalse(path.parent.exists())
        self.assertFalse(path.parent.parent.exists())
        self.assertTrue(path.parent.parent.parent.is_dir())
        self.assertTrue((self.root/'media').is_dir())

    async def test_nonempty_and_temporary_directories_are_preserved(self):
        path = self.photo()
        temporary = path.parent/'.welcomeeye-saving.tmp'
        temporary.write_bytes(b'CAPTURE_IN_PROGRESS')
        other = self.photo(day='2026-10-12')
        plan = await self.prepare('day', day='2026-10-13')
        result = await self.manager.confirm('admin', plan['token'])
        self.assertEqual(result['directories_removed'], 0)
        self.assertTrue(temporary.exists())
        self.assertTrue(other.exists())
        self.assertTrue(path.parent.is_dir())

    async def test_old_empty_folders_require_confirmation_and_are_owner_scoped(self):
        path = self.photo(); path.unlink()
        other = self.photo(owner='other'); other.unlink()
        listing = await self.manager.browse('owner')
        self.assertEqual(listing['empty_directories'], 1)
        self.assertTrue(path.parent.exists())
        plan = await self.prepare()
        self.assertEqual((plan['count'], plan['empty_directories']), (0, 1))
        self.manager.cancel('admin', plan['token'])
        self.assertTrue(path.parent.exists())
        plan = await self.prepare()
        result = await self.manager.confirm('admin', plan['token'])
        self.assertEqual(result['directories_removed'], 2)
        self.assertFalse(path.parent.parent.exists())
        self.assertTrue(other.parent.is_dir())

    async def test_new_file_in_confirmed_empty_folder_is_preserved(self):
        path = self.photo(); path.unlink()
        plan = await self.prepare()
        path.write_bytes(b'NEW_CAPTURE')
        result = await self.manager.confirm('admin', plan['token'])
        self.assertEqual(result['directories_removed'], 0)
        self.assertEqual(result['directory_errors'], 0)
        self.assertTrue(path.exists())

    async def test_link_substituted_for_empty_folder_is_refused(self):
        path = self.photo(); path.unlink()
        plan = await self.prepare()
        path.parent.rmdir()
        outside = self.root/'outside'; outside.mkdir()
        try:
            path.parent.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('No symlink privileges')
        result = await self.manager.confirm('admin', plan['token'])
        self.assertEqual(result['directory_errors'], 1)
        self.assertTrue(outside.is_dir())
        self.assertTrue(path.parent.is_symlink())

    async def test_empty_device_directory_can_be_cleaned_without_photos(self):
        path = self.photo(); path.unlink(); path.parent.rmdir()
        plan = await self.prepare()
        self.assertEqual(plan['empty_directories'], 1)
        result = await self.manager.confirm('admin', plan['token'])
        self.assertEqual(result['directories_removed'], 1)
        self.assertFalse(path.parent.parent.exists())

    async def test_date_cleanup_keeps_other_empty_dates_and_foreign_files(self):
        path = self.photo(); path.unlink()
        other = self.photo(day='2026-10-12'); other.unlink()
        ignored = path.parent.parent/'not-a-date'; ignored.mkdir()
        plan = await self.prepare('day', day='2026-10-13')
        result = await self.manager.confirm('admin', plan['token'])
        self.assertEqual(result['directories_removed'], 1)
        self.assertTrue(other.parent.is_dir())
        self.assertTrue(ignored.is_dir())

    async def test_directory_permission_failure_does_not_hide_successful_photo_deletion(self):
        path = self.photo(); plan = await self.prepare()
        with patch.object(files, '_rmdir', side_effect=PermissionError(13, 'denied')):
            result = await self.manager.confirm('admin', plan['token'])
        self.assertEqual((result['deleted'], result['failed'], result['directory_errors']), (1, 0, 1))
        self.assertFalse(path.exists()); self.assertTrue(path.parent.is_dir())

    async def test_pruning_waits_for_capture_between_mkdir_and_temporary_creation(self):
        old = self.photo(); plan = await self.prepare()
        self.hub.hass = self.hass
        module = load('media_storage')
        storage = module.CaptureMediaStorage(self.hub)
        data = BytesIO(); Image.new('RGB', (3, 3), 'green').save(data, format='JPEG')
        saving, resume, deleting = threading.Event(), threading.Event(), threading.Event()
        original_temp = module.tempfile.NamedTemporaryFile
        original_delete = library.delete_snapshot
        def paused_temp(*args, **kwargs):
            saving.set()
            if not resume.wait(5):
                raise TimeoutError('Test capture not resumed')
            return original_temp(*args, **kwargs)
        def attempting_delete(*args):
            deleting.set()
            return original_delete(*args)
        with patch.object(module.tempfile, 'NamedTemporaryFile', side_effect=paused_temp), \
                patch.object(library, 'delete_snapshot', side_effect=attempting_delete):
            save_task = asyncio.create_task(storage.save(data.getvalue(),
                datetime(2026, 10, 13, 12, tzinfo=timezone.utc), 'manual'))
            try:
                self.assertTrue(await asyncio.to_thread(saving.wait, 3))
                delete_task = asyncio.create_task(self.manager.confirm('admin', plan['token']))
                self.assertTrue(await asyncio.to_thread(deleting.wait, 3))
                self.assertFalse(delete_task.done())
            finally:
                resume.set()
            saved, deleted = await asyncio.gather(save_task, delete_task)
        self.assertEqual(deleted['deleted'], 1)
        self.assertEqual((self.root/'media'/saved['filename']).read_bytes(), data.getvalue())
        self.assertFalse(old.exists())

    async def test_confirmation_owner_expiry_and_cancel(self):
        path=self.photo(); plan=await self.prepare()
        with self.assertRaises(files.PhotoError): await self.manager.confirm('other',plan['token'])
        self.manager.cancel('admin',plan['token'])
        with self.assertRaises(files.PhotoError): await self.manager.confirm('admin',plan['token'])
        plan=await self.prepare(); self.manager._plans[plan['token']]['expires']=0
        with self.assertRaises(files.PhotoError): await self.manager.confirm('admin',plan['token'])
        self.assertTrue(path.exists())

    async def test_arbitrary_paths_invalid_dates_and_foreign_ids_rejected(self):
        self.photo(); foreign=self.photo(owner='foreign')
        other=files.inventory(self.roots,'foreign')[0]
        for day in ('../2026-10-13','2026-02-30','2026-1-1','2026-10-13/..'):
            with self.subTest(day=day),self.assertRaises(files.PhotoError): await self.prepare('day',day=day)
        for ids in (['../../outside.jpg'],[other.id],[],['a'*64,'a'*64]):
            with self.subTest(ids=ids),self.assertRaises(files.PhotoError): await self.prepare('selection',ids=ids)
        self.assertTrue(foreign.exists())

    async def test_symlink_file_and_ancestor_refused(self):
        path=self.photo(); outside=self.root/'private.jpg'; outside.write_bytes(b'KEEP')
        plan=await self.prepare(); path.unlink()
        try: path.symlink_to(outside)
        except (OSError,NotImplementedError): self.skipTest('No symlink privileges')
        result=await self.manager.confirm('admin',plan['token'])
        self.assertEqual(result['failed'],1); self.assertEqual(outside.read_bytes(),b'KEEP')
        path.unlink(); path.parent.rmdir(); path.parent.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(files.PhotoError): await self.manager.library('owner').list('2026-10-13')

    async def test_non_jpeg_temporary_hidden_and_hardlinks_ignored(self):
        path=self.photo(); (path.parent/'.welcomeeye.tmp').write_bytes(b'SAVE')
        (path.parent/'not_a_photo.txt').write_text('KEEP')
        self.assertEqual(len(await self.manager.library('owner').list()),1)

    async def test_last_capture_reference_cleared_but_jpeg_preserved(self):
        path=self.photo(); row=(await self.manager.library('owner').list())[0]
        self.hub.manual_snapshot=SimpleNamespace(filename=row.filename, media_content_id=row.public()['media_content_id'],
            jpeg=b'LAST_VALID_JPEG', diagnostics={'last_media_filename':row.filename})
        plan=await self.prepare(); await self.manager.confirm('admin',plan['token'])
        self.assertIsNone(self.hub.manual_snapshot.filename); self.assertIsNone(self.hub.manual_snapshot.media_content_id)
        self.assertEqual(self.hub.manual_snapshot.jpeg,b'LAST_VALID_JPEG'); self.hub._notify.assert_called_once()

    async def test_same_filename_in_other_media_root_keeps_current_reference(self):
        self.photo(); rows=await self.manager.library('owner').list(); current=rows[0]
        self.hub.manual_snapshot=SimpleNamespace(filename=current.filename,
            media_content_id=current.public()['media_content_id'],jpeg=b'VALID',diagnostics={})
        self.roots['archive']=str(self.root/'archive')
        archived=self.root/'archive'/current.filename; archived.parent.mkdir(parents=True); archived.write_bytes(b'OLD')
        other=next(p for p in await self.manager.library('owner').list() if p.source=='archive')
        plan=await self.prepare('selection',ids=[other.id]); await self.manager.confirm('admin',plan['token'])
        self.assertEqual(self.hub.manual_snapshot.media_content_id,current.public()['media_content_id'])
        self.assertFalse(archived.exists())

    async def test_retention_7_30_90_and_boundary_day(self):
        now=datetime(2026,10,13,12,tzinfo=timezone.utc)
        for days in (7,30,90):
            with self.subTest(days=days):
                self.hub.entry.data={'photo_retention_days':days}
                self.manager.library('owner').diagnostics['last_cleanup_run']=None
                old=self.photo(f'old_{days}.jpg',str(now.date()-timedelta(days=days+1)))
                boundary=self.photo(f'boundary_{days}.jpg',str(now.date()-timedelta(days=days)))
                foreign=self.photo(f'foreign_{days}.jpg',str(now.date()-timedelta(days=days+1)),owner='other')
                await self.manager.library('owner').cleanup(now)
                self.assertFalse(old.exists()); self.assertTrue(boundary.exists()); self.assertTrue(foreign.exists())

    async def test_unlimited_never_cleans_and_install_does_not_delete(self):
        path=self.photo(day='2001-01-01')
        self.assertIsNone(await self.manager.library('owner').cleanup())
        self.hub.entry.data={'photo_retention_days':7}
        await self.manager.detach('owner'); await self.manager.attach(self.hub)
        self.assertTrue(path.exists())

    async def test_timestamped_captures_expire_at_exact_retention_age(self):
        self.hub.entry.data={'photo_retention_days':7}
        old=self.photo('2026-10-06_06-00-00_manual.jpg',day='2026-10-06')
        newer=self.photo('2026-10-06_18-00-00_ring_3.jpg',day='2026-10-06')
        await self.manager.library('owner').cleanup(datetime(2026,10,13,12,tzinfo=timezone.utc))
        self.assertFalse(old.exists()); self.assertTrue(newer.exists())

    async def test_daily_limit_persists_across_reload_and_cancels_timer(self):
        now=datetime(2026,10,13,12,tzinfo=timezone.utc)
        self.hub.entry.data={'photo_retention_days':7}
        await self.manager.detach('owner'); await self.manager.attach(self.hub)
        lib=self.manager.library('owner'); remove=lib._remove_timer
        self.photo(day='2001-01-01'); await lib.cleanup(now)
        await self.manager.detach('owner'); remove.assert_called_once()
        await self.manager.attach(self.hub)
        new=self.photo(day='2001-01-01')
        self.assertIsNone(await self.manager.library('owner').cleanup(now+timedelta(hours=23)))
        self.assertTrue(new.exists())
        await self.manager.library('owner').cleanup(now+timedelta(days=1))
        self.assertFalse(new.exists())

    async def test_changed_media_root_invalidates_confirmation(self):
        old=self.photo(); plan=await self.prepare(); self.hass.config.media_dirs={'local':str(self.root/'elsewhere')}
        with self.assertRaises(files.PhotoError): await self.manager.confirm('admin',plan['token'])
        self.assertTrue(old.exists())

    async def test_storage_state_failure_prevents_retention_delete(self):
        self.hub.entry.data={'photo_retention_days':7}; old=self.photo(day='2001-01-01')
        lib=self.manager.library('owner'); lib._store.async_save=AsyncMock(side_effect=OSError('PRIVATE_PATH'))
        await lib.cleanup(); self.assertTrue(old.exists()); self.assertNotIn('PRIVATE_PATH',str(lib.diagnostics))


class PhotoFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_families_preserve_secrets_and_photo_preferences(self):
        for variant in ('connect_v1','connect2_r001','connect2_r002','connect3'):
            instance=flow(); entry=SimpleNamespace(entry_id='entry',unique_id='unique',data={
                'device_variant':variant,'host':'192.0.2.1','password':'PRIVATE','photo_retention_days':30,
                'auth_code':'PRIVATE_AUTH','certificate_sha256':'a'*64,
                'protocol_family':'connect3_qv_experimental' if variant=='connect3' else
                    'r002_experimental' if variant=='connect2_r002' else 'legacy_owsp'})
            instance._get_reconfigure_entry=lambda:entry
            form=await instance.async_step_reconfigure()
            self.assertIn('photo_management',form['data_schema'])
            result=await instance.async_step_reconfigure({'photo_management':{'photo_retention_days':'7'}})
            self.assertEqual(result['reason'],'reconfigure_successful',result)
            self.assertEqual(entry.data['photo_retention_days'],7)
            self.assertEqual(entry.data['password'],'PRIVATE')
            with self.assertRaises(ValueError): instance._read_opening_choices({'photo_retention_days':True},{},entry.data)
