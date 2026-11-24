import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    'channel_policy_tests', Path(__file__).resolve().parents[1]/'scripts/channel_policy.py')
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


class ChannelPolicyTests(unittest.TestCase):
    def setUp(self):
        self.base = ('https://github.com/aurora-silicon/fedora-aurora/releases/download/'
                     'payload-fedora-kde-41-edge-alpha.1')
        self.report = {
            'schemaVersion': 1, 'releaseKind': 'alpha', 'status': 'passed', 'imageRunID': 41,
            'payloadDigest': 'sha256:'+'b'*64,
            **{name: {'repository': 'aurora-silicon/'+name, 'branch': 'aurora-edge',
                      'revision': 'a'*40, 'tree': 'c'*40} for name in ('linux', 'mesa')}}
        self.raw = json.dumps(self.report, sort_keys=True).encode()
        proof = dict(self.report, evidenceDigest='sha256:'+hashlib.sha256(self.raw).hexdigest())
        self.catalog = {'schemaVersion': 5, 'channel': 'edge', 'edgeQualification': proof,
                        'models': [{'payloadDigest': self.report['payloadDigest'],
                                    'payloadArtifact': {'fileName': 'os.zip', 'sizeBytes': 123,
                                                        'sourceURL': self.base+'/os.zip'},
                                    'metadataArtifact': {'fileName': 'metadata.json',
                                                        'sourceURL': self.base+'/metadata.json'}}]}
        self.receipt = {'release_channel': 'edge', 'release_kind': 'alpha',
                        'packaged_sources': {name: self.report[name] for name in ('linux', 'mesa')},
                        'artifacts': {'os.zip': {'bytes': 123, 'sha256': 'b'*64}}}

    def verify(self, catalog=None, raw=None, receipt=None, testing=None):
        policy.verify_edge_delivery(catalog or self.catalog, raw or self.raw,
                                    receipt or self.receipt, {'id': 41}, self.base, testing)

    def test_legacy_development_descriptor_routes_testing_and_reserves_distinct_edge(self):
        source = {'schema_version': 3, 'default_channel': 'edge',
                  'channels': {name: {'catalog_url': 'https://example.invalid/channels/'+name
                                      +'/catalog.signed.json'} for name in ('stable', 'rc', 'edge')}}
        result = json.loads(policy.descriptor(json.dumps(source).encode()))
        self.assertEqual(result['default_channel'], 'testing')
        self.assertEqual(set(result['channels']), set(policy.CHANNELS))
        self.assertTrue(result['channels']['edge']['catalog_url'].endswith(
            '/channels/edge-alpha/catalog.signed.json'))
        self.assertTrue(result['channels']['testing']['catalog_url'].endswith(
            '/channels/testing/catalog.signed.json'))

    def test_edge_cannot_be_the_new_descriptor_default(self):
        source = {'schema_version': 3, 'default_channel': 'edge',
                  'channels': {name: {'catalog_url': 'https://example.invalid/'+name}
                               for name in policy.CHANNELS}}
        with self.assertRaisesRegex(ValueError, 'opt-in'):
            policy.descriptor(json.dumps(source).encode())

    def test_testing_never_becomes_edge_even_with_a_qualification_object(self):
        for declared in (None, 'testing'):
            candidate = copy.deepcopy(self.catalog)
            if declared is None:
                candidate.pop('channel')
            else:
                candidate['channel'] = declared
            with self.subTest(declared=declared), self.assertRaisesRegex(ValueError, 'cannot become Edge'):
                policy.validate_catalog(candidate, 'edge')

    def test_legacy_testing_needs_authenticated_development_lineage(self):
        candidate = {'models': [{'payloadDigest': 'sha256:'+'a'*64}]}
        with self.assertRaisesRegex(ValueError, 'authenticated legacy'):
            policy.validate_catalog(candidate, 'testing')
        policy.validate_catalog(candidate, 'testing', legacy_development=True)

    def test_a_channel_cannot_read_a_different_channel_catalog(self):
        with self.assertRaisesRegex(ValueError, 'different channel'):
            policy.validate_catalog(self.catalog, 'testing')

    def test_empty_edge_needs_no_invented_qualification(self):
        policy.validate_catalog({'schemaVersion': 5, 'channel': 'edge', 'models': []}, 'edge')

    def test_a_complete_new_alpha_is_supported(self):
        self.verify()

    def test_source_branch_revision_tree_and_status_are_required(self):
        for source in ('linux', 'mesa'):
            for field, value in [('branch', 'main'), ('revision', ''), ('tree', 'not-a-tree')]:
                candidate = copy.deepcopy(self.catalog)
                candidate['edgeQualification'][source][field] = value
                with self.subTest(source=source, field=field), self.assertRaises(ValueError):
                    policy.validate_catalog(candidate, 'edge')
        candidate = copy.deepcopy(self.catalog)
        candidate['edgeQualification']['status'] = 'pending'
        with self.assertRaises(ValueError):
            policy.validate_catalog(candidate, 'edge')

    def test_exact_detached_report_bytes_are_bound_without_self_reference(self):
        with self.assertRaisesRegex(ValueError, 'evidence differs'):
            self.verify(raw=self.raw+b' ')
        self.assertNotIn('evidenceDigest', json.loads(self.raw))

    def test_actual_packaged_sources_must_match_qualified_sources(self):
        receipt = copy.deepcopy(self.receipt)
        receipt['packaged_sources']['mesa']['tree'] = 'd'*40
        with self.assertRaisesRegex(ValueError, 'packaged Linux/Mesa'):
            self.verify(receipt=receipt)

    def test_receipt_payload_must_match_the_qualified_payload(self):
        receipt = copy.deepcopy(self.receipt)
        receipt['artifacts']['os.zip']['sha256'] = 'a'*64
        with self.assertRaisesRegex(ValueError, 'payload receipt'):
            self.verify(receipt=receipt)

    def test_an_existing_testing_payload_cannot_be_relabelled(self):
        with self.assertRaisesRegex(ValueError, 'relabelled'):
            self.verify(testing={'models': [{'payloadDigest': self.report['payloadDigest']}]})

    def test_actual_git_tree_and_edge_ancestry_are_verified(self):
        def fetch(path):
            if '/git/commits/' in path:
                return {'sha': 'a'*40, 'tree': {'sha': 'c'*40}}
            if '/git/ref/heads/' in path:
                return {'object': {'sha': 'b'*40}}
            return {'status': 'ahead', 'base_commit': {'sha': 'a'*40}}
        policy.verify_edge_source_refs(self.catalog['edgeQualification'], fetch=fetch)
        def wrong_tree(path):
            if '/git/commits/' in path:
                return {'sha': 'a'*40, 'tree': {'sha': 'd'*40}}
            return fetch(path)
        with self.assertRaisesRegex(ValueError, 'tree or branch ancestor'):
            policy.verify_edge_source_refs(self.catalog['edgeQualification'], fetch=wrong_tree)
        def wrong_branch(path):
            if '/compare/' in path:
                return {'status': 'diverged', 'base_commit': {'sha': 'a'*40}}
            return fetch(path)
        with self.assertRaisesRegex(ValueError, 'tree or branch ancestor'):
            policy.verify_edge_source_refs(self.catalog['edgeQualification'], fetch=wrong_branch)

    def test_default_sources_resolve_qualified_branch_heads(self):
        catalog = copy.deepcopy(self.catalog)
        proof = catalog['edgeQualification']
        proof['linux']['branch'] = 'main'
        proof['mesa']['branch'] = 'aurora-wip'
        paths = []
        def fetch(path):
            paths.append(path)
            if '/git/ref/heads/' in path:
                return {'object': {'sha': 'b'*40}}
            if '/git/commits/' in path:
                return {'sha': 'a'*40, 'tree': {'sha': 'c'*40}}
            return {'status': 'ahead', 'base_commit': {'sha': 'a'*40}}
        policy.validate_catalog(catalog, 'edge')
        policy.verify_edge_source_refs(proof, fetch=fetch)
        self.assertIn('repos/aurora-silicon/linux/git/ref/heads/main', paths)
        self.assertIn('repos/aurora-silicon/mesa/git/ref/heads/aurora-wip', paths)
        self.assertTrue(all(path.endswith('b'*40) for path in paths if '/compare/' in path))

    def test_testing_release_artifact_urls_cannot_be_reused(self):
        candidate = copy.deepcopy(self.catalog)
        candidate['models'][0]['payloadArtifact']['sourceURL'] = 'https://example.invalid/testing.zip'
        with self.assertRaisesRegex(ValueError, 'new Edge Alpha release'):
            self.verify(catalog=candidate)


if __name__ == '__main__':
    unittest.main()
