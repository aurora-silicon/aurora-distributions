import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

SPEC = importlib.util.spec_from_file_location(
    'channel_health', Path(__file__).resolve().parents[1]/'scripts/check-feeds.py')
feeds = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(feeds)


class ChannelHealthTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        key = self.root/'private.pem'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(key)], check=True)
        self.public = subprocess.check_output(
            ['openssl', 'pkey', '-in', str(key), '-pubout', '-outform', 'DER'])[-32:]
        self.payload = json.dumps({'schemaVersion': 5, 'channel': 'edge', 'sequence': 1,
                                   'models': []}).encode()
        (self.root/'catalog').write_bytes(self.payload)
        subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(key), '-rawin',
                        '-in', str(self.root/'catalog'), '-out', str(self.root/'signature')], check=True)

    def envelope(self, signature=None):
        return json.dumps({'schema_version': 1,
                           'catalog': base64.b64encode(self.payload).decode(),
                           'signature': base64.b64encode(
                               signature or (self.root/'signature').read_bytes()).decode()}).encode()

    def test_missing_edge_is_unavailable_and_never_falls_back_to_testing(self):
        error = urllib.error.HTTPError('https://example.invalid', 404, 'not found', None, None)
        with patch.object(feeds, 'fetch', side_effect=error) as fetch:
            result = feeds.installer_channel_status('https://example.invalid', 'edge', self.public, self.root)
        self.assertIn('unavailable', result)
        self.assertEqual(fetch.call_count, 1)
        self.assertIn('/channels/edge-alpha/', fetch.call_args.args[0])

    def test_signed_empty_edge_is_verified(self):
        with patch.object(feeds, 'fetch', return_value=self.envelope()):
            result = feeds.installer_channel_status('https://example.invalid', 'edge', self.public, self.root)
        self.assertIn('signature valid', result)
        self.assertIn('0 models', result)

    def test_bad_signature_is_not_mistaken_for_empty_edge(self):
        with patch.object(feeds, 'fetch', return_value=self.envelope(b'\0'*64)):
            with self.assertRaises(subprocess.CalledProcessError):
                feeds.installer_channel_status('https://example.invalid', 'edge', self.public, self.root)

    def test_server_failure_is_not_reported_as_unpublished_edge(self):
        error = urllib.error.HTTPError('https://example.invalid', 500, 'server error', None, None)
        with patch.object(feeds, 'fetch', side_effect=error):
            with self.assertRaises(urllib.error.HTTPError):
                feeds.installer_channel_status('https://example.invalid', 'edge', self.public, self.root)
