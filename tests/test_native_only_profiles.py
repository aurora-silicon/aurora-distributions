import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1]/'scripts/prepare-native-only-profiles.py'
spec = importlib.util.spec_from_file_location('native_only_profiles', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class NativeOnlyProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.key = self.root/'key.pem'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(self.key)],
                       check=True)
        public = subprocess.check_output(['openssl', 'pkey', '-in', str(self.key),
                                          '-pubout', '-outform', 'DER'])[-32:]
        (self.root/'keys').mkdir()
        (self.root/'keys/catalog.ed25519.pub').write_bytes(public)
        (self.root/'keys/supported-models.json').write_text(json.dumps({'supported': ['apple,j274']}))
        self.metadata = json.dumps({'os_list': [{'aurora_stage1': {
            'kind': 'common-raw-v1', 'qualified_machines': {'j274ap': ['26.6.2']}}}]}).encode()
        self.source = self.root/'source'
        self.source.mkdir()
        (self.source/'release.json').write_text('{}')
        (self.source/'trust-root.ed25519.pub').write_bytes(public)
        for profile in module.PROFILES:
            directory = self.source/'Profiles'/profile
            directory.mkdir(parents=True)
            (directory/'release.json').write_text('{}')
            (directory/'trust-root.ed25519.pub').write_bytes(public)
            (directory/'delivery.json').write_text('{}')
            (directory/'image-build.json').write_text('{}')
            catalog = {'schemaVersion': 4, 'sequence': 1, 'issuedAt': '2000-01-01T00:00:00Z',
                       'installer': {'minimumVersion': '0.1.0', 'latestVersion': '0.1.0',
                                     'downloadURL': 'https://example.invalid/old'},
                       'models': [{'deviceIdentifier': 'apple,j274', 'status': 'enabled',
                                   'installationProfile': profile, 'engineVersion': 'test',
                                   'engineDigest': 'sha256:'+'a'*64,
                                   'engineArtifact': {'sourceURL': 'https://example.invalid/engine'},
                                   'metadataDigest': 'sha256:'+hashlib.sha256(self.metadata).hexdigest(),
                                   'metadataArtifact': {'sourceURL': 'https://example.invalid/metadata'},
                                   'payloadDigest': 'sha256:'+'b'*64,
                                   'payloadArtifact': {'sourceURL': 'https://example.invalid/image'}}]}
            (directory/'catalog.json').write_text(json.dumps(catalog))
            self.sign(directory)

    def sign(self, directory):
        subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(self.key), '-rawin',
                        '-in', str(directory/'catalog.json'),
                        '-out', str(directory/'catalog.json.sig')], check=True)

    def prepare(self):
        with patch.object(module, 'ROOT', self.root), patch.object(module.time, 'time', return_value=100):
            module.prepare(self.source, self.root/'output', self.key, 'v0.1.0-alpha.6', 'a'*40,
                           fetch=lambda _: self.metadata)

    def test_reuses_authenticated_images_and_signs_engine_free_catalogs(self):
        original = {(profile, name): (self.source/'Profiles'/profile/name).read_bytes()
                    for profile in module.PROFILES for name in module.FILES}
        self.prepare()
        for profile in module.PROFILES:
            directory = self.root/'output/Profiles'/profile
            catalog = json.loads((directory/'catalog.json').read_text())
            model = catalog['models'][0]
            self.assertEqual(catalog['schemaVersion'], 5)
            self.assertEqual(catalog['sequence'], 100)
            self.assertEqual(catalog['installer']['downloadURL'],
                             'https://github.com/aurora-silicon/aurora-installer/releases/tag/v0.1.0-alpha.6')
            self.assertNotIn('engineArtifact', model)
            self.assertNotIn('engineDigest', model)
            self.assertEqual(model['metadataDigest'],
                             'sha256:'+hashlib.sha256(self.metadata).hexdigest())
            self.assertEqual(model['payloadDigest'], 'sha256:'+'b'*64)
            module.verify((directory/'catalog.json').read_bytes(),
                          (directory/'catalog.json.sig').read_bytes(),
                          (self.root/'keys/catalog.ed25519.pub').read_bytes())
            for name in module.FILES:
                self.assertEqual((self.source/'Profiles'/profile/name).read_bytes(), original[(profile, name)])

    def test_tampered_source_is_rejected_before_output(self):
        path = self.source/'Profiles/nixos-minimal/catalog.json'
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaises(subprocess.CalledProcessError):
            self.prepare()
        self.assertFalse((self.root/'output').exists())

    def test_missing_native_stage_one_policy_is_rejected_before_output(self):
        self.metadata = json.dumps({'os_list': [{'aurora_stage1': {
            'kind': 'common-raw-v1', 'qualified_machines': {'j700ap': ['26.6.2']}}}]}).encode()
        for profile in module.PROFILES:
            directory = self.source/'Profiles'/profile
            catalog = json.loads((directory/'catalog.json').read_text())
            catalog['models'][0]['metadataDigest'] = 'sha256:'+hashlib.sha256(self.metadata).hexdigest()
            (directory/'catalog.json').write_text(json.dumps(catalog))
            self.sign(directory)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse((self.root/'output').exists())


if __name__ == '__main__':
    unittest.main()
