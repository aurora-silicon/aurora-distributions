"""The metadata refresh must keep its attested original delivery and policy."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import test_refresh_cohort65 as fixtures

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('adopt65', ROOT / 'scripts/adopt-delivery.py')
adopter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adopter)


class Adopt65Tests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.Refresh65Tests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.f = fixture
        self.key = fixture.root / 'key.pem'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(self.key)], check=True)
        self.public = subprocess.check_output(['openssl', 'pkey', '-in', str(self.key),
                                              '-pubout', '-outform', 'DER'])[-32:]
        self.metadata_name = 'installer-data-fedora-kde.json'
        old_meta = (json.dumps(fixture.metadata, indent=2) + '\n').encode()
        for row in fixture.catalog['models']:
            row['metadataDigest'] = 'sha256:' + hashlib.sha256(old_meta).hexdigest()
        source_catalog = json.dumps(fixture.catalog).encode()
        fixture.lineage['catalog_sha256'] = hashlib.sha256(source_catalog).hexdigest()
        fixture.invoke()
        directory = fixture.root / 'output'
        self.catalog = json.loads((directory / 'catalog.json').read_text())
        self.receipt = json.loads((directory / 'delivery.json').read_text())
        self.data = {name: (directory / name).read_bytes() for name in ('metadata-refresh.json',)}
        self.data['source-delivery.json'] = json.dumps(fixture.lineage).encode()
        path = fixture.root / 'source.json'
        path.write_bytes(source_catalog)
        subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(self.key), '-rawin', '-in',
                        str(path), '-out', str(fixture.root / 'sig')], check=True)
        self.source = {'catalog.json': source_catalog,
                       'catalog.json.sig': (fixture.root / 'sig').read_bytes(),
                       'trust-root.ed25519.pub': self.public,
                       'delivery.json': json.dumps(fixture.delivery).encode(),
                       'image-build.json': json.dumps(fixture.image).encode(),
                       'release.json': b'{}', self.metadata_name: old_meta}
        self.new_meta = (directory / self.metadata_name).read_bytes()

    def invoke(self, provenance='verified'):
        fixture = self.f
        def opener(url, timeout):
            if '-models65/' in url:
                return io.BytesIO(self.new_meta)
            return io.BytesIO(self.source[url.rsplit('/', 1)[1]])
        with patch.object(adopter.urllib.request, 'urlopen', side_effect=opener), \
             patch.object(adopter, 'verify_provenance', return_value=provenance):
            adopter.verify_models65(fixture.repository, fixture.lineage['tag'] + '-models65',
                                   self.data, self.catalog, self.receipt, fixture.image, self.public)

    def test_authenticated_policy_only_refresh_is_accepted(self):
        self.invoke()

    def test_source_signature_is_still_required(self):
        value = json.loads(self.source['catalog.json'])
        value['sequence'] += 1
        self.source['catalog.json'] = json.dumps(value).encode()
        lineage = json.loads(self.data['source-delivery.json'])
        lineage['catalog_sha256'] = hashlib.sha256(self.source['catalog.json']).hexdigest()
        self.data['source-delivery.json'] = json.dumps(lineage).encode()
        patch_record = json.loads(self.data['metadata-refresh.json'])
        patch_record['source_catalog_sha256'] = lineage['catalog_sha256']
        self.data['metadata-refresh.json'] = json.dumps(patch_record).encode()
        with self.assertRaises(subprocess.CalledProcessError):
            self.invoke()

    def test_source_hosted_provenance_is_required(self):
        with self.assertRaisesRegex(ValueError, 'source delivery identity or provenance'):
            self.invoke(provenance='none')

    def test_original_payload_pointer_and_digest_cannot_change(self):
        for field, value in [('payloadDigest', 'sha256:' + 'e' * 64),
                             ('payloadArtifact', dict(self.catalog['models'][0]['payloadArtifact'],
                                                      sourceURL='https://example.invalid/payload.zip'))]:
            rows = copy.deepcopy(self.catalog['models'])
            for row in self.catalog['models']:
                row[field] = value
            with self.assertRaisesRegex(ValueError, 'original payload pointer or digest'):
                self.invoke()
            self.catalog['models'] = rows

    def test_missing_actual_member_evidence_is_refused(self):
        record = json.loads(self.data['metadata-refresh.json'])
        record['board_members'].pop('esp/m1n1/boot-j716c.bin')
        self.data['metadata-refresh.json'] = json.dumps(record).encode()
        with self.assertRaisesRegex(ValueError, 'complete bounded board-member'):
            self.invoke()

    def test_existing_model_fields_cannot_change(self):
        self.catalog['models'][1]['status'] = 'disabled'
        with self.assertRaisesRegex(ValueError, 'model fields beyond metadata admission'):
            self.invoke()

    def test_changed_metadata_outside_admission_is_refused(self):
        value = json.loads(self.new_meta)
        value['os_list'][0]['package'] = 'another.zip'
        self.new_meta = (json.dumps(value, indent=2) + '\n').encode()
        digest = 'sha256:' + hashlib.sha256(self.new_meta).hexdigest()
        for row in self.catalog['models']:
            row['metadataDigest'] = digest
            row['metadataArtifact']['sizeBytes'] = len(self.new_meta)
        self.receipt['artifacts'][self.metadata_name] = {'bytes': len(self.new_meta),
                                                       'sha256': digest.removeprefix('sha256:')}
        record = json.loads(self.data['metadata-refresh.json'])
        record['metadata_sha256'] = digest
        self.data['metadata-refresh.json'] = json.dumps(record).encode()
        with self.assertRaisesRegex(ValueError, 'beyond board admission'):
            self.invoke()

    def test_new_board_firmware_admission_cannot_be_broadened(self):
        value = json.loads(self.new_meta)
        value['os_list'][0]['aurora_stage1']['qualified_machines']['j716cap'] = ['13.5', '27.2']
        self.new_meta = (json.dumps(value, indent=2) + '\n').encode()
        digest = 'sha256:' + hashlib.sha256(self.new_meta).hexdigest()
        for row in self.catalog['models']:
            row['metadataDigest'] = digest
            row['metadataArtifact']['sizeBytes'] = len(self.new_meta)
        self.receipt['artifacts'][self.metadata_name] = {'bytes': len(self.new_meta),
                                                       'sha256': digest.removeprefix('sha256:')}
        record = json.loads(self.data['metadata-refresh.json'])
        record['metadata_sha256'] = digest
        self.data['metadata-refresh.json'] = json.dumps(record).encode()
        with self.assertRaisesRegex(ValueError, 'beyond board admission'):
            self.invoke()

class Authentication65Tests(unittest.TestCase):
    def setUp(self):
        Adopt65Tests.setUp(self)

    def authenticate(self, incomplete=False):
        source = self.source
        sums = {name: hashlib.sha256(value).hexdigest() for name, value in source.items()}
        record = ''.join(digest + '  ' + name + '\n' for name, digest in sums.items()).encode()
        def fetcher(url):
            name = url.rsplit('/', 1)[1]
            return record if name == 'SHA256SUMS' else source[name]
        subjects = [{'name': name, 'digest': {'sha256': digest}} for name, digest in sums.items()]
        if incomplete:
            subjects.pop()
        def gh(command, text):
            self.assertIn('--deny-self-hosted-runners', command)
            self.assertIn(fixtures.module.WORKFLOW, command)
            return json.dumps([{'verificationResult': {'statement': {'subject': subjects}}}])
        with patch.object(fixtures.module, 'fetch', side_effect=fetcher), \
             patch.object(fixtures.module.subprocess, 'check_output', side_effect=gh):
            return fixtures.module.authenticate(self.f.repository, self.f.profile, self.f.run,
                                                self.public, self.f.root)

    def test_source_records_require_complete_hosted_attestation(self):
        data, catalog, metadata, image, lineage = self.authenticate()
        self.assertEqual(lineage['provenance'], 'verified')
        with self.assertRaisesRegex(ValueError, 'hosted provenance does not cover'):
            self.authenticate(incomplete=True)


if __name__ == '__main__':
    unittest.main()
