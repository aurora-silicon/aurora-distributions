import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('adopt_set65', ROOT / 'scripts/adopt-set.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class AdoptSet65Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'keys').mkdir()
        for source, target in [('supported-models-58.json', 'supported-models.json'),
                               ('supported-models-65.json', 'supported-models-65.json')]:
            (self.root / 'keys' / target).write_bytes((ROOT / 'keys' / source).read_bytes())
        self.items = [{'repository': 'aurora-silicon/' + repository,
                       'tag': f'payload-{profile}-{desktop}-{index}-models65'}
                      for profile, repository in [('fedora', 'fedora-aurora'),
                                                  ('arch', 'alarm-aurora'), ('nixos', 'nixos-aurora')]
                      for index, desktop in enumerate(['minimal', 'kde', 'gnome'], 1)]
        self.calls = []

    def fake_run(self, command, check):
        self.calls.append(command)
        self.assertTrue(check)
        manifest = json.loads((self.root / 'keys/supported-models.json').read_text())
        self.assertEqual(len(manifest['supported']), 65)

    def test_complete_set_selects_the_65_model_manifest(self):
        module.adopt_set(self.items, 'models65', self.root, self.fake_run)
        self.assertEqual(len(self.calls), 10)

    def test_partial_or_mixed_sets_are_refused(self):
        for items in [self.items[:-1], [dict(self.items[0], tag='payload-fedora-minimal-1')] + self.items[1:]]:
            with self.assertRaisesRegex(ValueError, 'complete nine-profile'):
                module.adopt_set(items, 'models65', self.root, self.fake_run)
        self.assertFalse(self.calls)

    def test_nine_variants_cannot_duplicate_one_profile_or_repository(self):
        items = [dict(item) for item in self.items]
        items[0] = dict(items[1], tag=items[1]['tag'].replace('-2-', '-123-'))
        with self.assertRaisesRegex(ValueError, 'complete nine-profile'):
            module.adopt_set(items, 'models65', self.root, self.fake_run)

    def test_missing_old_caution_is_refused_before_adoption(self):
        path = self.root / 'keys/supported-models-65.json'
        manifest = json.loads(path.read_text())
        manifest['stage1_caution'].pop('apple,j813')
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'drops an existing'):
            module.adopt_set(self.items, 'models65', self.root, self.fake_run)


if __name__ == '__main__':
    unittest.main()
