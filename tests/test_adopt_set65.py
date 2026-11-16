import importlib.util
import json
import subprocess
import sys
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
        self.core = [{'repository': 'aurora-silicon/' + repository,
                      'tag': f'payload-{profile}-{desktop}-{index}-models65'}
                     for profile, repository in [('fedora', 'fedora-aurora'),
                                                 ('arch', 'alarm-aurora'), ('nixos', 'nixos-aurora')]
                     for index, desktop in enumerate(['minimal', 'kde', 'gnome'], 1)]
        self.omarchy = {'repository': 'aurora-silicon/omarchy-aurora',
                        'tag': 'payload-omarchy-hyprland-123456789'}
        self.items = self.core + [self.omarchy]
        self.calls = []
        self.public_before = (self.root / 'keys/supported-models.json').read_bytes()

    def fake_run(self, command, check):
        self.calls.append(command)
        self.assertTrue(check)
        manifest = json.loads((self.root / 'keys/supported-models.json').read_text())
        self.assertEqual(len(manifest['supported']), 65)

    def assert_rejected_before_manifest_change(self, items, message='complete ten-profile'):
        with self.assertRaisesRegex(ValueError, message):
            module.adopt_set(items, 'models65', self.root, self.fake_run)
        self.assertEqual((self.root / 'keys/supported-models.json').read_bytes(), self.public_before)
        self.assertFalse(self.calls)

    def test_complete_ten_profile_set_authenticates_every_delivery(self):
        module.adopt_set(self.items, 'models65', self.root, self.fake_run)
        self.assertEqual(len(self.calls), 11)
        self.assertEqual(self.calls[-2][-4:], ['--repository', self.omarchy['repository'],
                                             '--tag', self.omarchy['tag']])
        self.assertTrue(all(command[1].endswith('/scripts/adopt-delivery.py')
                            for command in self.calls[:-1]))
        self.assertTrue(self.calls[-1][-1].endswith('/scripts/verify-catalogs'))

    def test_nine_only_partial_and_mixed_core_sets_are_refused(self):
        mixed = [dict(self.items[0], tag='payload-fedora-minimal-1')] + self.items[1:]
        for items in [self.core, self.items[:-2] + [self.omarchy], mixed]:
            with self.subTest(items=items):
                self.assert_rejected_before_manifest_change(items)

    def test_cli_accepts_models65_then_enforces_the_complete_set(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / 'scripts/adopt-set.py'), '--model-cohort', 'models65',
             '--deliveries', json.dumps(self.core)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('65-model adoption requires the complete ten-profile set', result.stderr)
        self.assertNotIn('invalid choice', result.stderr)

    def test_duplicate_profile_and_duplicate_delivery_are_refused(self):
        items = [dict(item) for item in self.items]
        items[0] = dict(items[1], tag=items[1]['tag'].replace('-2-', '-123-'))
        self.assert_rejected_before_manifest_change(items)
        self.assert_rejected_before_manifest_change(self.items[:-1] + self.items[:1], 'duplicate delivery')

    def test_wrong_repository_cannot_supply_any_profile(self):
        for index, repository in [(0, 'aurora-silicon/nixos-aurora'),
                                  (9, 'aurora-silicon/alarm-aurora')]:
            with self.subTest(index=index):
                items = [dict(item) for item in self.items]
                items[index]['repository'] = repository
                self.assert_rejected_before_manifest_change(items)

    def test_omarchy_must_be_a_normal_nonvariant_delivery(self):
        for suffix in ['-models65', '-models58', '-other', '-1-models65']:
            with self.subTest(suffix=suffix):
                items = self.core + [dict(self.omarchy, tag=self.omarchy['tag'] + suffix)]
                self.assert_rejected_before_manifest_change(items)

    def test_malformed_and_zero_image_run_tags_are_refused(self):
        for index, tag in [(0, 'payload-fedora-minimal-0-models65'),
                           (9, 'payload-omarchy-hyprland-0'),
                           (9, 'payload-omarchy-hyprland-123456789\n')]:
            with self.subTest(tag=tag):
                items = [dict(item) for item in self.items]
                items[index]['tag'] = tag
                self.assert_rejected_before_manifest_change(items)

    def test_nonstring_fields_are_refused_before_manifest_change(self):
        for field in ['repository', 'tag']:
            with self.subTest(field=field):
                items = [dict(item) for item in self.items]
                items[9][field] = ['unhashable']
                self.assert_rejected_before_manifest_change(items, 'invalid delivery set')

    def test_workflow_retains_the_models65_cli_choice(self):
        workflow = (ROOT / '.github/workflows/adopt-set.yml').read_text()
        self.assertIn('options: [current, models58, models65]', workflow)

    def test_missing_old_caution_is_refused_before_adoption(self):
        path = self.root / 'keys/supported-models-65.json'
        manifest = json.loads(path.read_text())
        manifest['stage1_caution'].pop('apple,j813')
        path.write_text(json.dumps(manifest))
        self.assert_rejected_before_manifest_change(self.items, 'drops an existing')

    def test_failed_omarchy_authentication_prevents_final_catalog_gate(self):
        def failed(command, check):
            self.fake_run(command, check)
            if self.omarchy['tag'] in command:
                raise subprocess.CalledProcessError(1, command)
        with self.assertRaises(subprocess.CalledProcessError):
            module.adopt_set(self.items, 'models65', self.root, failed)
        self.assertEqual(len(self.calls), 10)
        self.assertTrue(all('adopt-delivery.py' in command[1] for command in self.calls))
        self.assertEqual((self.root / 'keys/supported-models.json').read_bytes(), self.public_before)


if __name__ == '__main__':
    unittest.main()
