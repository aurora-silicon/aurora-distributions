import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('adopt_set', ROOT / 'scripts/adopt-set.py')
adopter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adopter)


class AdoptSetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'keys').mkdir()
        self.current = json.loads((ROOT / 'keys/supported-models.json').read_text())
        self.target = json.loads((ROOT / 'keys/supported-models-58.json').read_text())
        self.write('supported-models.json', self.current)
        self.write('supported-models-58.json', self.target)
        self.items = [{'repository': 'aurora-silicon/' + repo,
                       'tag': f'payload-{prefix}-{desktop}-{100 + index}-models58'}
                      for prefix, repo in [('fedora', 'fedora-aurora'), ('arch', 'alarm-aurora'),
                                           ('nixos', 'nixos-aurora')]
                      for index, desktop in enumerate(['minimal', 'kde', 'gnome'])]
        self.calls = []

    def write(self, name, value):
        (self.root / 'keys' / name).write_text(json.dumps(value))

    def fake_run(self, command, check):
        self.assertTrue(check)
        self.assertEqual(json.loads((self.root / 'keys/supported-models.json').read_text()), self.target)
        self.calls.append(command)

    def test_complete_set_selects_manifest_before_validation(self):
        adopter.adopt_set(self.items, 'models58', self.root, self.fake_run)
        self.assertEqual(len(self.calls), 10)
        self.assertTrue(self.calls[-1][-1].endswith('/scripts/verify-catalogs'))

    def test_partial_set_cannot_change_manifest(self):
        with self.assertRaisesRegex(ValueError, 'complete nine-profile'):
            adopter.adopt_set(self.items[:-1], 'models58', self.root, self.fake_run)
        self.assertEqual(json.loads((self.root / 'keys/supported-models.json').read_text()), self.current)
        self.assertFalse(self.calls)

    def test_mixed_old_variant_cannot_change_manifest(self):
        self.items[0]['tag'] = self.items[0]['tag'].removesuffix('-models58')
        with self.assertRaisesRegex(ValueError, 'complete nine-profile'):
            adopter.adopt_set(self.items, 'models58', self.root, self.fake_run)
        self.assertFalse(self.calls)

    def test_cannot_drop_any_existing_model(self):
        self.target['supported'].remove('apple,j700')
        self.target['supported'].append('apple,unknown')
        self.write('supported-models-58.json', self.target)
        with self.assertRaisesRegex(ValueError, 'drops an existing'):
            adopter.adopt_set(self.items, 'models58', self.root, self.fake_run)

    def test_cannot_drop_any_existing_caution(self):
        self.target['stage1_caution'].pop(next(iter(self.current['stage1_caution'])))
        self.write('supported-models-58.json', self.target)
        with self.assertRaisesRegex(ValueError, 'drops an existing'):
            adopter.adopt_set(self.items, 'models58', self.root, self.fake_run)

    def test_duplicate_model_or_delivery_is_refused(self):
        self.target['supported'].append(self.target['supported'][0])
        self.write('supported-models-58.json', self.target)
        with self.assertRaises(ValueError):
            adopter.adopt_set(self.items, 'models58', self.root, self.fake_run)
        with self.assertRaisesRegex(ValueError, 'duplicate delivery'):
            adopter.adopt_set(self.items[:-1] + self.items[:1], 'models58', self.root, self.fake_run)

    def test_failed_validation_stops_before_final_catalog_check(self):
        def failed(command, check):
            self.calls.append(command)
            raise subprocess.CalledProcessError(1, command)
        with self.assertRaises(subprocess.CalledProcessError):
            adopter.adopt_set(self.items, 'models58', self.root, failed)
        self.assertEqual(len(self.calls), 1)


if __name__ == '__main__':
    unittest.main()
