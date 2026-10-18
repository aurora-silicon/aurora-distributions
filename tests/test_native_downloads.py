import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('native_downloads',
    Path(__file__).resolve().parents[1]/'scripts/fetch-native-downloads.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class NativeDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.site = Path(self.temporary.name)/'site'
        (self.site/'installer').mkdir(parents=True)
        self.key = self.site.parent/'private.pem'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(self.key)], check=True)
        self.public = subprocess.check_output(['openssl', 'pkey', '-in', str(self.key),
                                              '-pubout', '-outform', 'DER'])[-32:]
        self.payloads = {'zip': b'zip content', 'pkg': b'package content'}
        self.manifest = {'schema': 1, 'assets': {}}
        for kind, data in self.payloads.items():
            self.manifest['assets'][kind] = {
                'url': 'https://github.com/aurora-silicon/aurora-installer/releases/download/'
                       'native-dev-20261018/Aurora-Installer-2.1.0-Developer.'+kind,
                'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                'compatibility_paths': ['installer/installer/2.1.0/legacy.zip'] if kind == 'zip' else []}
        self.sign()

    def sign(self):
        path = self.site/'installer/native-downloads.json'
        path.write_text(json.dumps(self.manifest))
        subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(self.key), '-rawin',
                        '-in', str(path), '-out', str(path.with_suffix('.json.sig'))], check=True)

    def opener(self, request, timeout):
        return io.BytesIO(self.payloads[request.full_url.rsplit('.', 1)[1]])

    def test_authenticated_download_is_available_at_legacy_path(self):
        module.fetch(self.site, self.public, self.opener)
        self.assertEqual((self.site/'installer/installer/2.1.0/legacy.zip').read_bytes(), self.payloads['zip'])

    def test_unsigned_manifest_tamper_never_fetches(self):
        path = self.site/'installer/native-downloads.json'
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaises(subprocess.CalledProcessError):
            module.fetch(self.site, self.public, lambda *a, **k: self.fail('must not fetch'))

    def test_corrupt_package_prevents_even_verified_zip_alias(self):
        self.payloads['pkg'] = b'bad'
        with self.assertRaises(ValueError):
            module.fetch(self.site, self.public, self.opener)
        self.assertFalse((self.site/'installer/installer').exists())

    def test_manifest_cannot_escape_site_or_use_foreign_host(self):
        for alias in ('installer/../../escape.zip', '/installer/escape.zip',
                      'installer/../escape.zip', 'installer/catalog.json'):
            self.manifest['assets']['zip']['compatibility_paths'] = [alias]
            self.sign()
            with self.assertRaises(ValueError):
                module.fetch(self.site, self.public, lambda *a, **k: self.fail('must not fetch'))
        self.manifest['assets']['zip']['compatibility_paths'] = []
        self.manifest['assets']['zip']['url'] = self.manifest['assets']['zip']['url'].replace(
            'github.com/', 'github.com.attacker.invalid/')
        self.sign()
        with self.assertRaises(ValueError):
            module.fetch(self.site, self.public, lambda *a, **k: self.fail('must not fetch'))

    def test_partial_manifest_is_an_error(self):
        (self.site/'installer/native-downloads.json.sig').unlink()
        with self.assertRaises(ValueError):
            module.fetch(self.site, self.public, self.opener)


if __name__ == '__main__':
    unittest.main()
