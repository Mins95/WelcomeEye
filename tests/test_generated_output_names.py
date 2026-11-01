"""Retained inactive controls lose provisional labels without being recreated."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from load_integration import load

localization = load('localization')


def entry(output=1, **changes):
    return SimpleNamespace(**({'domain': 'button', 'platform': 'welcomeeye_local',
        'unique_id': f'device_open_channel_2_output_{output}',
        'entity_id': f'button.owner_name_{output}', 'name': 'My custom label',
        'original_name': 'Portillon 2 (essai)' if output == 1 else 'Portail 2 (essai)',
        'disabled_by': 'user'} | changes))


class GeneratedOutputNameTests(unittest.TestCase):
    def test_all_old_generated_labels_and_no_other_registry_fields_change(self):
        for output, previous, current in (
            (1, 'Portillon 2 (essai)', 'Portillon 2'),
            (2, 'Portail 2 (essai)', 'Portail 2'),
            (1, 'Strike 2 (trial)', 'Wicket gate 2'),
            (2, 'Gate 2 (trial)', 'Vehicle gate 2'),
            (1, 'Puerta peatonal 2 (prueba)', 'Puerta peatonal 2'),
            (2, 'Portal 2 (prueba)', 'Portal 2'),
        ):
            with self.subTest(previous=previous):
                item = entry(output, original_name=previous)
                before = vars(item).copy()
                registry = Mock()
                localization.refresh_generated_output_names(registry, [item], 'device')
                registry.async_update_entity.assert_called_once_with(item.entity_id, original_name=current)
                self.assertEqual(vars(item), before)

    def test_no_renaming_of_other_devices_entities_or_non_generated_names(self):
        registry = Mock()
        items = [entry(platform='another_integration'), entry(domain='switch'),
                 entry(unique_id='other_open_channel_2_output_1'),
                 entry(unique_id='device_open_output_1'),
                 entry(original_name='Portillon 2'), entry(original_name='Custom (essai)'),
                 entry(original_name=None)]
        localization.refresh_generated_output_names(registry, items, 'device')
        registry.async_update_entity.assert_not_called()


if __name__ == '__main__':
    unittest.main()
