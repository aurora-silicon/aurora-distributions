"""Metadata admission changes must retain authenticated payload identities."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('refresh65', ROOT / 'scripts/refresh-cohort65-v1.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def fdt(board, chip='t6050'):
    value = ('apple,' + board + '\0apple,' + chip + '\0').encode()
    value += b'\0' * (-len(value) % 4)
    structure = (struct.pack('>II', 1, 0) + struct.pack('>III', 3, len(value), 0)
                 + value + struct.pack('>II', 2, 9))
    strings = b'compatible\0'
    header = struct.pack('>10I', 0xd00dfeed, 56 + len(structure) + len(strings),
                         56, 56 + len(structure), 40, 17, 16, 0, len(strings), len(structure))
    return header + b'\0' * 16 + structure + strings


class Refresh65Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.identifiers = json.loads((ROOT / 'keys/supported-models-65.json').read_text())['supported']
        previous = json.loads((ROOT / 'keys/supported-models-58.json').read_text())['supported']
        self.profile = 'fedora-kde'
        self.repository = 'aurora-silicon/fedora-aurora'
        self.run = 43698407877
        self.base = f'https://github.com/{self.repository}/releases/download/payload-{self.profile}-{self.run}'
        self.target = self.base + '-models65'
        self.members = {f'esp/m1n1/{prefix}{board}.bin': b'AURA' + fdt(board) + b'NEXT'
                        for board in module.NEW_BOARDS for prefix in ('boot-', 'boot-ctxr-')}
        self.payload = self.root / 'payload.zip'
        self.write_zip()
        self.metadata = {'os_list': [{'installation_profile': self.profile, 'package': 'original.zip',
            'aurora_stage1': {'kind': 'common-raw-v1', 'qualified_machines': {
                identifier.removeprefix('apple,') + 'ap': ['27.2'] for identifier in previous}},
            'aurora_stage2': {'kind': 'board-file-v1', 'board_members': {
                board + 'ap': f'esp/m1n1/boot-{board}.bin' for board in module.NEW_BOARDS}},
            'aurora_macho_boot': {'kind': 'padded-ctxr-v1', 'confined_members': [
                f'esp/m1n1/boot-ctxr-{board}.bin' for board in module.NEW_BOARDS]}}]}
        model = {'status': 'enabled', 'installationProfile': self.profile,
                 'metadataArtifact': {'fileName': 'installer-data-fedora-kde.json'},
                 'metadataDigest': 'sha256:' + 'c' * 64,
                 'payloadArtifact': {'sourceURL': self.base + '/original.zip',
                    'fileName': 'original.zip', 'sizeBytes': self.payload.stat().st_size},
                 'payloadDigest': 'sha256:' + module.file_sha(self.payload)}
        self.catalog = {'schemaVersion': 5, 'sequence': 100, 'models': [
            dict(copy.deepcopy(model), deviceIdentifier=identifier) for identifier in previous]}
        self.delivery = {'installation_profile': self.profile, 'private_neo_inputs': False,
                         'native_neo_reset_qa': False, 'release_id': 'build-' + str(self.run),
                         'artifacts': {'original.zip': {'bytes': self.payload.stat().st_size,
                                                       'sha256': module.file_sha(self.payload)}}}
        self.image = {'id': self.run, 'head_sha': 'a' * 40, 'head_branch': 'main',
                      'conclusion': 'success', 'path': '.github/workflows/images.yml',
                      'head_repository': {'full_name': self.repository}}
        self.lineage = {'repository': self.repository, 'source_revision': 'a' * 40,
                        'tag': f'payload-{self.profile}-{self.run}', 'provenance': 'verified',
                        'catalog_sha256': 'b' * 64}

    def write_zip(self):
        with zipfile.ZipFile(self.payload, 'w') as archive:
            for name, value in self.members.items():
                archive.writestr(name, value)
            archive.writestr('boot.img', b'OS image fixture')
            archive.writestr('root.img', b'root image fixture')

    def update_payload_identity(self):
        digest = module.file_sha(self.payload)
        for model in self.catalog['models']:
            model['payloadDigest'] = 'sha256:' + digest
            model['payloadArtifact']['sizeBytes'] = self.payload.stat().st_size
        self.delivery['artifacts']['original.zip'] = {'bytes': self.payload.stat().st_size, 'sha256': digest}

    def invoke(self):
        return module.refresh(self.payload, self.catalog, self.metadata, self.delivery, self.image,
                              self.lineage, self.identifiers, self.root / 'output', self.target)

    def test_complete_real_board_members_expand_policy_without_changing_zip(self):
        before = self.payload.read_bytes()
        patch = self.invoke()
        self.assertEqual(self.payload.read_bytes(), before)
        self.assertTrue(patch['payload_unchanged'])
        output = self.root / 'output'
        self.assertFalse(list(output.glob('*.zip')))
        catalog = json.loads((output / 'catalog.json').read_text())
        self.assertEqual({m['deviceIdentifier'] for m in catalog['models']}, set(self.identifiers))
        for model in catalog['models']:
            self.assertEqual(model['payloadArtifact'], self.catalog['models'][0]['payloadArtifact'])
            self.assertEqual(model['payloadDigest'], self.catalog['models'][0]['payloadDigest'])
            self.assertEqual(model['metadataArtifact']['sourceURL'], self.target + '/installer-data-fedora-kde.json')
        metadata = json.loads((output / 'installer-data-fedora-kde.json').read_text())
        for board in module.NEW_BOARDS:
            self.assertEqual(metadata['os_list'][0]['aurora_stage1']['qualified_machines'][board + 'ap'], ['27.2'])
        self.assertEqual(len(patch['board_members']), 14)

    def test_existing_model_fields_are_preserved(self):
        self.catalog['models'][1]['sourceNote'] = 'retain existing board-specific evidence'
        self.invoke()
        catalog = json.loads((self.root / 'output/catalog.json').read_text())
        row = next(row for row in catalog['models']
                   if row['deviceIdentifier'] == self.catalog['models'][1]['deviceIdentifier'])
        self.assertEqual(row['sourceNote'], self.catalog['models'][1]['sourceNote'])

    def test_missing_declared_board_is_refused(self):
        self.metadata['os_list'][0]['aurora_stage2']['board_members'].pop('j716cap')
        with self.assertRaisesRegex(ValueError, 'lacks own board'):
            self.invoke()
        self.assertFalse((self.root / 'output').exists())

    def test_missing_real_stage2_and_ctxr_files_are_refused(self):
        for prefix in ('boot-', 'boot-ctxr-'):
            with self.subTest(prefix=prefix):
                self.setUp()
                self.members.pop(f'esp/m1n1/{prefix}j716c.bin')
                self.write_zip()
                self.update_payload_identity()
                with self.assertRaisesRegex(ValueError, 'ZIP lacks bounded own board'):
                    self.invoke()

    def test_wrong_board_and_wrong_chip_bytes_are_refused(self):
        for board, chip in [('j775d', 't6050'), ('j716c', 't8142')]:
            with self.subTest(board=board, chip=chip):
                self.setUp()
                self.members['esp/m1n1/boot-j716c.bin'] = b'AURA' + fdt(board, chip)
                self.write_zip()
                self.update_payload_identity()
                with self.assertRaisesRegex(ValueError, 'no matching T6050'):
                    self.invoke()

    def test_a_combined_image_is_not_an_own_board_image(self):
        self.members['esp/m1n1/boot-j716c.bin'] = b'AURA' + fdt('j716c') + fdt('j775d')
        self.write_zip()
        self.update_payload_identity()
        with self.assertRaisesRegex(ValueError, 'matching T6050-only'):
            self.invoke()

    def test_signed_zip_identity_is_required(self):
        self.payload.write_bytes(self.payload.read_bytes() + b'tamper')
        with self.assertRaisesRegex(ValueError, 'ZIP identity differs'):
            self.invoke()

    def test_source_metadata_inside_zip_requires_a_different_migration(self):
        with zipfile.ZipFile(self.payload, 'a') as archive:
            archive.writestr('installer-data-fedora-kde.json', b'old metadata')
        self.update_payload_identity()
        with self.assertRaisesRegex(ValueError, 'embeds installer metadata'):
            self.invoke()

    def test_unattested_or_changed_source_is_refused(self):
        self.lineage['provenance'] = 'none'
        with self.assertRaisesRegex(ValueError, 'hosted provenance lineage'):
            self.invoke()
        self.lineage['provenance'] = 'verified'
        self.lineage['source_revision'] = 'c' * 40
        with self.assertRaisesRegex(ValueError, 'hosted provenance lineage'):
            self.invoke()

    def test_dropping_old_or_adding_duplicate_model_is_refused(self):
        self.catalog['models'].pop()
        with self.assertRaisesRegex(ValueError, 'complete signed 58-model'):
            self.invoke()
        self.identifiers[-1] = self.identifiers[0]
        with self.assertRaisesRegex(ValueError, 'all 65'):
            self.invoke()

    def test_download_authenticates_both_parts_and_the_whole_zip(self):
        row = copy.deepcopy(self.catalog['models'][0])
        data = self.payload.read_bytes()
        chunks = [data[:len(data) // 2], data[len(data) // 2:]]
        row['payloadArtifact']['parts'] = [
            {'sourceURL': self.base + f'/original.zip.part{index:02d}',
             'fileName': f'original.zip.part{index:02d}', 'sizeBytes': len(chunk),
             'sha256': 'sha256:' + hashlib.sha256(chunk).hexdigest()}
            for index, chunk in enumerate(chunks)]
        def opener(url, timeout):
            return io.BytesIO(chunks[int(url[-2:])])
        with patch.object(module.shutil, 'disk_usage', return_value=SimpleNamespace(free=100 << 30)), \
             patch.object(module.urllib.request, 'urlopen', side_effect=opener):
            target = self.root / 'downloaded.zip'
            module.download_payload(row, self.repository, self.lineage['tag'], target)
            self.assertEqual(target.read_bytes(), data)
            row['payloadArtifact']['parts'][1]['sha256'] = 'sha256:' + 'e' * 64
            with self.assertRaisesRegex(ValueError, 'part identity differs'):
                module.download_payload(row, self.repository, self.lineage['tag'], self.root / 'tampered.zip')

    def test_download_respects_the_free_space_floor(self):
        with patch.object(module.shutil, 'disk_usage', return_value=SimpleNamespace(free=8 << 30)):
            with self.assertRaisesRegex(ValueError, 'free-space floor'):
                module.download_payload(self.catalog['models'][0], self.repository,
                                        self.lineage['tag'], self.root / 'downloaded.zip')
        self.assertFalse((self.root / 'downloaded.zip').exists())

    def test_signature_mutation_is_refused(self):
        key = self.root / 'key.pem'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(key)], check=True)
        public = subprocess.check_output(['openssl', 'pkey', '-in', str(key), '-pubout', '-outform', 'DER'])[-32:]
        path = self.root / 'catalog.json'
        path.write_bytes(b'catalog')
        subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(key), '-rawin', '-in', str(path),
                        '-out', str(self.root / 'sig')], check=True)
        signature = (self.root / 'sig').read_bytes()
        module.verify_signature(b'catalog', signature, public)
        with self.assertRaises(subprocess.CalledProcessError):
            module.verify_signature(b'altered catalog', signature, public)


if __name__ == '__main__':
    unittest.main()
