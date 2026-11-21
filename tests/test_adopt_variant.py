import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

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

    def invoke(self, provenance='verified', tag='payload-fedora-kde-42508499173-models58',
               repository='aurora-silicon/fedora-aurora', channel='testing'):
        raw = json.dumps(self.catalog).encode()
        path = self.root / 'source.json'
        path.write_bytes(raw)
        subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(self.key), '-rawin',
                        '-in', str(path), '-out', str(self.root / 'source.sig')], check=True)
        data = {name: json.dumps(value).encode() for name, value in self.json.items()}
        data.update({'catalog.json': raw, 'catalog.json.sig': (self.root / 'source.sig').read_bytes(),
                     'trust-root.ed25519.pub': self.public})
        # Assets whose exact bytes a digest covers, such as the detached Edge device records.
        data.update(getattr(self, 'raw', {}))
        def opener(url, timeout):
            name = url.rsplit('/', 1)[1]
            if name not in data:
                raise urllib.error.HTTPError(url, 404, 'Not Found', None, None)
            return io.BytesIO(data[name])
        with patch.object(adopter, 'ROOT', self.root), \
             patch.object(adopter, 'verify_provenance', return_value=provenance), \
             patch.object(adopter.urllib.request, 'urlopen', side_effect=opener):
            adopter.adopt(repository, tag, self.root / 'site', channel=channel)

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
        with self.assertRaisesRegex(ValueError, 'requires hosted provenance'):
            self.invoke(provenance='none (delivery predates attestations)')

    def test_variant_cannot_drop_a_supported_model(self):
        self.catalog['models'].pop()
        with self.assertRaisesRegex(ValueError, 'model coverage'):
            self.invoke()


class OmarchyTests(unittest.TestCase):
    def setUp(self):
        VariantTests.setUp(self)
        self.repository = 'aurora-silicon/omarchy-aurora'
        self.profile = 'omarchy-hyprland'
        for model in self.catalog['models']:
            model['installationProfile'] = self.profile
        for channel, record in self.json['release.json']['channels'].items():
            record['catalog_url'] = ('https://aurorasilicon.org/aurora-distributions/installer/Profiles/'
                                     + self.profile + '/channels/' + channel + '/catalog.signed.json')
        self.json['delivery.json']['installation_profile'] = self.profile
        self.json['delivery.json']['release_id'] = 'build-42508499173'
        self.json['image-build.json']['head_repository']['full_name'] = self.repository
        self.json['vm-result.json'] = dict.fromkeys(adopter.VM_CHECKS, True)
        (self.root / 'site/installer/profile-builds.json').write_text(json.dumps(
            {'profiles': [{'profile': self.profile}]}))

    def invoke(self, provenance='verified', repository=None, tag=None):
        VariantTests.invoke(self, provenance=provenance, repository=repository or self.repository,
                            tag=tag or 'payload-omarchy-hyprland-42508499173')

    def test_complete_vm_candidate_is_adopted(self):
        self.invoke()
        directory = self.root / 'site/installer/Profiles' / self.profile
        self.assertEqual(json.loads((directory / 'catalog.json').read_text()), self.catalog)
        self.assertEqual(json.loads((directory / 'vm-result.json').read_text()), self.json['vm-result.json'])

    def test_each_missing_or_nonboolean_vm_check_is_refused(self):
        for name in adopter.VM_CHECKS:
            for value in (None, False, 'true', 1):
                with self.subTest(check=name, value=value):
                    self.json['vm-result.json'] = dict.fromkeys(adopter.VM_CHECKS, True)
                    self.json['vm-result.json'][name] = value
                    with self.assertRaisesRegex(ValueError, 'complete passing VM'):
                        self.invoke()

    def test_omarchy_requires_new_guest_package_trust_evidence(self):
        complete = dict.fromkeys(adopter.VM_CHECKS, True)
        for missing in (True, False):
            with self.subTest(missing=missing):
                self.json['vm-result.json'] = dict(complete)
                if missing:
                    self.json['vm-result.json'].pop('package_trust')
                else:
                    self.json['vm-result.json']['package_trust'] = False
                with self.assertRaisesRegex(ValueError, 'complete passing VM'):
                    self.invoke()

    def test_omarchy_requires_hosted_provenance(self):
        with self.assertRaisesRegex(ValueError, 'requires hosted provenance'):
            self.invoke(provenance='none (delivery predates attestations)')

    def test_profile_cannot_be_published_by_another_repository(self):
        with self.assertRaisesRegex(ValueError, 'immutable Aurora profile'):
            self.invoke(repository='aurora-silicon/alarm-aurora')
        with self.assertRaisesRegex(ValueError, 'immutable Aurora profile'):
            self.invoke(tag='payload-omarchy-kde-42508499173')
        with self.assertRaisesRegex(ValueError, 'immutable Aurora profile'):
            self.invoke(tag='payload-omarchy-hyprland-42508499173-models58')


class OmarchyPreSignTests(unittest.TestCase):
    def test_guest_package_trust_is_required_before_the_signing_job(self):
        workflow = (ROOT / '.github/workflows/build-payload.yml').read_text()
        block = re.search(r"python3 - <<'PYVM'\n(.*?)\n          PYVM", workflow, re.S)[1]
        script = '\n'.join(line[10:] for line in block.splitlines())
        sign_job = workflow.split('  sign:\n', 1)[1].split('  publish:', 1)[0]
        self.assertIn('needs: payload', sign_job)
        self.assertNotIn('always()', sign_job)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / 'downloaded/out/vm-result.json'
            path.parent.mkdir(parents=True)
            complete = dict.fromkeys(adopter.VM_CHECKS, True)
            for state in ('passed', 'missing', 'failed'):
                with self.subTest(state=state):
                    evidence = dict(complete)
                    if state == 'missing':
                        evidence.pop('package_trust')
                    elif state == 'failed':
                        evidence['package_trust'] = False
                    path.write_text(json.dumps(evidence))
                    result = subprocess.run([sys.executable, '-c', script], cwd=directory,
                                            capture_output=True, text=True)
                    if state == 'passed':
                        self.assertEqual(result.returncode, 0, result.stderr)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn('guest package trust', result.stderr)


if __name__ == '__main__':
    unittest.main()


class EdgeTests(unittest.TestCase):
    def setUp(self):
        VariantTests.setUp(self)
        self.tag = f'payload-fedora-kde-{adopter.neo_edge.IMAGE_RUN}-edge-alpha.2'
        self.base = 'https://github.com/aurora-silicon/fedora-aurora/releases/download/' + self.tag
        edge = adopter.neo_edge
        run = self.json['image-build.json']
        run.update(id=edge.IMAGE_RUN, status='completed', event='push', head_branch=edge.IMAGE_BRANCH,
                   path=edge.IMAGE_WORKFLOW, head_sha=edge.IMAGE_HEAD)
        proof = {'schema': 1, 'fedora': 44, 'profile': edge.PROFILE, 'models': [edge.MODEL],
                  'signing_run': edge.SIGNING_RUN, 'replacement_signing_run': edge.MESA_SIGNING_RUN,
                 'replacement_mesa': edge.SOURCES['mesa']['revision'], 'files': edge.FILES,
                 'installed_edge_packages': {
                     key: {'filename': filename, 'sha256': 'd'*64, 'main_header_and_payload_sha256': 'e'*64}
                     for key, filename in edge.REQUIRED_PACKAGES.items()}}
        self.json['edge-image-proof.json'] = proof
        metadata = {"os_list": [{"allowed_device_classes": ["j700ap"],
                                 "aurora_stage1": {"qualified_machines": {"j700ap": ["27.2"]}},
                                 "aurora_stage2": {"board_members": {"j700ap": "esp/m1n1/boot-j700.bin"}}}]}
        raw_metadata = json.dumps(metadata).encode()
        self.json['installer-data-fedora-kde.json'] = metadata
        for model in self.catalog['models']:
            model.update(payloadDigest='sha256:'+'b'*64,
                         payloadArtifact={'sourceURL': self.base+'/os.zip',
                                          'fileName': 'os.zip', 'sizeBytes': 123},
                         metadataDigest=edge.digest(raw_metadata),
                         metadataArtifact={'sourceURL': self.base+'/installer-data-fedora-kde.json',
                                           'fileName': 'installer-data-fedora-kde.json', 'sizeBytes': len(raw_metadata)})
        receipt = self.json['delivery.json']
        receipt.update(release_id=f'build-{edge.IMAGE_RUN}-edge-alpha.2',
                       artifacts={'os.zip': {'bytes': 123, 'sha256': 'b'*64}})
        gates = {'pair': edge.EVIDENCE_PAIR.copy(),
                 'checks': {gate: {'status': 'passed', 'sha256': 'sha256:'+'f'*64} for gate in edge.GATES}}
        self.template, self.gates = copy.deepcopy(self.catalog), copy.deepcopy(gates)
        raw = edge.bind(self.catalog, receipt, run, json.dumps(proof).encode(), gates)
        self.json['edge-qualification.json'] = json.loads(raw)
        self.catalog['edgeQualification']['evidenceDigest'] = edge.digest(
            json.dumps(self.json['edge-qualification.json']).encode())
        self.raw = edge.device_evidence(self.catalog)

    def invoke(self, provenance='verified', tag=None, repository='aurora-silicon/fedora-aurora'):
        with patch.object(adopter, 'verify_edge_source_refs', return_value=None):
            VariantTests.invoke(self, provenance=provenance, tag=tag or self.tag,
                                repository=repository, channel='edge')

    def test_a_qualified_edge_delivery_does_not_replace_testing(self):
        profile = self.root/'site/installer/Profiles/fedora-kde'
        profile.mkdir(parents=True)
        original = json.dumps({'channel': 'testing', 'models': [{'payloadDigest': 'sha256:'+'a'*64}]}).encode()
        (profile/'catalog.json').write_bytes(original)
        self.invoke()
        self.assertEqual((profile/'catalog.json').read_bytes(), original)
        edge = json.loads((profile/'channels/edge-alpha/catalog.json').read_text())
        self.assertEqual(edge['channel'], 'edge')
        self.assertFalse((profile/'channels/edge').exists())
        channel = profile/'channels/edge-alpha'
        fallback = self.root/'site/installer/channels/edge-alpha'
        for name in ('catalog.signed.json', 'edge-qualification.json'):
            self.assertEqual((fallback/name).read_bytes(), (channel/name).read_bytes())

    def test_an_existing_testing_tag_cannot_be_adopted_as_edge(self):
        with self.assertRaisesRegex(ValueError, 'never testing promotion'):
            self.invoke(tag='payload-fedora-kde-44380859872-models58')

    def test_an_unqualified_source_cannot_enter_edge(self):
        self.catalog['edgeQualification']['mesa']['branch'] = 'testing'
        with self.assertRaisesRegex(ValueError, 'aurora-edge provenance'):
            self.invoke()


    def test_main_image_run_is_not_neo_edge_provenance(self):
        self.json['image-build.json']['head_branch'] = 'main'
        with self.assertRaisesRegex(ValueError, 'completed Fedora Neo Edge image run'):
            self.invoke()

    def test_changed_image_proof_is_rejected(self):
        self.json['edge-image-proof.json']['files'] = {'replacement': 'a'*64}
        with self.assertRaisesRegex(ValueError, 'Neo-smoked file'):
            self.invoke()

    def test_wider_model_rows_are_rejected(self):
        self.catalog['models'].append(dict(self.catalog['models'][0], deviceIdentifier='apple,j813'))
        with self.assertRaisesRegex(ValueError, 'catalog model coverage'):
            self.invoke()

    def test_missing_cts_receipt_refuses_to_bind(self):
        unsigned = copy.deepcopy(self.catalog)
        del unsigned['channel']
        del unsigned['edgeQualification']
        with self.assertRaisesRegex(ValueError, 'full Vulkan CTS'):
            adopter.neo_edge.bind(unsigned, self.json['delivery.json'], self.json['image-build.json'],
                                  json.dumps(self.json['edge-image-proof.json']).encode(), {})


    def test_testing_metadata_cannot_replace_neo_metadata(self):
        raw = self.json['installer-data-fedora-kde.json']['os_list'][0]
        raw['allowed_device_classes'].append('j813ap')
        with self.assertRaisesRegex(ValueError, 'Neo-only install policy'):
            self.invoke()

    def test_old_pair_evidence_cannot_bind_the_replacement(self):
        evidence = copy.deepcopy(self.catalog['edgeQualification']['neoEvidence'])
        evidence['pair']['mesaDsoSHA256'] = 'sha256:' + 'a' * 64
        with self.assertRaisesRegex(ValueError, 'full Vulkan CTS'):
            adopter.neo_edge.verify_evidence(evidence)

    # Per-device evidence (aurora-installer#176). EdgeM5Tests runs each of these with the J714s on.

    def rebind_report(self):
        """Re-serve the detached report after a test edits the signed qualification."""
        proof = self.catalog['edgeQualification']
        self.json['edge-qualification.json'] = {k: v for k, v in proof.items() if k != 'evidenceDigest'}
        proof['evidenceDigest'] = adopter.neo_edge.digest(json.dumps(self.json['edge-qualification.json']).encode())

    def test_devices_digest_each_edge_macs_detached_record(self):
        edge = adopter.neo_edge
        devices = self.catalog['edgeQualification']['devices']
        self.assertEqual(list(devices), edge.models())
        self.assertEqual(sorted(self.raw), sorted('edge-device-' + model.replace(',', '-') + '.json'
                                                  for model in edge.models()))
        rows = {edge.MODEL: self.gates} if not edge.M5 else self.gates
        for model in edge.models():
            raw = self.raw[edge.device_record_name(model)]
            self.assertEqual(devices[model], {'evidenceDigest': 'sha256:' + hashlib.sha256(raw).hexdigest()})
            self.assertTrue(raw.endswith(b'}\n'))
            self.assertEqual(json.loads(raw), {
                'schemaVersion': 1, 'deviceIdentifier': model, 'family': edge.FAMILIES[model],
                'machine': model, 'pair': rows[model]['pair'], 'checks': rows[model]['checks'],
                'linux': edge.SOURCES['linux'], 'mesa': edge.SOURCES['mesa']})
        # The report the Alpha 22 installer reads is unchanged.
        self.assertEqual(self.catalog['edgeQualification']['neoEvidence'], rows[edge.MODEL])
        self.assertEqual('m5Evidence' in self.catalog['edgeQualification'], edge.M5)

    def test_adoption_serves_every_device_record_and_drops_stale_ones(self):
        channel = self.root/'site/installer/Profiles/fedora-kde/channels/edge-alpha'
        fallback = self.root/'site/installer/channels/edge-alpha'
        for directory in (channel, fallback):
            directory.mkdir(parents=True)
            (directory/'edge-device-apple-j716c.json').write_bytes(b'{}\n')
        self.invoke()
        for directory in (channel, fallback):
            self.assertEqual({path.name: path.read_bytes() for path in directory.glob('edge-device-*.json')},
                             self.raw)

    def test_the_adopted_site_passes_the_served_channel_check(self):
        spec = importlib.util.spec_from_file_location('verify_channels_for_neo_edge',
                                                      ROOT / 'scripts/verify-channel-catalogs.py')
        channels = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(channels)
        self.invoke()
        self.assertEqual(channels.verify_channels(self.root/'site', self.root/'keys/catalog.ed25519.pub'), 2)

    def test_a_tampered_device_record_is_refused(self):
        for model in adopter.neo_edge.models():
            with self.subTest(model=model):
                name = adopter.neo_edge.device_record_name(model)
                original = self.raw[name]
                self.raw[name] = original.replace(b'"passed"', b'"failed"', 1)
                with self.assertRaisesRegex(ValueError, 'missing or altered device evidence: ' + model):
                    self.invoke()
                self.raw[name] = original

    def test_an_unpublished_device_record_is_refused(self):
        del self.raw[adopter.neo_edge.device_record_name(adopter.neo_edge.models()[-1])]
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.invoke()
        caught.exception.close()

    def test_a_missing_device_is_refused(self):
        for model in adopter.neo_edge.models():
            with self.subTest(model=model):
                devices = self.catalog['edgeQualification']['devices']
                entry = devices.pop(model)
                self.rebind_report()
                with self.assertRaisesRegex(ValueError, 'per-device evidence differs'):
                    self.invoke()
                devices[model] = entry
                self.rebind_report()

    def test_an_extra_device_is_refused(self):
        extra = 'apple,j716c' if adopter.neo_edge.M5 else 'apple,j714s'
        record = b'{"deviceIdentifier": "' + extra.encode() + b'"}\n'
        self.raw[adopter.neo_edge.device_record_name(extra)] = record
        self.catalog['edgeQualification']['devices'][extra] = {'evidenceDigest': adopter.neo_edge.digest(record)}
        self.rebind_report()
        with self.assertRaisesRegex(ValueError, 'per-device evidence differs'):
            self.invoke()

    def test_a_record_for_an_unlisted_mac_is_refused(self):
        catalog = copy.deepcopy(self.catalog)
        catalog['models'] = catalog['models'][:-1]
        with self.assertRaisesRegex(ValueError, 'has not listed: ' + adopter.neo_edge.models()[-1]):
            adopter.neo_edge.verify_devices(catalog, self.raw)

    def test_a_record_naming_another_device_is_refused(self):
        edge = adopter.neo_edge
        model = edge.models()[-1]
        record = json.loads(self.raw[edge.device_record_name(model)])
        record['deviceIdentifier'] = 'apple,j716c'
        raw = (json.dumps(record, sort_keys=True, indent=2) + '\n').encode()
        catalog = copy.deepcopy(self.catalog)
        catalog['edgeQualification']['devices'][model] = {'evidenceDigest': edge.digest(raw)}
        with self.assertRaisesRegex(ValueError, 'names another device'):
            edge.verify_devices(catalog, dict(self.raw, **{edge.device_record_name(model): raw}))

    def test_the_binder_writes_the_device_records(self):
        edge = adopter.neo_edge
        assets = self.root/'assets'
        assets.mkdir()
        files = {'catalog-template.json': self.template, 'delivery.json': self.json['delivery.json'],
                 'image-build.json': self.json['image-build.json'],
                 'edge-image-proof.json': self.json['edge-image-proof.json'],
                 'installer-data-fedora-kde.json': self.json['installer-data-fedora-kde.json']}
        for name, value in files.items():
            (assets/name).write_bytes(json.dumps(value).encode())
        (self.root/'evidence.json').write_text(json.dumps(self.gates))
        with patch.object(sys, 'argv', ['neo_edge.py', '--assets', str(assets),
                                         '--evidence', str(self.root/'evidence.json')]):
            edge.main()
        catalog = json.loads((assets/'catalog.json').read_text())
        records = {path.name: path.read_bytes() for path in assets.glob('edge-device-*.json')}
        self.assertEqual(records, edge.device_evidence(catalog))
        self.assertEqual(list(catalog['edgeQualification']['devices']), edge.models())
        edge.verify_devices(catalog, records)

    def test_the_payload_workflow_publishes_the_device_records(self):
        workflow = (ROOT / '.github/workflows/build-payload.yml').read_text()
        binder = workflow.split('- name: Bind the staged Neo payload to the owner receipts', 1)[1].split('- id: draft', 1)[0]
        self.assertIn('devices = device_evidence(catalog)', binder)
        self.assertIn('verify_devices(catalog, devices)', binder)
        self.assertIn('(assets / name).write_bytes(record)', binder)


class EdgeM5Tests(EdgeTests):
    """neo_edge.M5: the M5 Pro (J714s) on the same Fedora KDE Edge payload as the Neo."""

    def setUp(self):
        patcher = patch.object(adopter.neo_edge, 'M5', True)
        patcher.start()
        self.addCleanup(patcher.stop)
        VariantTests.setUp(self)
        edge = adopter.neo_edge
        self.catalog['models'].append({'deviceIdentifier': edge.M5_MODEL, 'installationProfile': 'fedora-kde',
                                       'status': 'enabled'})
        self.tag = f'payload-fedora-kde-{edge.IMAGE_RUN}-edge-alpha.1'
        self.base = 'https://github.com/aurora-silicon/fedora-aurora/releases/download/' + self.tag
        run = self.json['image-build.json']
        run.update(id=edge.IMAGE_RUN, status='completed', event='push', head_branch=edge.IMAGE_BRANCH,
                   path=edge.IMAGE_WORKFLOW, head_sha=edge.IMAGE_HEAD)
        proof = {'schema': 1, 'fedora': 44, 'profile': edge.PROFILE, 'models': [edge.MODEL, edge.M5_MODEL],
                 'signing_run': edge.SIGNING_RUN, 'replacement_signing_run': edge.MESA_SIGNING_RUN,
                 'replacement_mesa': edge.SOURCES['mesa']['revision'], 'files': edge.FILES,
                 'installed_edge_packages': {
                     key: {'filename': filename, 'sha256': 'd'*64, 'main_header_and_payload_sha256': 'e'*64}
                     for key, filename in edge.REQUIRED_PACKAGES.items()}}
        self.json['edge-image-proof.json'] = proof
        metadata = {"os_list": [{"allowed_device_classes": ["j700ap", "j714sap"],
                                 "aurora_stage1": {"qualified_machines": {"j700ap": ["27.2"], "j714sap": ["27.2"]}},
                                 "aurora_stage2": {"board_members": {"j700ap": "esp/m1n1/boot-j700.bin",
                                                                     "j714sap": "esp/m1n1/boot-j714s.bin"}}}]}
        raw_metadata = json.dumps(metadata).encode()
        self.json['installer-data-fedora-kde.json'] = metadata
        for model in self.catalog['models']:
            model.update(payloadDigest='sha256:'+'b'*64,
                         payloadArtifact={'sourceURL': self.base+'/os.zip',
                                          'fileName': 'os.zip', 'sizeBytes': 123},
                         metadataDigest=edge.digest(raw_metadata),
                         metadataArtifact={'sourceURL': self.base+'/installer-data-fedora-kde.json',
                                           'fileName': 'installer-data-fedora-kde.json', 'sizeBytes': len(raw_metadata)})
        receipt = self.json['delivery.json']
        receipt.update(release_id=f'build-{edge.IMAGE_RUN}-edge-alpha.1',
                       artifacts={'os.zip': {'bytes': 123, 'sha256': 'b'*64}})
        passed = {'status': 'passed', 'sha256': 'sha256:'+'f'*64}
        self.gates = {
            edge.MODEL: {'pair': edge.EVIDENCE_PAIR.copy(), 'checks': {gate: dict(passed) for gate in edge.GATES}},
            edge.M5_MODEL: {'pair': edge.EVIDENCE_PAIR.copy(), 'checks': {gate: dict(passed) for gate in edge.M5_GATES}},
        }
        self.template = copy.deepcopy(self.catalog)
        raw = edge.bind(self.catalog, receipt, run, json.dumps(proof).encode(), copy.deepcopy(self.gates))
        self.json['edge-qualification.json'] = json.loads(raw)
        self.catalog['edgeQualification']['evidenceDigest'] = edge.digest(
            json.dumps(self.json['edge-qualification.json']).encode())
        self.raw = edge.device_evidence(self.catalog)

    def test_both_models_share_one_qualified_payload(self):
        self.invoke()
        channel = self.root/'site/installer/Profiles/fedora-kde/channels/edge-alpha'
        edge = json.loads((channel/'catalog.json').read_text())
        self.assertEqual([m['deviceIdentifier'] for m in edge['models']], ['apple,j700', 'apple,j714s'])
        report = json.loads((channel/'edge-qualification.json').read_text())
        self.assertEqual(set(report['m5Evidence']['checks']), adopter.neo_edge.M5_GATES)

    def test_wider_model_rows_are_rejected(self):
        self.catalog['models'].append(dict(self.catalog['models'][0], deviceIdentifier='apple,j716c'))
        with self.assertRaisesRegex(ValueError, 'catalog model coverage'):
            self.invoke()

    def test_testing_metadata_cannot_replace_neo_metadata(self):
        raw = self.json['installer-data-fedora-kde.json']['os_list'][0]
        raw['allowed_device_classes'] = ['j700ap']
        with self.assertRaisesRegex(ValueError, 'Neo-only install policy'):
            self.invoke()

    def test_old_pair_evidence_cannot_bind_the_replacement(self):
        evidence = copy.deepcopy(self.gates)
        evidence['apple,j714s']['pair']['kernelImageSHA256'] = 'sha256:' + 'a' * 64
        with self.assertRaisesRegex(ValueError, 'M5 Pro release gate'):
            adopter.neo_edge.verify_evidence(evidence)

    def test_missing_cts_receipt_refuses_to_bind(self):
        unsigned = copy.deepcopy(self.catalog)
        del unsigned['channel']
        del unsigned['edgeQualification']
        with self.assertRaisesRegex(ValueError, 'Neo and the M5 Pro evidence'):
            adopter.neo_edge.bind(unsigned, self.json['delivery.json'], self.json['image-build.json'],
                                  json.dumps(self.json['edge-image-proof.json']).encode(), {})

    def test_the_neo_evidence_alone_is_not_enough(self):
        with self.assertRaisesRegex(ValueError, 'Neo and the M5 Pro evidence'):
            adopter.neo_edge.verify_evidence(self.gates['apple,j700'])

    def test_a_failed_m5_gate_refuses(self):
        evidence = copy.deepcopy(self.gates)
        evidence['apple,j714s']['checks']['deepSleep']['status'] = 'failed'
        with self.assertRaisesRegex(ValueError, 'Missing passing M5 Pro evidence: deepSleep'):
            adopter.neo_edge.verify_evidence(evidence)

    def test_an_image_without_the_m5_is_refused(self):
        self.json['edge-image-proof.json']['models'] = ['apple,j700']
        with self.assertRaisesRegex(ValueError, 'completed Fedora Neo Edge image run|Neo-smoked file'):
            self.invoke()


@unittest.skipUnless((ROOT / 'scripts/edge_train.py').is_file(), 'the Edge train (aurora-distributions#64) is not merged')
class EdgeTrainDeviceRecordTests(unittest.TestCase):
    """The single-profile Neo and M5 records are byte for byte the Edge train's records."""

    def test_neo_and_m5_records_equal_the_train_records(self):
        import edge_train  # scripts/ is on sys.path through adopt-delivery.py
        edge = adopter.neo_edge
        cohort = edge_train.load(edge_train.COHORT)
        train = {'riders': ['t6050'], 'pair': copy.deepcopy(edge.SOURCES), 'reference': dict(edge.EVIDENCE_PAIR)}
        self.assertEqual({edge.FAMILIES[edge.MODEL]: [edge.MODEL], edge.FAMILIES[edge.M5_MODEL]: [edge.M5_MODEL]},
                         {name: spec['models'] for name, spec in edge_train.qualified_families(cohort, train).items()
                          if name in edge.FAMILIES.values()})
        families = edge_train.qualified_families(cohort, train, edge.PROFILE)
        self.assertEqual(set(families['t8140']['gates']), edge.GATES)
        self.assertEqual(set(families['t6050']['gates']), edge.M5_GATES)
        passed = {'status': 'passed', 'sha256': 'sha256:' + 'f' * 64}
        rows = {name: {'machine': spec['machines'][0], 'pair': dict(edge.EVIDENCE_PAIR),
                       'checks': {gate: dict(passed) for gate in spec['gates']}}
                for name, spec in families.items()}
        expected = edge_train._device_records(rows, cohort, train, edge.PROFILE)
        gates = {model: {key: rows[edge.FAMILIES[model]][key] for key in ('pair', 'checks')}
                 for model in (edge.MODEL, edge.M5_MODEL)}
        for m5 in (False, True):
            with self.subTest(m5=m5), patch.object(edge, 'M5', m5):
                records = edge.device_records(gates if m5 else gates[edge.MODEL])
                self.assertEqual(list(records), edge.models())
                for model, raw in records.items():
                    self.assertEqual(raw, expected[model])
                    self.assertEqual(edge.device_record_name(model), edge_train.device_record_name(model))
