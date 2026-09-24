"""Run against actual HA/Pillow APIs in an isolated Core Python process."""
import ast
import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.components.image import ImageEntity
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.device_registry import DeviceInfo
from PIL import Image


def load(path, scope):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (isinstance(node, ast.ImportFrom) and node.level)]
    exec(compile(tree, str(path), 'exec'), scope)


async def main(root):
    component = root / 'custom_components/welcomeeye_local'
    with tempfile.TemporaryDirectory() as tmp:
        hass = HomeAssistant(tmp)
        scope = {'__name__': __name__, 'DOMAIN': 'welcomeeye_local'}
        load(component / 'entity.py', scope)
        load(component / 'image.py', scope)
        state = SimpleNamespace(jpeg=None, updated=None, source=None, image_sequence=None,
                                status='idle', entity_id=None, close=AsyncMock(),
                                media_content_id=None, filename=None,
                                diagnostics={'last_save_error_type': None})
        manual = SimpleNamespace(**vars(state))
        hub = SimpleNamespace(entry=SimpleNamespace(unique_id='fixture', title='WelcomeEye fixture'),
                              device_model='WelcomeEye Connect 2', stopped=False,
                              ring_image=state, manual_snapshot=manual,
                              subscribe=lambda fn: lambda: None)
        entity = scope['WelcomeEyeRingImage'](hass, hub)
        snapshot = scope['WelcomeEyeSnapshotImage'](hass, hub)
        assert isinstance(entity, ImageEntity)
        assert entity.device_info['identifiers'] == {('welcomeeye_local', 'fixture')}
        assert entity.content_type == 'image/jpeg'
        assert entity.state is None
        assert await entity.async_image() is None
        fixture = BytesIO()
        Image.new('RGB', (16, 16), 'red').save(fixture, format='JPEG')
        scope.update(_finish_task=None)
        load(component / 'media_storage.py', scope)
        assert await hass.async_add_executor_job(scope['validate_jpeg'], fixture.getvalue()) == fixture.getvalue()
        try:
            await hass.async_add_executor_job(scope['validate_jpeg'], b'not-a-jpeg')
        except Exception:
            pass
        else:
            raise AssertionError('Corrupt JPEG accepted')
        state.jpeg = fixture.getvalue()
        state.updated = datetime.now(timezone.utc)
        state.source, state.image_sequence, state.status = 'fresh_snapshot', 1, 'ready'
        assert await entity.async_image() == fixture.getvalue()
        assert await snapshot.async_image() is None
        assert entity.state == state.updated.isoformat()
        timestamp = entity.state
        state.status = 'failed'
        assert entity.state == timestamp
        assert entity.extra_state_attributes['ring_sequence'] == 1
        assert not any(isinstance(value, bytes) for value in entity.extra_state_attributes.values())
        state.updated += timedelta(seconds=1)
        assert entity.state != timestamp
        await hass.async_stop(force=True)
    print('REAL_HA_IMAGE_API_AND_JPEG_VALIDATION_OK')


if __name__ == '__main__':
    asyncio.run(main(Path(__file__).parents[1]))
