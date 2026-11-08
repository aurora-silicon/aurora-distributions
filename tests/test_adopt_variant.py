import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('adopt_variant', ROOT / 'scripts/adopt-delivery.py')
adopter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adopter)


class VariantTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'keys').mkdir()
        (self.root / 'site/installer').mkdir(parents=True)
        (self.root / 'site/installer/profile-builds.json').write_text(json.dumps({'profiles': [{'profile': 'fedora-kde'}]}))
        self.key = self.root / 'key.pem'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(self.key)], check=True)
        self.public = subprocess.check_output(['openssl', 'pkey', '-in', str(self.key),
                                              '-pubout', '-outform', 'DER'])[-32:]
        (self.root / 'keys/catalog.ed25519.pub').write_bytes(self.public)
        manifest = json.loads((ROOT / 'keys/supported-models-58.json').read_text())
        (self.root / 'keys/supported-models.json').write_text(json.dumps(manifest))
        profile = 'fedora-kde'
        self.catalog = {'schemaVersion': 5, 'sequence': 9999999999,
                        'models': [{'deviceIdentifier': i, 'installationProfile': profile,
                                    'status': 'enabled'} for i in manifest['supported']]}
        base = 'https://aurorasilicon.org/aurora-distributions/installer/Profiles/' + profile
        self.json = {
            'release.json': {'schema_version': 3,
                'trust_root_fingerprint': 'sha256:' + hashlib.sha256(self.public).hexdigest(),
                'channels': {c: {'catalog_url': base + '/channels/' + c + '/catalog.signed.json'}
                             for c in ['stable', 'rc', 'edge']}},
            'delivery.json': {'installation_profile': profile, 'private_neo_inputs': False,
                'native_neo_reset_qa': False, 'release_id': 'build-42508499173-models58',
                'artifacts': {'payload.zip': {'bytes': 123, 'sha256': 'a' * 64}}},
            'image-build.json': {'id': 42508499173, 'conclusion': 'success', 'head_branch': 'main',
                'head_sha': 'b' * 40, 'head_repository': {'full_name': 'aurora-silicon/fedora-aurora'},
                'path': '.github/workflows/images.yml'},
            'source-delivery.json': {'repository': 'aurora-silicon/fedora-aurora',
                'source_revision': 'b' * 40, 'tag': 'payload-fedora-kde-42508499173'},
            'zip-refresh.json': {'existing_compressed_members_changed': False,
                'payload_name': 'payload.zip', 'bytes': 123, 'sha256': 'a' * 64}}

    def invoke(self, provenance='verified', tag='payload-fedora-kde-42508499173-models58'):
        raw = json.dumps(self.catalog).encode()
        path = self.root / 'source.json'
        path.write_bytes(raw)
        subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(self.key), '-rawin',
                        '-in', str(path), '-out', str(self.root / 'source.sig')], check=True)
        data = {name: json.dumps(value).encode() for name, value in self.json.items()}
        data.update({'catalog.json': raw, 'catalog.json.sig': (self.root / 'source.sig').read_bytes(),
                     'trust-root.ed25519.pub': self.public})
        def opener(url, timeout):
            return io.BytesIO(data[url.rsplit('/', 1)[1]])
        with patch.object(adopter, 'ROOT', self.root), \
             patch.object(adopter, 'verify_provenance', return_value=provenance), \
             patch.object(adopter.urllib.request, 'urlopen', side_effect=opener):
            adopter.adopt('aurora-silicon/fedora-aurora', tag, self.root / 'site')

    def test_variant_keeps_original_image_binding_and_all58_models(self):
        self.invoke()
        path = self.root / 'site/installer/Profiles/fedora-kde/catalog.json'
        self.assertEqual(json.loads(path.read_text()), self.catalog)
        status = json.loads((self.root / 'site/installer/profile-builds.json').read_text())
        self.assertTrue(status['profiles'][0]['release'].endswith('-models58'))

    def test_variant_cannot_claim_a_different_image(self):
        with self.assertRaisesRegex(ValueError, 'image provenance differs'):
            self.invoke(tag='payload-fedora-kde-42508499174-models58')

    def test_variant_cannot_change_source_lineage(self):
        self.json['source-delivery.json']['source_revision'] = 'c' * 40
        with self.assertRaisesRegex(ValueError, 'lineage differs'):
            self.invoke()

    def test_variant_cannot_change_payload_digest(self):
        self.json['zip-refresh.json']['sha256'] = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'lineage differs'):
            self.invoke()

    def test_variant_requires_attested_provenance(self):
        with self.assertRaisesRegex(ValueError, 'require hosted provenance'):
            self.invoke(provenance='none (delivery predates attestations)')

    def test_variant_cannot_drop_a_supported_model(self):
        self.catalog['models'].pop()
        with self.assertRaisesRegex(ValueError, 'model coverage'):
            self.invoke()


if __name__ == '__main__':
    unittest.main()
