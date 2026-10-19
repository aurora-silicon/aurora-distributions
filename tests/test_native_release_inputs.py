import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1]/'scripts/prepare-native-release-inputs.py'
spec = importlib.util.spec_from_file_location('native_release_inputs', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class NativeReleaseInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.key = self.root/'key.pem'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(self.key)], check=True)
        public = subprocess.check_output(['openssl', 'pkey', '-in', str(self.key), '-pubout', '-outform', 'DER'])[-32:]
        (self.root/'keys').mkdir()
        (self.root/'keys/catalog.ed25519.pub').write_bytes(public)
        (self.root/'keys/supported-models.json').write_text(json.dumps({'supported': ['apple,j274']}))
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
                       'installer': {'minimumVersion': '2.1.0', 'latestVersion': '2.1.0',
                                     'downloadURL': 'https://example.invalid/old.zip'},
                       'models': [{'deviceIdentifier': 'apple,j274', 'status': 'enabled',
                                   'installationProfile': profile, 'payloadDigest': 'sha256:'+'a'*64}]}
            (directory/'catalog.json').write_text(json.dumps(catalog))
            self.sign(directory)

    def sign(self, directory):
        subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(self.key), '-rawin', '-in',
                        str(directory/'catalog.json'), '-out', str(directory/'catalog.json.sig')], check=True)

    def prepare(self):
        with patch.object(module, 'ROOT', self.root):
            module.prepare(self.source, self.root/'output', self.key, 'native-dev-20261018', 'a'*40)

    def test_changes_only_app_metadata_and_emits_valid_signatures(self):
        original = {profile: (self.source/'Profiles'/profile/'catalog.json').read_bytes()
                    for profile in module.PROFILES}
        self.prepare()
        for profile in module.PROFILES:
            directory = self.root/'output/Profiles'/profile
            catalog = json.loads((directory/'catalog.json').read_text())
            self.assertEqual(catalog['models'], json.loads(original[profile])['models'])
            self.assertEqual((self.source/'Profiles'/profile/'catalog.json').read_bytes(), original[profile])
            self.assertTrue(catalog['installer']['downloadURL'].endswith('/native-dev-20261018/Aurora-Installer-2.1.2-Developer.zip'))
            self.assertEqual(catalog['installer']['minimumVersion'], '2.1.2')
            self.assertEqual(catalog['installer']['latestVersion'], '2.1.2')
            module.verify((directory/'catalog.json').read_bytes(), (directory/'catalog.json.sig').read_bytes(),
                          (self.root/'keys/catalog.ed25519.pub').read_bytes())

    def test_tampering_is_rejected_before_any_output(self):
        path = self.source/'Profiles/nixos-minimal/catalog.json'
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaises(subprocess.CalledProcessError):
            self.prepare()
        self.assertFalse((self.root/'output').exists())

    def test_signed_nonpublic_model_cannot_enter_the_bundle(self):
        directory = self.source/'Profiles/fedora-kde'
        catalog = json.loads((directory/'catalog.json').read_text())
        catalog['models'][0]['deviceIdentifier'] = 'apple,j700'
        (directory/'catalog.json').write_text(json.dumps(catalog))
        self.sign(directory)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse((self.root/'output').exists())

    def test_newer_catalog_version_cannot_be_downgraded(self):
        directory = self.source/'Profiles/fedora-kde'
        catalog = json.loads((directory/'catalog.json').read_text())
        catalog['installer']['latestVersion'] = '2.1.3'
        (directory/'catalog.json').write_text(json.dumps(catalog))
        self.sign(directory)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse((self.root/'output').exists())


if __name__ == '__main__':
    unittest.main()
