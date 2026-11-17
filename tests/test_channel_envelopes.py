import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    'verify_channel_envelopes', Path(__file__).resolve().parents[1]/'scripts/verify-channel-catalogs.py')
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class ChannelEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.site = self.root/'site'
        self.key = self.root/'private.pem'
        self.public = self.root/'public.key'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(self.key)], check=True)
        self.public.write_bytes(subprocess.check_output(
            ['openssl', 'pkey', '-in', str(self.key), '-pubout', '-outform', 'DER'])[-32:])
        self.testing = {'schemaVersion': 5, 'channel': 'testing', 'sequence': 1,
                        'models': [{'payloadDigest': 'sha256:'+'b'*64}]}

    def serve(self, lane, value, bad_signature=False):
        folder = self.site/'installer/channels'/lane
        folder.mkdir(parents=True)
        raw = json.dumps(value).encode()
        (self.root/'catalog').write_bytes(raw)
        subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(self.key), '-rawin',
                        '-in', str(self.root/'catalog'), '-out', str(self.root/'signature')], check=True)
        signature = b'\0'*64 if bad_signature else (self.root/'signature').read_bytes()
        (folder/'catalog.signed.json').write_text(json.dumps({
            'schema_version': 1, 'catalog': base64.b64encode(raw).decode(),
            'signature': base64.b64encode(signature).decode()}))
        return folder

    def test_testing_legacy_alias_and_empty_edge_are_authenticated_separately(self):
        self.serve('testing', self.testing)
        self.serve('edge', self.testing)
        self.serve('edge-alpha', {'schemaVersion': 5, 'channel': 'edge', 'sequence': 1, 'models': []})
        self.assertEqual(verifier.verify_channels(self.site, self.public), 3)

    def test_testing_copied_into_the_edge_namespace_is_rejected(self):
        self.serve('edge-alpha', self.testing)
        with self.assertRaisesRegex(ValueError, 'cannot become Edge'):
            verifier.verify_channels(self.site, self.public)

    def test_bad_signature_is_rejected(self):
        self.serve('edge-alpha', {'schemaVersion': 5, 'channel': 'edge', 'models': []}, bad_signature=True)
        with self.assertRaises(subprocess.CalledProcessError):
            verifier.verify_channels(self.site, self.public)

    def test_wire_envelope_cannot_disagree_with_its_raw_catalog(self):
        folder = self.serve('testing', self.testing)
        (folder/'catalog.json').write_bytes(b'{}')
        (folder/'catalog.json.sig').write_bytes((self.root/'signature').read_bytes())
        with self.assertRaisesRegex(ValueError, 'envelope and raw catalog differ'):
            verifier.verify_channels(self.site, self.public)

    def test_arbitrary_unchannelled_data_is_not_legacy_development(self):
        value = dict(self.testing)
        value.pop('channel')
        self.serve('testing', value)
        with self.assertRaisesRegex(ValueError, 'authenticated legacy'):
            verifier.verify_channels(self.site, self.public)
