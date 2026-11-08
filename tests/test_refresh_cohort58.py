"""ZIP and metadata migration tests; the SDK owns and tests DTB derivation."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('refresh58', ROOT / 'scripts/refresh-cohort58-v1.py')
refresh = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(refresh)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        storage = patch.object(refresh.shutil, 'disk_usage', return_value=SimpleNamespace(free=100 * 1024**3))
        storage.start()
        self.addCleanup(storage.stop)
        self.identifiers = json.loads((ROOT / 'keys/supported-models-58.json').read_text())['supported']
        old = set(self.identifiers) - refresh.OLED
        self.base = self.root / 'base.zip'
        self.members = {'boot.img': b'original boot filesystem' * 1024,
                        'root.img': b'original root filesystem' * 8192,
                        'esp/m1n1/boot.bin': b'authenticated common Stage2',
                        'esp/aurora/stage1.macho': b'authenticated padded Stage1'}
        boards = {}
        for identifier in sorted(old):
            board = identifier.removeprefix('apple,')
            member = f'esp/m1n1/boot-{board}.bin'
            boards[board + 'ap'] = member
            self.members[member] = b'original board ' + board.encode()
        with zipfile.ZipFile(self.base, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in self.members.items():
                info = zipfile.ZipInfo(name, date_time=(2026, 11, 8, 12, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
        self.metadata = {'os_list': [{'installation_profile': 'fedora-kde', 'package': 'base.zip',
            'partitions': [{'name': 'Root', 'image': 'root.img', 'bytes': 123456}],
            'aurora_stage1': {'kind': 'common-raw-v1',
                'qualified_machines': {i.removeprefix('apple,') + 'ap': ['27.0', '27.2'] for i in old}},
            'aurora_stage2': {'kind': 'board-file-v1', 'board_members': boards},
            'aurora_macho_boot': {'kind': 'padded-ctxr-v1',
                'stage1_member': 'esp/aurora/stage1.macho', 'confined_members': ['esp/m1n1/boot-ctxr.bin']}}]}
        self.delivery = {'artifacts': {'base.zip': {'bytes': self.base.stat().st_size,
                                                   'sha256': refresh.sha(self.base)}}}
        self.added = {name.removeprefix('apple,'): (b'new authenticated ' + name.encode(), 1)
                      for name in refresh.OLED}
        self.sdk = SimpleNamespace(oled_stage2_images=self.derive,
                                   aurora_recovery=SimpleNamespace(validate_payload=lambda archive: None))

    def derive(self, common, manifest):
        self.assertEqual(common, self.members['esp/m1n1/boot.bin'])
        self.assertEqual(len(manifest['boards']), 54)
        for board, record in manifest['boards'].items():
            self.assertEqual(record['sha256'], hashlib.sha256(self.members[record['member']]).hexdigest())
        return self.added

    def run_refresh(self, directory='out', metadata=None):
        output = self.root / directory
        patch = refresh.refresh(self.base, metadata or self.metadata, self.delivery, self.sdk,
                                self.identifiers, output, 'build-42508499173-models58')
        return output, patch

    def test_preserves_compressed_prefix_and_all_existing_members(self):
        output, patch = self.run_refresh()
        payload = output / patch['payload_name']
        self.assertEqual(self.base.read_bytes()[:patch['prefix_bytes']],
                         payload.read_bytes()[:patch['prefix_bytes']])
        with zipfile.ZipFile(payload) as archive:
            for name, data in self.members.items():
                self.assertEqual(archive.read(name), data)
            for name, data in patch['added_members'].items():
                self.assertEqual(archive.getinfo(name).compress_type, zipfile.ZIP_STORED)
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), data['sha256'])
        self.assertEqual(len(patch['added_members']), 8)

    def test_authenticated_tail_reconstructs_exact_new_zip(self):
        output, patch = self.run_refresh()
        data = self.base.read_bytes()[:patch['prefix_bytes']] + (output / patch['tail_name']).read_bytes()
        self.assertEqual(data, (output / patch['payload_name']).read_bytes())
        self.assertEqual(hashlib.sha256(data).hexdigest(), patch['sha256'])
        self.assertEqual(len(data), patch['bytes'])

    def test_refresh_is_deterministic(self):
        _, first = self.run_refresh('first')
        _, second = self.run_refresh('second')
        self.assertEqual(first, second)

    def test_metadata_preserves_old_policy_and_admits_all58(self):
        output, _ = self.run_refresh()
        new = json.loads((output / 'installer-data-fedora-kde.json').read_text())['os_list'][0]
        old = self.metadata['os_list'][0]
        self.assertEqual(new['partitions'], old['partitions'])
        for key, value in old['aurora_stage1']['qualified_machines'].items():
            self.assertEqual(new['aurora_stage1']['qualified_machines'][key], value)
        for key, value in old['aurora_stage2']['board_members'].items():
            self.assertEqual(new['aurora_stage2']['board_members'][key], value)
        self.assertEqual(len(new['aurora_stage1']['qualified_machines']), 58)
        for identifier in refresh.OLED:
            self.assertEqual(new['aurora_stage1']['qualified_machines'][identifier.removeprefix('apple,') + 'ap'], ['27.2'])

    def test_rejects_missing_old_model(self):
        bad = copy.deepcopy(self.metadata)
        bad['os_list'][0]['aurora_stage1']['qualified_machines'].pop('j700ap')
        with self.assertRaisesRegex(ValueError, 'preserve every original'):
            self.run_refresh(metadata=bad)
        self.assertFalse((self.root / 'out').exists())

    def test_rejects_changed_base_zip(self):
        with self.base.open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'base ZIP identity'):
            self.run_refresh()
        self.assertFalse((self.root / 'out').exists())

    def test_refuses_unapproved_old_image_before_network(self):
        with self.assertRaisesRegex(ValueError, 'nine verified legacy-audio'):
            refresh.authenticate('aurora-silicon/fedora-aurora', 'fedora-kde', 1, self.root, self.root)

    def test_storage_floor_is_checked_before_copy(self):
        with patch.object(refresh.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(ValueError, 'free-space floor'):
                self.run_refresh()
        self.assertFalse(list((self.root / 'out').glob('*.zip')))


if __name__ == '__main__':
    unittest.main()
