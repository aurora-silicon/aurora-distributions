import base64
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
        (self.root/'keys/supported-models-58.json').write_text(json.dumps({'supported': ['apple,j160']}))
        (self.root/'keys/supported-models-65.json').write_text(json.dumps({'supported': ['apple,j274']}))
        self.metadata = json.dumps({'os_list': [{'aurora_stage1': {
            'kind': 'common-raw-v1', 'qualified_machines': {'j274ap': ['26.6.2']}}}]}).encode()
        self.source = self.root/'source'
        self.source.mkdir()
        (self.source/'release.json').write_text(json.dumps(self.descriptor()))
        (self.source/'trust-root.ed25519.pub').write_bytes(public)
        for profile in module.PROFILES:
            directory = self.source/'Profiles'/profile
            directory.mkdir(parents=True)
            (directory/'release.json').write_text(json.dumps(self.descriptor()))
            (directory/'trust-root.ed25519.pub').write_bytes(public)
            (directory/'delivery.json').write_text('{}')
            (directory/'image-build.json').write_text('{}')
            if profile == 'omarchy-hyprland':
                (directory/'vm-result.json').write_text(json.dumps(dict.fromkeys(module.VM_CHECKS, True)))
            catalog = {'channel': 'testing', 'schemaVersion': 4, 'sequence': 1, 'issuedAt': '2000-01-01T00:00:00Z',
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

    @staticmethod
    def descriptor():
        return {'schema_version': 3, 'default_channel': 'testing',
                'channels': {channel: {'catalog_url': 'https://example.invalid/channels/'
                              + ('edge-alpha' if channel == 'edge' else channel)
                              + '/catalog.signed.json'}
                             for channel in ('stable', 'rc', 'testing', 'edge')}}

    def sign(self, directory):
        subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(self.key), '-rawin',
                        '-in', str(directory/'catalog.json'),
                        '-out', str(directory/'catalog.json.sig')], check=True)

    def prepare(self, **options):
        with patch.object(module, 'ROOT', self.root), patch.object(module.time, 'time', return_value=100):
            module.prepare(self.source, self.root/'output', self.key, 'v0.1.0-alpha.6', 'a'*40,
                           fetch=lambda _: self.metadata, **options)

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

    def test_testing_is_preserved_and_edge_stays_signed_empty(self):
        self.prepare()
        for profile in module.PROFILES:
            root = self.root/'output/Profiles'/profile
            testing = json.loads((root/'catalog.json').read_text())
            self.assertEqual(testing['channel'], 'testing')
            self.assertEqual(testing['models'][0]['payloadDigest'], 'sha256:'+'b'*64)
            edge = json.loads((root/'channels/edge-alpha/catalog.json').read_text())
            self.assertEqual(edge['channel'], 'edge')
            self.assertEqual(edge['models'], [])
            self.assertNotIn('edgeQualification', edge)
            self.assertEqual((root/'channels/edge/catalog.signed.json').read_bytes(),
                             (root/'channels/testing/catalog.signed.json').read_bytes())
            descriptor = json.loads((root/'release.json').read_text())
            self.assertEqual(descriptor['default_channel'], 'testing')
            self.assertIn('/channels/edge-alpha/', descriptor['channels']['edge']['catalog_url'])

    def test_published_core_cohort_preserves_signed_edge_with_no_omarchy(self):
        report = self.stage_edge()
        active = self.root/'keys/supported-models.json'
        (self.root/'keys/supported-models-65.json').write_text(
            json.dumps({'supported': ['apple,j274', 'apple,j275']}))
        (self.root/'keys/supported-models-58.json').write_bytes(active.read_bytes())
        (self.source/'Profiles/omarchy-hyprland').rename(self.root/'omarchy-hyprland')
        self.prepare()
        output = self.root/'output/Profiles'
        self.assertEqual({profile.name for profile in output.iterdir()}, set(module.CORE_PROFILES))
        self.assertEqual((output/'fedora-gnome/channels/edge-alpha/edge-qualification.json').read_bytes(), report)
        self.assertEqual((output.parent/'channels/edge-alpha/edge-qualification.json').read_bytes(), report)
        self.assertEqual((output.parent/'catalog.json').read_bytes(),
                         (output/'fedora-gnome/catalog.json').read_bytes())

    def test_expanded_cohort_requires_its_omarchy_profile(self):
        (self.source/'Profiles/omarchy-hyprland').rename(self.root/'omarchy-hyprland')
        with self.assertRaisesRegex(ValueError, 'complete published model cohort'):
            self.prepare()
        self.assertFalse((self.root/'output').exists())

    def add_debian_profiles(self, profiles=module.DEBIAN_PROFILES, *, channel='testing'):
        public = (self.root/'keys/catalog.ed25519.pub').read_bytes()
        for profile in profiles:
            directory = self.source/'Profiles'/profile
            directory.mkdir(parents=True)
            (directory/'release.json').write_text(json.dumps(self.descriptor()))
            (directory/'trust-root.ed25519.pub').write_bytes(public)
            (directory/'delivery.json').write_text('{}')
            (directory/'image-build.json').write_text(json.dumps({
                'head_branch': 'main', 'path': '.github/workflows/images.yml', 'conclusion': 'success'}))
            catalog = {'schemaVersion': 5, 'sequence': 1, 'issuedAt': '2000-01-01T00:00:00Z',
                       'installer': {'minimumVersion': '0.1.0', 'latestVersion': '0.1.0',
                                     'downloadURL': 'https://example.invalid/old'},
                       'models': [{'deviceIdentifier': 'apple,j274', 'status': 'enabled',
                                   'installationProfile': profile,
                                   'metadataDigest': 'sha256:'+hashlib.sha256(self.metadata).hexdigest(),
                                   'metadataArtifact': {'sourceURL': 'https://example.invalid/metadata'},
                                   'payloadDigest': 'sha256:'+'b'*64,
                                   'payloadArtifact': {'sourceURL': 'https://example.invalid/image'}}]}
            if channel is not None:
                catalog['channel'] = channel
            (directory/'catalog.json').write_text(json.dumps(catalog))
            self.sign(directory)

    def test_expanded_cohort_prepares_all_three_debian_profiles_when_present(self):
        self.add_debian_profiles()
        self.prepare()
        output = self.root/'output/Profiles'
        self.assertEqual({profile.name for profile in output.iterdir()},
                         set(module.PROFILES) | set(module.DEBIAN_PROFILES))
        for profile in module.DEBIAN_PROFILES:
            catalog = json.loads((output/profile/'catalog.json').read_text())
            self.assertEqual(catalog['channel'], 'testing')
            self.assertEqual(catalog['schemaVersion'], 5)

    def test_partial_debian_profile_set_is_rejected(self):
        self.add_debian_profiles(('debian-minimal',))
        with self.assertRaisesRegex(ValueError, 'complete published model cohort'):
            self.prepare()
        self.assertFalse((self.root/'output').exists())

    def test_unchannelled_debian_profile_is_rejected(self):
        self.add_debian_profiles(channel=None)
        with self.assertRaisesRegex(ValueError, 'unchannelled testing'):
            self.prepare()
        self.assertFalse((self.root/'output').exists())

    def test_debian_profiles_are_rejected_in_the_58_model_cohort(self):
        self.add_debian_profiles()
        active = self.root/'keys/supported-models.json'
        (self.root/'keys/supported-models-65.json').write_text(
            json.dumps({'supported': ['apple,j274', 'apple,j275']}))
        (self.root/'keys/supported-models-58.json').write_bytes(active.read_bytes())
        (self.source/'Profiles/omarchy-hyprland').rename(self.root/'omarchy-hyprland')
        with self.assertRaisesRegex(ValueError, '65-model release cohort'):
            self.prepare()
        self.assertFalse((self.root/'output').exists())

    def stage_edge(self, payload_digit='c', branch='aurora-edge', devices=None):
        self.to_schema_five()
        root = self.source/'Profiles/fedora-gnome'
        edge = root/'channels/edge-alpha'
        edge.mkdir(parents=True)
        value = json.loads((root/'catalog.json').read_text())
        report = {'schemaVersion': 1, 'releaseKind': 'alpha', 'status': 'passed', 'imageRunID': 41,
                  'payloadDigest': 'sha256:'+payload_digit*64,
                  **{name: {'repository': 'aurora-silicon/'+name, 'branch': branch,
                            'revision': 'a'*40, 'tree': 'c'*40} for name in ('linux', 'mesa')}}
        for model, record in (devices or {}).items():
            (edge/('edge-device-' + model.replace(',', '-') + '.json')).write_bytes(record)
        if devices:
            report['devices'] = {model: {'evidenceDigest': 'sha256:'+hashlib.sha256(record).hexdigest()}
                                 for model, record in devices.items()}
        raw = json.dumps(report).encode()
        value.update(channel='edge', sequence=2, edgeQualification=dict(
            report, evidenceDigest='sha256:'+hashlib.sha256(raw).hexdigest()))
        value['models'][0]['payloadDigest'] = report['payloadDigest']
        (edge/'catalog.json').write_text(json.dumps(value))
        (edge/'edge-qualification.json').write_bytes(raw)
        self.sign(edge)
        (edge/'catalog.signed.json').write_text(json.dumps({
            'schema_version': 1,
            'catalog': base64.b64encode((edge/'catalog.json').read_bytes()).decode(),
            'signature': base64.b64encode((edge/'catalog.json.sig').read_bytes()).decode()}))
        return raw

    def test_a_future_qualified_edge_is_preserved_without_touching_testing_payloads(self):
        report = self.stage_edge()
        self.prepare()
        root = self.root/'output/Profiles/fedora-gnome'
        testing = json.loads((root/'catalog.json').read_text())
        edge = json.loads((root/'channels/edge-alpha/catalog.json').read_text())
        self.assertEqual(testing['channel'], 'testing')
        self.assertEqual(testing['models'][0]['payloadDigest'], 'sha256:'+'b'*64)
        self.assertEqual(edge['channel'], 'edge')
        self.assertEqual(edge['models'][0]['payloadDigest'], 'sha256:'+'c'*64)
        self.assertEqual(edge['sequence'], 2)
        source = self.source/'Profiles/fedora-gnome/channels/edge-alpha'
        self.assertEqual((root/'channels/edge-alpha/catalog.json').read_bytes(), (source/'catalog.json').read_bytes())
        self.assertEqual((root/'channels/edge-alpha/catalog.json.sig').read_bytes(), (source/'catalog.json.sig').read_bytes())
        self.assertEqual((root/'channels/edge-alpha/edge-qualification.json').read_bytes(), report)
        module.verify((root/'channels/edge-alpha/catalog.json').read_bytes(),
                      (root/'channels/edge-alpha/catalog.json.sig').read_bytes(),
                      (self.root/'keys/catalog.ed25519.pub').read_bytes())

    def test_an_adopted_edge_train_keeps_its_record_and_image_proofs(self):
        self.stage_edge()
        source = self.source/'Profiles/fedora-gnome/channels/edge-alpha'
        proof = b'{"schema": 2}\n'
        metadata = b'{"os_list": []}\n'
        (source/'edge-image-proof.json').write_bytes(proof)
        (source/'installer-data-fedora-gnome.json').write_bytes(metadata)
        record = b'{"schema_version": 1, "release": "alpha.4"}\n'
        (self.source/'edge-train.json').write_bytes(record)
        device = b'{"deviceIdentifier": "apple,j274"}\n'
        (source/'edge-device-apple-j274.json').write_bytes(device)
        self.prepare()
        output = self.root/'output'
        edge = output/'Profiles/fedora-gnome/channels/edge-alpha'
        self.assertEqual((edge/'edge-image-proof.json').read_bytes(), proof)
        self.assertEqual((edge/'installer-data-fedora-gnome.json').read_bytes(), metadata)
        self.assertEqual((output/'edge-train.json').read_bytes(), record)
        self.assertEqual((edge/'edge-device-apple-j274.json').read_bytes(), device)
        self.assertEqual((output/'channels/edge-alpha/edge-device-apple-j274.json').read_bytes(), device)

    def test_served_device_records_are_verified_and_kept_exactly(self):
        record = b'{"deviceIdentifier": "apple,j274", "family": "t8103"}\n'
        self.stage_edge(devices={'apple,j274': record})
        self.prepare()
        output = self.root/'output'
        for directory in (output/'Profiles/fedora-gnome/channels/edge-alpha', output/'channels/edge-alpha'):
            self.assertEqual((directory/'edge-device-apple-j274.json').read_bytes(), record)

    def test_a_missing_or_altered_served_device_record_is_refused(self):
        record = b'{"deviceIdentifier": "apple,j274", "family": "t8103"}\n'
        self.stage_edge(devices={'apple,j274': record})
        served = self.source/'Profiles/fedora-gnome/channels/edge-alpha/edge-device-apple-j274.json'
        for change in (lambda: served.write_bytes(record + b' '), served.unlink):
            with self.subTest(change=change):
                change()
                with self.assertRaisesRegex(ValueError, 'device evidence'):
                    self.prepare()
                served.write_bytes(record)

    def test_testing_payloads_cannot_be_resigned_into_edge(self):
        self.stage_edge(payload_digit='b')
        with self.assertRaisesRegex(ValueError, 'relabelled'):
            self.prepare()
        self.assertFalse((self.root/'output').exists())

    def test_wrong_source_branches_cannot_be_carried_into_edge(self):
        self.stage_edge(branch='main')
        with self.assertRaisesRegex(ValueError, 'aurora-edge provenance'):
            self.prepare()
        self.assertFalse((self.root/'output').exists())

    def to_schema_five(self, **extra):
        """Re-sign every profile as the engine-free delivery build-payload emits."""
        for profile in module.PROFILES:
            directory = self.source/'Profiles'/profile
            catalog = json.loads((directory/'catalog.json').read_text())
            catalog['schemaVersion'] = 5
            for model in catalog['models']:
                for field in module.EXECUTION_FIELDS:
                    model.pop(field, None)
                model.update(extra)
            (directory/'catalog.json').write_text(json.dumps(catalog))
            self.sign(directory)

    def test_omarchy_vm_evidence_is_required_and_preserved_for_the_release(self):
        path = self.source/'Profiles/omarchy-hyprland/vm-result.json'
        original = path.read_bytes()
        path.write_text(json.dumps(dict.fromkeys(module.VM_CHECKS, False)))
        with self.assertRaisesRegex(ValueError, 'complete passing VM'):
            self.prepare()
        self.assertFalse((self.root/'output').exists())
        path.write_bytes(original)
        self.prepare()
        self.assertEqual((self.root/'output/Profiles/omarchy-hyprland/vm-result.json').read_bytes(), original)

    def test_omarchy_missing_or_failed_package_trust_cannot_be_released(self):
        path = self.source/'Profiles/omarchy-hyprland/vm-result.json'
        complete = json.loads(path.read_text())
        for missing in (True, False):
            with self.subTest(missing=missing):
                evidence = dict(complete)
                if missing:
                    evidence.pop('package_trust')
                else:
                    evidence['package_trust'] = False
                path.write_text(json.dumps(evidence))
                with self.assertRaisesRegex(ValueError, 'complete passing VM'):
                    self.prepare()
                self.assertFalse((self.root/'output').exists())

    def test_engine_free_deliveries_are_resigned_for_the_release(self):
        self.to_schema_five()
        self.prepare()
        receipt = json.loads((self.root/'output/native-only-catalogs.json').read_text())
        self.assertEqual(receipt['source_catalog_schemas'], [5])
        for profile in module.PROFILES:
            catalog = json.loads((self.root/'output/Profiles'/profile/'catalog.json').read_text())
            self.assertEqual(catalog['schemaVersion'], 5)
            self.assertEqual(catalog['sequence'], 100)
            self.assertFalse(set(module.EXECUTION_FIELDS) & set(catalog['models'][0]))

    def test_a_schema_five_delivery_naming_an_engine_is_rejected(self):
        self.to_schema_five(engineDigest='sha256:'+'a'*64)
        with self.assertRaisesRegex(ValueError, 'names an engine'):
            self.prepare()
        self.assertFalse((self.root/'output').exists())

    def test_empty_channels_are_resigned_as_schema_five(self):
        self.prepare()
        public = (self.root/'keys/catalog.ed25519.pub').read_bytes()
        output = self.root/'output'
        for directory in [output] + [output/'Profiles'/profile for profile in module.PROFILES]:
            for channel in module.EMPTY_CHANNELS:
                target = directory/'channels'/channel
                catalog = (target/'catalog.json').read_bytes()
                self.assertEqual(json.loads(catalog),
                                 {'schemaVersion': 5, 'sequence': 100, 'channel': channel,
                                  'issuedAt': json.loads(catalog)['issuedAt'], 'models': []})
                module.verify(catalog, (target/'catalog.json.sig').read_bytes(), public)
                envelope = json.loads((target/'catalog.signed.json').read_text())
                self.assertEqual(base64.b64decode(envelope['catalog']), catalog)
                self.assertEqual(base64.b64decode(envelope['signature']),
                                 (target/'catalog.json.sig').read_bytes())

    def signed_channel_fixture(self, profile, channel='stable'):
        source = self.source/'Profiles'/profile
        catalog = json.loads((source/'catalog.json').read_text())
        catalog.update(schemaVersion=5, channel=channel, sequence=42)
        for field in module.EXECUTION_FIELDS:
            catalog['models'][0].pop(field, None)
        directory = source/'channels/stable'
        directory.mkdir(parents=True)
        raw = json.dumps(catalog).encode()
        (directory/'catalog.json').write_bytes(raw)
        self.sign(directory)
        signature = (directory/'catalog.json.sig').read_bytes()
        (directory/'catalog.signed.json').write_text(json.dumps({
            'schema_version': 1, 'catalog': base64.b64encode(raw).decode(),
            'signature': base64.b64encode(signature).decode()}))
        return directory

    def test_reissuing_nightly_preserves_stable_and_uses_gnome_as_root_fallback(self):
        gnome = self.signed_channel_fixture('fedora-gnome')
        kde = self.signed_channel_fixture('fedora-kde')
        self.prepare()
        output = self.root/'output'
        for expected, destination in (
                (gnome, output/'Profiles/fedora-gnome/channels/stable'),
                (kde, output/'Profiles/fedora-kde/channels/stable'),
                (gnome, output/'channels/stable')):
            for name in ('catalog.json', 'catalog.json.sig', 'catalog.signed.json'):
                self.assertEqual((destination/name).read_bytes(), (expected/name).read_bytes())
        self.assertEqual(json.loads((output/'catalog.json').read_text())['channel'], 'testing')

    def test_testing_envelope_in_stable_path_is_rejected(self):
        self.signed_channel_fixture('fedora-gnome', 'testing')
        with self.assertRaisesRegex(ValueError, 'belongs to a different channel'):
            self.prepare()
        self.assertFalse((self.root/'output').exists())

    def test_tampered_stable_signature_is_rejected(self):
        path = self.signed_channel_fixture('fedora-gnome')/'catalog.signed.json'
        envelope = json.loads(path.read_text())
        envelope['signature'] = base64.b64encode(b'\x00'*64).decode()
        path.write_text(json.dumps(envelope))
        with self.assertRaises(subprocess.CalledProcessError):
            self.prepare()
        self.assertFalse((self.root/'output').exists())

    def published_stable_baseline(self):
        profile = 'fedora-gnome'
        tag = 'v0.1.0-alpha.5'
        source = self.root/'published'
        directory = source/'Profiles'/profile
        directory.mkdir(parents=True)
        catalog = json.loads((self.source/'Profiles'/profile/'catalog.json').read_bytes())
        catalog.update(schemaVersion=5, sequence=50)
        catalog['installer']['downloadURL'] = module.INSTALLER_RELEASE + tag
        model = catalog['models'][0]
        for field in module.EXECUTION_FIELDS:
            model.pop(field, None)
        model['payloadDigest'] = 'sha256:'+'c'*64
        (directory/'catalog.json').write_text(json.dumps(catalog))
        self.sign(directory)
        return source, directory, tag, catalog

    def test_prepares_stable_from_an_explicit_published_baseline(self):
        source, directory, tag, original = self.published_stable_baseline()
        self.prepare(stable_source=source, stable_release_tag=tag, stable_source_revision='b'*40)
        output = self.root/'output'
        stable = output/'Profiles/fedora-gnome/channels/stable'
        raw = (stable/'catalog.json').read_bytes()
        catalog = json.loads(raw)
        self.assertEqual(catalog['channel'], 'stable')
        self.assertEqual(catalog['sequence'], 100)
        self.assertEqual(catalog['models'], original['models'])
        self.assertEqual(catalog['installer'], original['installer'])
        self.assertEqual((output/'channels/stable/catalog.signed.json').read_bytes(),
                         (stable/'catalog.signed.json').read_bytes())
        self.assertEqual(json.loads((output/'Profiles/fedora-kde/channels/stable/catalog.json').read_bytes())['models'], [])
        self.assertEqual(json.loads((output/'catalog.json').read_bytes())['models'][0]['payloadDigest'], 'sha256:'+'b'*64)
        module.verify(raw, (stable/'catalog.json.sig').read_bytes(),
                      (self.root/'keys/catalog.ed25519.pub').read_bytes())
        receipt = json.loads((output/'native-only-catalogs.json').read_bytes())['stable_baseline']
        self.assertEqual(receipt['release_tag'], tag)
        self.assertEqual(receipt['source_revision'], 'b'*40)
        self.assertEqual(receipt['profiles']['fedora-gnome']['source_catalog_sha256'],
                         hashlib.sha256((directory/'catalog.json').read_bytes()).hexdigest())

    def test_a_stable_baseline_with_a_different_signed_release_is_rejected(self):
        source, _, _, _ = self.published_stable_baseline()
        with self.assertRaisesRegex(ValueError, 'differs from the selected native installer release'):
            self.prepare(stable_source=source, stable_release_tag='v0.1.0-alpha.4', stable_source_revision='b'*40)
        self.assertFalse((self.root/'output').exists())

    def test_a_stable_baseline_requires_a_main_revision_and_release_tag(self):
        source, _, tag, _ = self.published_stable_baseline()
        with self.assertRaisesRegex(ValueError, 'requires a source directory, revision and immutable installer tag'):
            self.prepare(stable_source=source, stable_release_tag=tag)
        self.assertFalse((self.root/'output').exists())

    def test_tampered_source_is_rejected_before_output(self):
        path = self.source/'Profiles/nixos-minimal/catalog.json'
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaises(subprocess.CalledProcessError):
            self.prepare()
        self.assertFalse((self.root/'output').exists())

    def rewrite(self, template, identifiers):
        """Re-sign every profile for IDENTIFIERS sharing one metadata TEMPLATE."""
        for name in ('supported-models.json', 'supported-models-65.json'):
            (self.root/'keys'/name).write_text(json.dumps({'supported': identifiers}))
        self.metadata = json.dumps({'os_list': [template]}).encode()
        for profile in module.PROFILES:
            directory = self.source/'Profiles'/profile
            catalog = json.loads((directory/'catalog.json').read_text())
            model = dict(catalog['models'][0])
            model['metadataDigest'] = 'sha256:'+hashlib.sha256(self.metadata).hexdigest()
            catalog['models'] = [dict(model, deviceIdentifier=identifier)
                                 for identifier in identifiers]
            (directory/'catalog.json').write_text(json.dumps(catalog))
            self.sign(directory)

    def neo_template(self, **fields):
        template = {'aurora_stage1': {'kind': 'common-raw-v1', 'qualified_machines': {
                        'j274ap': ['26.6.2'], 'j700ap': ['26.6.2']}},
                    'aurora_stage2': {'kind': 'board-file-v1', 'board_members': {
                        'j274ap': 'esp/aurora/boot-j274.bin', 'j700ap': 'esp/aurora/boot-j700.bin'}}}
        template.update(fields)
        return template

    def test_the_neo_is_admitted_with_its_own_stage_two(self):
        self.rewrite(self.neo_template(), ['apple,j274', 'apple,j700'])
        self.prepare()
        for profile in module.PROFILES:
            catalog = json.loads((self.root/'output/Profiles'/profile/'catalog.json').read_text())
            self.assertEqual({model['deviceIdentifier'] for model in catalog['models']},
                             {'apple,j274', 'apple,j700'})

    def test_a_template_that_refuses_a_supported_board_is_rejected_before_output(self):
        for template in (
            self.neo_template(excluded_device_classes=['j700ap']),
            self.neo_template(allowed_device_classes=['j700ap']),
            self.neo_template(aurora_stage2={'kind': 'board-file-v1', 'board_members': {
                'j274ap': 'esp/aurora/boot-j274.bin'}}),
            {key: value for key, value in self.neo_template().items() if key != 'aurora_stage2'},
        ):
            self.rewrite(template, ['apple,j274', 'apple,j700'])
            with self.assertRaisesRegex(ValueError, 'does not admit every supported board'):
                self.prepare()
            self.assertFalse((self.root/'output').exists())

    def test_a_stage_one_unqualified_board_may_be_left_out_of_qualification(self):
        template = self.neo_template()
        self.rewrite(template, ['apple,j274', 'apple,j700', 'apple,j316s'])
        manifest = {'supported': ['apple,j274', 'apple,j700', 'apple,j316s']}
        (self.root/'keys/supported-models.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'lacks native Stage 1 policy'):
            self.prepare()
        self.assertFalse((self.root/'output').exists())
        manifest['stage1_unqualified'] = {'apple,j316s': 'iBoot refuses the common Stage 1'}
        (self.root/'keys/supported-models.json').write_text(json.dumps(manifest))
        self.prepare()

    def test_the_j616s_needs_its_own_stage_two(self):
        template = self.neo_template()
        template['aurora_stage1']['qualified_machines']['j616sap'] = ['26.6.2']
        self.rewrite(template, ['apple,j274', 'apple,j700', 'apple,j616s'])
        with self.assertRaisesRegex(ValueError, 'does not admit every supported board'):
            self.prepare()
        self.assertFalse((self.root/'output').exists())
        template['aurora_stage2']['board_members']['j616sap'] = 'esp/aurora/boot-j616s.bin'
        self.rewrite(template, ['apple,j274', 'apple,j700', 'apple,j616s'])
        self.prepare()

    def needs_its_own_stage_two(self, board):
        template = self.neo_template()
        template['aurora_stage1']['qualified_machines'][board + 'ap'] = ['26.6.2']
        identifiers = ['apple,j274', 'apple,j700', 'apple,' + board]
        (self.root/'keys/supported-models.json').write_text(json.dumps({'supported': identifiers}))
        self.rewrite(template, identifiers)
        with self.assertRaisesRegex(ValueError, 'does not admit every supported board'):
            self.prepare()
        self.assertFalse((self.root/'output').exists())
        template['aurora_stage2']['board_members'][board + 'ap'] = 'esp/aurora/boot-%s.bin' % board
        self.rewrite(template, identifiers)
        self.prepare()

    def test_the_j614s_needs_its_own_stage_two(self):
        self.needs_its_own_stage_two('j614s')

    def test_the_m4_macbook_air_needs_its_own_stage_two(self):
        self.needs_its_own_stage_two('j713')

    def test_the_m4_max_mac_studio_needs_its_own_stage_two(self):
        self.needs_its_own_stage_two('j575c')

    def test_the_m6_mac_mini_needs_its_own_stage_two(self):
        self.needs_its_own_stage_two('j873g')

    def test_the_m6_macbook_pro_needs_its_own_stage_two(self):
        self.needs_its_own_stage_two('j804')

    def test_the_m6_imac_needs_its_own_stage_two(self):
        self.needs_its_own_stage_two('j833ct')

    def test_the_14_inch_m5_macbook_pro_needs_its_own_stage_two(self):
        self.needs_its_own_stage_two('j704')

    def test_the_15_inch_m5_macbook_air_needs_its_own_stage_two(self):
        self.needs_its_own_stage_two('j815')

    def test_a_payload_built_before_a_board_was_unqualified_still_adopts(self):
        template = self.neo_template()
        template['aurora_stage1']['qualified_machines']['j316sap'] = ['26.6.2']
        self.rewrite(template, ['apple,j274', 'apple,j700', 'apple,j316s'])
        (self.root/'keys/supported-models.json').write_text(json.dumps({
            'supported': ['apple,j274', 'apple,j700', 'apple,j316s'],
            'stage1_unqualified': {'apple,j316s': 'iBoot refuses the common Stage 1'}}))
        self.prepare()

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
