"""A plain delivery is built by the pinned producer whose model list is the adopted cohort."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

import test_adopt_variant as variant

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('select_plain_producer', ROOT / 'scripts/select-plain-producer.py')
selector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(selector)

OLD, NEW = '9779fa452351c5a66ae583918b8e8120c044051f', 'e3e085f23821efa71fb55bd1e3e6bbae83255c2e'
# Each pinned producer's macOS/scripts/supported-models.json, byte for byte. The workflow
# reads the producer's own file at selection time; these are the same bytes.
MANIFESTS = {OLD: (ROOT / 'keys/supported-models-58.json').read_bytes(),
             NEW: (ROOT / 'keys/supported-models-65.json').read_bytes()}
COHORT58 = (ROOT / 'keys/supported-models-58.json').read_bytes()
COHORT65 = (ROOT / 'keys/supported-models-65.json').read_bytes()


def manifest(revision):
    return MANIFESTS[revision]


def edited(raw, change):
    document = json.loads(raw)
    change(document)
    return json.dumps(document).encode()


class SelectionTests(unittest.TestCase):
    def test_the_pinned_producers_are_the_old_and_new_installer_producers(self):
        self.assertEqual(set(selector.PRODUCERS), {OLD, NEW})

    def test_the_58_cohort_selects_the_58_model_producer(self):
        revision, models = selector.select(COHORT58, manifest)
        self.assertEqual(revision, OLD)
        self.assertEqual(len(models), 58)
        self.assertIn('apple,j704', models)

    def test_the_65_cohort_selects_the_65_model_producer(self):
        revision, models = selector.select(COHORT65, manifest)
        self.assertEqual(revision, NEW)
        self.assertEqual(len(models), 65)

    def test_a_cohort_no_pinned_producer_lists_is_refused(self):
        for change in (lambda d: d['supported'].remove('apple,j704'),
                       lambda d: d['supported'].append('apple,j999'),
                       lambda d: d['supported'].__setitem__(0, 'apple,j999')):
            with self.assertRaisesRegex(ValueError, 'no pinned installer producer'):
                selector.select(edited(COHORT58, change), manifest)

    def test_two_producers_with_the_same_list_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'more than one pinned installer producer'):
            selector.select(COHORT58, lambda revision: COHORT58)

    def test_a_malformed_cohort_or_producer_list_is_refused(self):
        for change in (lambda d: d.__setitem__('schema_version', 2),
                       lambda d: d.__setitem__('supported', []),
                       lambda d: d['supported'].append(d['supported'][0]),
                       lambda d: d['supported'].append(7),
                       lambda d: d.pop('supported')):
            with self.assertRaisesRegex(ValueError, 'not a supported-models list'):
                selector.select(edited(COHORT58, change), manifest)
            with self.assertRaisesRegex(ValueError, 'not a supported-models list'):
                selector.select(COHORT58, lambda revision: edited(MANIFESTS[revision], change))
        with self.assertRaisesRegex(ValueError, 'not a supported-models list'):
            selector.select(b'[]', manifest)

    def test_main_reads_the_cohort_at_the_aurora_distributions_main_commit(self):
        head, reads = 'c' * 40, []

        def fetch(repository, path, revision):
            reads.append((repository, path, revision))
            if repository == selector.COHORT_REPOSITORY:
                return COHORT58
            return MANIFESTS[revision]

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'github-output'
            selector.main(['--output', str(output)], fetch=fetch, head=lambda repository: head)
            lines = output.read_text().splitlines()
        self.assertEqual(lines, ['revision=' + OLD, 'cohort-revision=' + head, 'models=58'])
        self.assertEqual(reads[0], (selector.COHORT_REPOSITORY, 'keys/supported-models.json', head))
        self.assertEqual({read[2] for read in reads[1:]}, {OLD, NEW})
        self.assertEqual({read[:2] for read in reads[1:]},
                         {('aurora-silicon/aurora-installer', 'macOS/scripts/supported-models.json')})

    def test_main_writes_nothing_when_the_cohort_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'github-output'
            with self.assertRaises(ValueError):
                selector.main(['--output', str(output)], head=lambda repository: 'c' * 40,
                              fetch=lambda repository, path, revision: (
                                  b'{}' if repository == selector.COHORT_REPOSITORY else MANIFESTS[revision]))
            self.assertFalse(output.exists())


class PlainAdoptionTests(unittest.TestCase):
    """The plain delivery the selected producer builds passes adopt-delivery.py's model coverage."""

    def setUp(self):
        variant.VariantTests.setUp(self)
        self.run_id = 44873841742
        self.json['delivery.json']['release_id'] = 'build-%d' % self.run_id
        self.json['image-build.json']['id'] = self.run_id
        for name in ('source-delivery.json', 'zip-refresh.json'):
            del self.json[name]

    def adopt_under(self, cohort, producer_models):
        (self.root / 'keys/supported-models.json').write_bytes(cohort)
        # build-payload.yml writes device_identifiers from the producer's own list.
        self.catalog['models'] = [{'deviceIdentifier': identifier, 'installationProfile': 'fedora-kde',
                                   'status': 'enabled'} for identifier in producer_models]
        variant.VariantTests.invoke(self, tag='payload-fedora-kde-%d' % self.run_id)
        return json.loads((self.root / 'site/installer/Profiles/fedora-kde/catalog.json').read_text())

    def selected_models(self, cohort):
        revision, _ = selector.select(cohort, manifest)
        return json.loads(MANIFESTS[revision])['supported']

    def test_a_plain_delivery_under_the_58_cohort_is_adopted_with_exactly_the_58(self):
        adopted = self.adopt_under(COHORT58, self.selected_models(COHORT58))
        enabled = [model['deviceIdentifier'] for model in adopted['models'] if model['status'] == 'enabled']
        self.assertEqual(sorted(enabled), sorted(json.loads(COHORT58)['supported']))
        self.assertEqual(len(enabled), 58)
        self.assertIn('apple,j704', enabled)

    def test_a_plain_delivery_under_the_65_cohort_is_adopted_with_exactly_the_65(self):
        adopted = self.adopt_under(COHORT65, self.selected_models(COHORT65))
        self.assertEqual(len([model for model in adopted['models'] if model['status'] == 'enabled']), 65)

    def test_the_65_model_producer_under_the_58_cohort_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'model coverage'):
            self.adopt_under(COHORT58, json.loads(MANIFESTS[NEW])['supported'])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / '.github/workflows/build-payload.yml').read_text()

    def test_the_workflow_runs_the_reviewed_selector(self):
        source = (ROOT / 'scripts/select-plain-producer.py').read_bytes()
        self.assertIn(hashlib.sha256(source).hexdigest() + '  select-plain-producer.py', self.workflow)

    def test_only_a_plain_delivery_selects_its_producer(self):
        step = self.workflow[self.workflow.index('id: producer'):]
        step = step[:step.index('      - ')]
        self.assertIn("inputs.neo-edge-evidence == '' && inputs.edge-train-revision == '' "
                      "&& inputs.delivery-revision == ''", step)

    def test_refreshes_keep_their_fixed_producers_and_a_plain_delivery_takes_the_selected_one(self):
        self.assertIn("ref: ${{ inputs.delivery-revision == 'models58' && 'b34998dae173411bbaff90b7bd1ad90e5987477b'"
                      " || inputs.delivery-revision == 'models65' && '68148f7b1b820059f65780e17bc27ccb576def49'"
                      " || steps.producer.outputs.revision }}", self.workflow)

    def test_a_plain_assembly_refuses_a_producer_it_did_not_select(self):
        identity = self.workflow[self.workflow.index('- name: Select immutable delivery identity'):]
        identity = identity[:identity.index('PYIDENTITY\n', identity.index('PYIDENTITY') + 1)]
        self.assertIn('PRODUCER_REVISION: ${{ steps.producer.outputs.revision }}', identity)
        self.assertRegex(identity, re.escape("assert revision or (re.fullmatch(r'[0-9a-f]{40}',producer) and head == producer)"))


if __name__ == '__main__':
    unittest.main()
