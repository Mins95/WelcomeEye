"""Render the real blueprint with synthetic rings, never send a notification."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment
import yaml

ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT = ROOT / 'blueprints/automation/welcomeeye_local/ring_photo_notification.yaml'


class Input(str):
    pass


class BlueprintLoader(yaml.SafeLoader):
    pass


BlueprintLoader.add_constructor('!input', lambda loader, node: Input(loader.construct_scalar(node)))


def load_blueprint(inputs=None):
    blueprint = yaml.load(BLUEPRINT.read_text(encoding='utf-8'), Loader=BlueprintLoader)
    values = {key: value['default'] for key, value in blueprint['blueprint']['input'].items()
              if 'default' in value}
    values.update({'doorbell_device': 'device', 'ring_image': 'image.fixture_last_ring',
                   'notification_action': 'notify.mobile_app_fixture', **(inputs or {})})

    def substitute(value):
        if isinstance(value, Input):
            return values[value]
        if isinstance(value, dict):
            return {key: substitute(item) for key, item in value.items()}
        if isinstance(value, list):
            return [substitute(item) for item in value]
        return value

    return substitute(blueprint)


class BlueprintScenario:
    def __init__(self, *, channel=None, inputs=None):
        self.config = load_blueprint(inputs)
        self.now = datetime(2026, 11, 5, 10, 0, 0, tzinfo=timezone.utc)
        self.attributes = {'ring_sequence': 7, 'capture_status': 'ready',
            'captured_at': self.now.isoformat(), 'filename':
            'WelcomeEye/welcomeeye_abcdef012345/2026-11-05/2026-11-05_11-00-00_ring_7.jpg'}
        self.attributes['media_content_id'] = 'media-source://welcomeeye_local/local/' + self.attributes['filename']
        self.ring = {key: self.attributes[key] for key in ('ring_sequence', 'filename', 'media_content_id')}
        self.ring.update(entry_id='entry', image_entity_id='image.fixture_last_ring')
        if channel is not None:
            self.attributes['channel'] = self.ring['channel'] = channel
        self.environment = NativeEnvironment(undefined=StrictUndefined)
        self.environment.tests.update(match=lambda value, pattern: bool(re.match(pattern, value)),
                                      search=lambda value, pattern: bool(re.search(pattern, value)))

        def timestamp(value, default=None):
            try:
                return (value if isinstance(value, datetime) else datetime.fromisoformat(value)).timestamp()
            except (ValueError, TypeError):
                return default

        self.environment.globals.update(
            now=lambda: self.now, as_timestamp=timestamp,
            state_attr=lambda entity, key: self.attributes.get(key) if entity == 'image.fixture_last_ring' else None,
            device_id=lambda entity: 'device' if entity == 'image.fixture_last_ring' else None,
            config_entry_id=lambda entity: 'entry' if entity == 'image.fixture_last_ring' else None)

    def render(self, value, variables):
        if isinstance(value, str):
            return self.environment.from_string(value).render(variables) if '{{' in value or '{%' in value else value
        if isinstance(value, dict):
            return {key: self.render(item, variables) for key, item in value.items()}
        if isinstance(value, list):
            return [self.render(item, variables) for item in value]
        return value

    def variables(self):
        variables = {'trigger': SimpleNamespace(event=SimpleNamespace(data=deepcopy(self.ring)))}
        for key, value in self.config['variables'].items():
            variables[key] = self.render(value, variables)
        return variables

    def accepted(self, variables=None):
        rendered = self.render(self.config['conditions'][0]['value_template'], variables or self.variables())
        # HA's Template renderer strips whitespace before parsing booleans.
        return rendered is True or isinstance(rendered, str) and rendered.strip() == 'True'

    def notification(self, variables):
        return self.render(self.config['actions'], variables)[0]


class RingPhotoBlueprintTests(unittest.TestCase):
    def test_saved_legacy_and_qv_photos_use_authenticated_immutable_media_and_uri_action(self):
        for channel in (None, 1, 2):
            scenario = BlueprintScenario(channel=channel)
            variables = scenario.variables()
            self.assertTrue(scenario.accepted(variables))
            notice = scenario.notification(variables)
            self.assertEqual(notice['action'], 'notify.mobile_app_fixture')
            payload = notice['data']['data']
            self.assertEqual(payload['image'], '/media/local/' + scenario.attributes['filename'])
            self.assertEqual(payload['actions'], [{'action': 'URI', 'title': 'View doorbell',
                                                  'uri': '/lovelace/welcomeeye'}])
            self.assertEqual(payload['clickAction'], payload['url'])
            self.assertEqual(payload['tag'], 'welcomeeye-entry-7')
            # If another ring arrives after the condition, the in-flight
            # notification remains tied to the accepted saved file.
            scenario.attributes.update(ring_sequence=8, media_content_id='next photo', filename='next.jpg')
            self.assertEqual(scenario.notification(variables), notice)
            self.assertFalse(scenario.accepted())

    def test_failed_save_old_capture_and_wrong_device_or_channel_are_ignored(self):
        for changes in ({'media_content_id': None}, {'filename': None}, {'ring_sequence': 6},
                        {'ring_sequence': True}, {'ring_sequence': '7'}, {'entry_id': 'other'},
                        {'image_entity_id': 'image.other'}, {'channel': 2}, {'channel': True}):
            scenario = BlueprintScenario(channel=1)
            scenario.ring.update(changes)
            self.assertFalse(scenario.accepted(), changes)
        for age in (61, -1):
            scenario = BlueprintScenario()
            scenario.attributes['captured_at'] = (scenario.now - timedelta(seconds=age)).isoformat()
            self.assertFalse(scenario.accepted())
        for state in ('capturing', 'failed', 'idle'):
            scenario = BlueprintScenario()
            scenario.attributes['capture_status'] = state
            self.assertFalse(scenario.accepted())
        scenario = BlueprintScenario()
        scenario.attributes['captured_at'] = 'not a date'
        self.assertFalse(scenario.accepted())

    def test_arbitrary_urls_traversal_and_another_ring_filename_are_rejected(self):
        valid = BlueprintScenario().ring['media_content_id']
        for media in ('https://example.com/photo.jpg', '/api/image_proxy/image.fixture_last_ring',
                      valid.replace('/WelcomeEye/', '/elsewhere/'),
                      valid.replace('/local/', '/%2e%2e/'), valid.replace('/local/', '/local%2fother/'),
                      valid.replace('/local/', '/%252e%252e/'), valid.replace('_ring_7', '_ring_8'),
                      valid + '?token=secret', valid.replace('.jpg', '.mp4')):
            scenario = BlueprintScenario()
            scenario.ring['media_content_id'] = scenario.attributes['media_content_id'] = media
            self.assertFalse(scenario.accepted(), media)
        scenario = BlueprintScenario()
        encoded = valid.replace('/local/', '/My%20photos/')
        scenario.ring['media_content_id'] = scenario.attributes['media_content_id'] = encoded
        self.assertTrue(scenario.accepted())
        self.assertTrue(scenario.notification(scenario.variables())['data']['data']['image'].startswith('/media/My%20photos/'))

    def test_malformed_targets_or_external_view_never_dispatch(self):
        for inputs in ({'notification_action': 'switch.turn_on'},
                       {'notification_action': ['notify.mobile_app_fixture']},
                       {'notification_action': 'notify.mobile_app_fixture\n'},
                       {'dashboard_path': '//example.com/view'},
                       {'dashboard_path': 'https://example.com/view'},
                       {'dashboard_path': '/lovelace/../config'},
                       {'dashboard_path': '/lovelace/welcomeeye\n'},
                       {'dashboard_path': '/lovelace/main?redirect=https://example.com'},
                       {'doorbell_device': 'other'}, {'ring_image': ['image.fixture_last_ring']},
                       {'maximum_age': '60'}, {'maximum_age': True}):
            self.assertFalse(BlueprintScenario(inputs=inputs).accepted(), inputs)

    def test_manual_run_missing_or_malformed_event_does_not_send(self):
        for ring in (None, [], 'bad', {}):
            scenario = BlueprintScenario()
            scenario.ring = ring
            self.assertFalse(scenario.accepted())
        scenario = BlueprintScenario()
        variables = {}
        for key, value in scenario.config['variables'].items():
            variables[key] = scenario.render(value, variables)
        self.assertFalse(scenario.accepted(variables))
