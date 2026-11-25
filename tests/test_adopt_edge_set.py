import base64
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import edge_fixtures as fx  # noqa: E402
from edge_fixtures import edge_train  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('adopt_edge_set', ROOT / 'scripts/adopt-edge-set.py')
adopter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adopter)
DELIVERY = importlib.util.spec_from_file_location('adopt_delivery_for_edge', ROOT / 'scripts/adopt-delivery.py')
delivery = importlib.util.module_from_spec(DELIVERY)
DELIVERY.loader.exec_module(delivery)
VERIFY = importlib.util.spec_from_file_location('verify_channels_for_edge', ROOT / 'scripts/verify-channel-catalogs.py')
verifier = importlib.util.module_from_spec(VERIFY)
VERIFY.loader.exec_module(verifier)


class TrainAdoptionTests(unittest.TestCase):
    """Ten signed, qualified deliveries of one train adopt together or not at all."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.site = self.root / 'site'
        (self.root / 'keys').mkdir()
        self.key = self.root / 'key.pem'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(self.key)], check=True)
        self.public = subprocess.check_output(['openssl', 'pkey', '-in', str(self.key),
                                              '-pubout', '-outform', 'DER'])[-32:]
        (self.root / 'keys/catalog.ed25519.pub').write_bytes(self.public)
        self.cohort = fx.cohort()
        self.train = fx.staged(fx.train())
        self.evidence = fx.evidence(self.train)
        self.deliveries = {}
        for profile in self.cohort['profiles']:
            self.deliveries[edge_train.release_tag(self.train, profile)] = self.delivery(profile)
        # The currently served state: signed empty Edge catalogs, and the Neo on Fedora KDE.
        for profile in self.cohort['profiles']:
            self.serve(profile, {'schemaVersion': 5, 'sequence': 1795085678, 'channel': 'edge', 'models': []})
        neo = copy.deepcopy(json.loads(self.deliveries[edge_train.release_tag(self.train, 'fedora-kde')]['catalog.json']))
        neo['sequence'] = 1795031723
        neo['models'] = [row for row in neo['models'] if row['deviceIdentifier'] == 'apple,j700']
        self.serve('fedora-kde', neo)

    def sign(self, raw):
        path = self.root / 'unsigned'
        path.write_bytes(raw)
        subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(self.key), '-rawin', '-in', str(path),
                        '-out', str(self.root / 'signature')], check=True)
        return (self.root / 'signature').read_bytes()

    def serve(self, profile, catalog):
        directory = self.site / 'installer/Profiles' / profile / 'channels/edge-alpha'
        directory.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(catalog).encode()
        signature = self.sign(raw)
        (directory / 'catalog.json').write_bytes(raw)
        (directory / 'catalog.json.sig').write_bytes(signature)
        (directory / 'catalog.signed.json').write_text(json.dumps({
            'schema_version': 1, 'catalog': base64.b64encode(raw).decode(),
            'signature': base64.b64encode(signature).decode()}))

    def delivery(self, profile, train=None):
        train = train or self.train
        catalog, receipt, raw_metadata = fx.template(train, profile)
        proof = json.dumps(fx.image_proof(train, profile)).encode()
        run = fx.image_run(train, profile)
        report = edge_train.bind(catalog, receipt, run, proof, self.evidence, self.cohort, train, profile)
        raw = json.dumps(catalog).encode()
        base = 'https://aurorasilicon.org/aurora-distributions/installer/Profiles/' + profile
        data = {
            'release.json': json.dumps({'schema_version': 3,
                'trust_root_fingerprint': 'sha256:' + hashlib.sha256(self.public).hexdigest(),
                'channels': {lane: {'catalog_url': base + '/channels/' + lane + '/catalog.signed.json'}
                             for lane in ('stable', 'rc', 'edge')}}).encode(),
            'trust-root.ed25519.pub': self.public, 'catalog.json': raw, 'catalog.json.sig': self.sign(raw),
            'delivery.json': json.dumps(receipt).encode(), 'image-build.json': json.dumps(run).encode(),
            'edge-qualification.json': report, 'edge-image-proof.json': proof,
            'installer-data-' + profile + '.json': raw_metadata,
        }
        if profile == 'omarchy-hyprland':
            data['vm-result.json'] = json.dumps(dict.fromkeys(delivery.VM_CHECKS, True)).encode()
        data.update(edge_train.device_evidence(catalog, self.cohort, train, profile))
        return data

    def items(self, train=None):
        train = train or self.train
        return [{'repository': edge_train.owner(profile), 'tag': edge_train.release_tag(train, profile)}
                for profile in self.cohort['profiles']]

    def snapshot(self):
        return {path.relative_to(self.site): path.read_bytes()
                for path in sorted(self.site.rglob('*')) if path.is_file()}

    def adopt(self, items=None, train=None):
        def opener(url, timeout):
            tag, name = url.rsplit('/', 2)[-2:]
            return io.BytesIO(self.deliveries[tag][name])
        train = train or self.train
        with patch.object(adopter, 'ROOT', self.root), \
             patch.object(adopter, 'verify_provenance', return_value='verified'), \
             patch.object(adopter, 'verify_edge_source_refs', return_value=None), \
             patch.object(adopter.urllib.request, 'urlopen', side_effect=opener):
            adopter.adopt_train(self.items() if items is None else items, self.cohort, train, self.site,
                                self.evidence, fx.manifests())

    def test_the_complete_train_is_adopted_with_its_record(self):
        self.adopt()
        record = json.loads((self.site / 'installer/edge-train.json').read_text())
        self.assertEqual(record['release'], 'alpha.4')
        self.assertEqual(record['trainDigest'], edge_train.train_digest(self.train))
        self.assertEqual(record['distributions'], self.train['distributions'])
        self.assertEqual(set(record['profiles']), set(self.cohort['profiles']))
        for profile in self.cohort['profiles']:
            channel = self.site / 'installer/Profiles' / profile / 'channels/edge-alpha'
            catalog = json.loads((channel / 'catalog.json').read_text())
            self.assertEqual(len(catalog['models']), 35, profile)
            self.assertEqual(record['profiles'][profile]['catalogSHA256'],
                             hashlib.sha256((channel / 'catalog.json').read_bytes()).hexdigest())
            self.assertTrue((channel / 'edge-qualification.json').is_file())
        fallback = self.site / 'installer/channels/edge-alpha'
        default_edge = self.site / 'installer/Profiles/fedora-gnome/channels/edge-alpha'
        for name in ('catalog.signed.json', 'edge-qualification.json'):
            self.assertEqual((fallback / name).read_bytes(), (default_edge / name).read_bytes())
        self.assertEqual(verifier.verify_channels(self.site, self.root / 'keys/catalog.ed25519.pub'), 11)

    def test_a_partial_set_is_refused_and_nothing_changes(self):
        before = self.snapshot()
        for drop in ('fedora-kde', 'omarchy-hyprland', 'nixos-minimal'):
            with self.subTest(drop=drop):
                items = [item for item in self.items() if not item['tag'].startswith('payload-' + drop + '-')]
                with self.assertRaisesRegex(ValueError, 'complete ten-profile train'):
                    self.adopt(items)
                self.assertEqual(self.snapshot(), before)

    def test_one_failing_profile_leaves_every_profile_unchanged(self):
        before = self.snapshot()
        tag = edge_train.release_tag(self.train, 'nixos-kde')
        self.deliveries[tag]['catalog.json.sig'] = bytes(64)
        with self.assertRaises(subprocess.CalledProcessError):
            self.adopt()
        self.assertEqual(self.snapshot(), before)

    def test_every_device_record_is_verified_and_served(self):
        tag = edge_train.release_tag(self.train, 'arch-gnome')
        name = edge_train.device_record_name('apple,j293')
        before = self.snapshot()
        original = self.deliveries[tag][name]
        self.deliveries[tag][name] = original.replace(b'apple,j293', b'apple,j313')
        with self.assertRaisesRegex(ValueError, 'device evidence'):
            self.adopt()
        self.assertEqual(self.snapshot(), before)
        self.deliveries[tag][name] = original
        self.adopt()
        served = self.site / 'installer/Profiles/arch-gnome/channels/edge-alpha'
        self.assertEqual((served / name).read_bytes(), original)
        (served / name).write_bytes(original + b' ')
        with self.assertRaisesRegex(ValueError, 'device evidence'):
            verifier.verify_channels(self.site, self.root / 'keys/catalog.ed25519.pub')

    def test_deliveries_of_another_train_are_refused(self):
        other = fx.staged(fx.train())
        other['profiles']['arch-kde']['image_run'] = 45999999999
        tag = edge_train.release_tag(other, 'arch-kde')
        self.deliveries[tag] = self.delivery('arch-kde', other)
        items = [item if not item['tag'].startswith('payload-arch-kde-') else {'repository': item['repository'], 'tag': tag}
                 for item in self.items()]
        with self.assertRaisesRegex(ValueError, 'complete ten-profile train'):
            self.adopt(items)

    def test_a_served_mac_cannot_silently_leave_edge(self):
        current = json.loads((self.site / 'installer/Profiles/fedora-kde/channels/edge-alpha/catalog.json').read_text())
        current['models'].append(dict(current['models'][0], deviceIdentifier='apple,j714s'))
        self.serve('fedora-kde', current)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'apple,j714s'):
            self.adopt()
        self.assertEqual(self.snapshot(), before)

    def test_the_sequence_must_exceed_every_served_edge_sequence(self):
        for tag in self.deliveries:
            catalog = json.loads(self.deliveries[tag]['catalog.json'])
            catalog['sequence'] = 1795085678
            raw = json.dumps(catalog).encode()
            self.deliveries[tag].update({'catalog.json': raw, 'catalog.json.sig': self.sign(raw)})
        with self.assertRaisesRegex(ValueError, 'sequence'):
            self.adopt()

    def test_a_withdrawn_mac_may_leave_edge(self):
        current = json.loads((self.site / 'installer/Profiles/fedora-kde/channels/edge-alpha/catalog.json').read_text())
        current['models'].append(dict(current['models'][0], deviceIdentifier='apple,j714s'))
        self.serve('fedora-kde', current)
        withdrawn = fx.staged(fx.train())
        withdrawn['withdrawn'] = ['apple,j714s']
        self.deliveries = {edge_train.release_tag(withdrawn, profile): self.delivery(profile, withdrawn)
                           for profile in self.cohort['profiles']}
        self.adopt(self.items(withdrawn), withdrawn)
        served = json.loads((self.site / 'installer/Profiles/fedora-kde/channels/edge-alpha/catalog.json').read_text())
        self.assertNotIn('apple,j714s', [row['deviceIdentifier'] for row in served['models']])

    def test_the_sequence_floor_is_per_profile(self):
        # Another profile's newer served catalog does not block this profile; the ten
        # train catalogs share one sequence with different payloads.
        self.serve('nixos-gnome', {'schemaVersion': 5, 'sequence': 1795300000, 'channel': 'edge', 'models': []})
        with self.assertRaisesRegex(ValueError, 'served nixos-gnome Edge sequence'):
            self.adopt()
        self.serve('nixos-gnome', {'schemaVersion': 5, 'sequence': 1795085678, 'channel': 'edge', 'models': []})
        self.adopt()
        sequences = {json.loads((self.site / 'installer/Profiles' / profile / 'channels/edge-alpha/catalog.json')
                                .read_text())['sequence'] for profile in self.cohort['profiles']}
        self.assertEqual(sequences, {1795200000})

    def test_a_relabelled_testing_payload_is_refused(self):
        testing = self.site / 'installer/Profiles/arch-gnome/catalog.json'
        catalog = json.loads(self.deliveries[edge_train.release_tag(self.train, 'arch-gnome')]['catalog.json'])
        testing.write_text(json.dumps({'channel': 'testing', 'models': [
            {'payloadDigest': catalog['models'][0]['payloadDigest']}]}))
        with self.assertRaisesRegex(ValueError, 'relabelled'):
            self.adopt()

    def test_the_single_profile_neo_path_is_closed_once_a_train_is_adopted(self):
        self.adopt()
        with patch.object(delivery, 'ROOT', self.root):
            with self.assertRaisesRegex(ValueError, 'one train'):
                delivery.adopt('aurora-silicon/fedora-aurora', 'payload-fedora-kde-44380859872-edge-alpha.5',
                               self.site, channel='edge')

    def test_served_state_must_match_the_train_record(self):
        self.adopt()
        self.serve('arch-minimal', {'schemaVersion': 5, 'sequence': 1795300000, 'channel': 'edge', 'models': []})
        with self.assertRaisesRegex(ValueError, 'train record'):
            verifier.verify_channels(self.site, self.root / 'keys/catalog.ed25519.pub')


class SingleProfileCompatibilityTests(unittest.TestCase):
    """A served single-profile Neo Edge catalog without `devices` (alpha.2, adopted before
    #63 and #64 merge) stays valid: served-state checks and Testing preparation accept it."""

    def setUp(self):
        import test_adopt_variant
        self.variant = test_adopt_variant
        test_adopt_variant.EdgeTests.setUp(self)
        test_adopt_variant.EdgeTests.invoke(self)
        self.site = self.root / 'site'
        # Rewrite the served state as the alpha.2 binder produced it: no devices, no records.
        channel = self.site / 'installer/Profiles/fedora-kde/channels/edge-alpha'
        catalog = json.loads((channel / 'catalog.json').read_text())
        proof = catalog['edgeQualification']
        proof.pop('devices', None)
        report = {key: value for key, value in proof.items() if key != 'evidenceDigest'}
        raw_report = (json.dumps(report, sort_keys=True, indent=2) + '\n').encode()
        proof['evidenceDigest'] = 'sha256:' + hashlib.sha256(raw_report).hexdigest()
        raw = json.dumps(catalog).encode()
        (self.root / 'unsigned').write_bytes(raw)
        subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(self.key), '-rawin', '-in',
                        str(self.root / 'unsigned'), '-out', str(self.root / 'signature')], check=True)
        envelope = json.dumps({'schema_version': 1, 'catalog': base64.b64encode(raw).decode(),
                               'signature': base64.b64encode((self.root / 'signature').read_bytes()).decode()})
        for directory in (channel, self.site / 'installer/channels/edge-alpha'):
            for record in directory.glob('edge-device-*.json'):
                record.unlink()
            (directory / 'catalog.signed.json').write_text(envelope)
            (directory / 'edge-qualification.json').write_bytes(raw_report)
        (channel / 'catalog.json').write_bytes(raw)
        (channel / 'catalog.json.sig').write_bytes((self.root / 'signature').read_bytes())

    def test_a_served_devices_less_neo_catalog_still_verifies(self):
        self.assertFalse((self.site / 'installer/edge-train.json').exists())
        self.assertFalse(list(self.site.rglob('edge-device-*.json')))
        self.assertEqual(verifier.verify_channels(self.site, self.root / 'keys/catalog.ed25519.pub'), 2)


if __name__ == '__main__':
    unittest.main()
