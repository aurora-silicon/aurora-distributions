import copy
import json
import re
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import edge_fixtures as fx  # noqa: E402
from edge_fixtures import edge_train  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SOCS = {'t8103': 'M1', 't6000': 'M1 Pro', 't6001': 'M1 Max', 't6002': 'M1 Ultra', 't8112': 'M2',
        't6020': 'M2 Pro', 't6021': 'M2 Max', 't6022': 'M2 Ultra', 't8122': 'M3', 't6030': 'M3 Pro',
        't6031': 'M3 Max', 't6034': 'M3 Max', 't6032': 'M3 Ultra'}
# The board device trees name each board's SoC (arch/arm64/boot/dts/apple/<soc>-<board>.dts).
BOARD_SOC = {
    't8103': 'j274 j293 j313 j456 j457', 't6000': 'j314s j316s', 't6001': 'j314c j316c j375c',
    't6002': 'j375d', 't8112': 'j413 j415 j473 j493', 't6020': 'j414s j416s j474s',
    't6021': 'j414c j416c j475c', 't6022': 'j180d j475d', 't8122': 'j433 j434 j504 j613 j615',
    't6030': 'j514s j516s', 't6031': 'j514c j516c', 't6034': 'j514m j516m', 't6032': 'j575d',
}


class CohortTests(unittest.TestCase):
    def test_cohort_is_every_m1_m2_m3_mac_of_the_65_model_list_plus_the_neo(self):
        cohort = fx.cohort()
        supported = json.loads((ROOT / 'keys/supported-models-65.json').read_text())['supported']
        target = {'apple,' + board for soc in SOCS for board in BOARD_SOC[soc].split()}
        self.assertTrue(target <= set(supported))
        listed = {model for family in cohort['families'].values() for model in family['models']}
        self.assertEqual(listed | set(cohort['excluded']), target | {'apple,j700'})
        self.assertEqual(set(cohort['excluded']), {'apple,j575d'})
        self.assertEqual(len(listed), 35)
        for name, family in cohort['families'].items():
            for model in family['models']:
                soc = next(soc for soc, boards in BOARD_SOC.items() if model[6:] in boards.split()) \
                    if model != 'apple,j700' else 't8140'
                self.assertIn(soc, family['socs'], model)

    def test_every_profile_carries_every_cohort_mac(self):
        cohort, train = fx.cohort(), fx.train()
        self.assertEqual(len(cohort['profiles']), 10)
        for profile in cohort['profiles']:
            models = edge_train.profile_models(cohort, train, profile)
            self.assertEqual(len(models), 35, profile)
            self.assertIn('apple,j700', models)

    def test_models_follow_the_producer_order(self):
        supported = json.loads((ROOT / 'keys/supported-models-65.json').read_text())['supported']
        models = edge_train.profile_models(fx.cohort(), fx.train(), 'arch-kde')
        self.assertEqual(models, [model for model in supported if model in models])

    def test_the_m5_rider_rides_only_its_profile_and_only_when_named(self):
        cohort = fx.cohort()
        self.assertNotIn('apple,j714s', edge_train.profile_models(cohort, fx.train(), 'fedora-kde'))
        train = fx.train(riders=['t6050'])
        self.assertIn('apple,j714s', edge_train.profile_models(cohort, train, 'fedora-kde'))
        self.assertNotIn('apple,j714s', edge_train.profile_models(cohort, train, 'arch-kde'))
        with self.assertRaisesRegex(ValueError, 'unknown rider'):
            edge_train.verify_train(fx.train(riders=['t9999']), cohort)

    def test_gates_match_the_neo_and_m5_evidence_contracts(self):
        import neo_edge
        cohort = fx.cohort()
        self.assertEqual(set(cohort['families']['t8140']['gates']), neo_edge.GATES)
        m5_gates = getattr(neo_edge, 'M5_GATES', None)
        if m5_gates is not None:
            self.assertEqual(set(cohort['riders']['t6050']['gates']), m5_gates)

    def test_excluded_and_cohort_models_are_disjoint(self):
        cohort = copy.deepcopy(fx.cohort())
        cohort['families']['t603x']['models'].append('apple,j575d')
        with self.assertRaisesRegex(ValueError, 'excluded'):
            edge_train.verify_cohort(cohort)


class TrainTests(unittest.TestCase):
    def test_complete_train_validates(self):
        edge_train.verify_train(fx.train(), fx.cohort())
        edge_train.verify_train(fx.staged(fx.train()), fx.cohort(), staged=True)

    def test_the_pair_is_a_variable_but_must_be_aurora_edge(self):
        for path, value, message in (
                (('pair', 'mesa', 'branch'), 'aurora-wip', 'aurora-edge'),
                (('pair', 'linux', 'tree'), 'tested', 'aurora-edge'),
                (('reference', 'mesaSigningRunID'), 0, 'reference'),
                (('release',), 'beta.1', 'release'),
                (('distributions', 'arch', 'snapshot'), 'testing-20261121.1', 'snapshot'),
                (('distributions', 'omarchy', 'alarm_snapshot'), 'edge-20261120.9', 'ALARM Edge snapshot')):
            with self.subTest(path=path):
                train = fx.train()
                target = train
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaisesRegex(ValueError, message):
                    edge_train.verify_train(train, fx.cohort())

    def test_a_partial_profile_list_is_refused(self):
        train = fx.train()
        del train['profiles']['nixos-minimal']
        with self.assertRaisesRegex(ValueError, 'every Edge profile'):
            edge_train.verify_train(train, fx.cohort())

    def test_staged_train_needs_every_staging_record(self):
        train = fx.staged(fx.train())
        del train['profiles']['arch-gnome']['stage']
        with self.assertRaisesRegex(ValueError, 'staging record'):
            edge_train.verify_train(train, fx.cohort(), staged=True)

    def test_train_digest_ignores_staging_records(self):
        train = fx.train()
        self.assertEqual(edge_train.train_digest(train), edge_train.train_digest(fx.staged(train)))
        changed = fx.train()
        changed['pair']['mesa']['revision'] = 'e' * 40
        self.assertNotEqual(edge_train.train_digest(train), edge_train.train_digest(changed))

    def test_release_identity(self):
        train = fx.train()
        self.assertEqual(edge_train.release_tag(train, 'nixos-gnome'),
                         'payload-nixos-gnome-%d-edge-alpha.4' % train['profiles']['nixos-gnome']['image_run'])
        self.assertTrue(re.fullmatch(r'build-[0-9]+-edge-alpha\.4', edge_train.release_id(train, 'arch-kde')))


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.cohort, self.train = fx.cohort(), fx.train()
        self.evidence = fx.evidence(self.train)

    def test_complete_evidence_passes(self):
        edge_train.verify_evidence(self.evidence, self.cohort, self.train)

    def test_every_family_and_distribution_needs_its_record(self):
        for section, name in (('families', 't603x'), ('families', 't8140'), ('distributions', 'nixos'),
                              ('distributions', 'omarchy')):
            with self.subTest(name=name):
                evidence = copy.deepcopy(self.evidence)
                del evidence[section][name]
                with self.assertRaisesRegex(ValueError, 'evidence'):
                    edge_train.verify_evidence(evidence, self.cohort, self.train)

    def test_a_failed_or_missing_gate_is_refused(self):
        for gate in self.cohort['gates']['family']:
            with self.subTest(gate=gate):
                evidence = copy.deepcopy(self.evidence)
                evidence['families']['t8112']['checks'][gate]['status'] = 'failed'
                with self.assertRaisesRegex(ValueError, 'passing'):
                    edge_train.verify_evidence(evidence, self.cohort, self.train)
        evidence = copy.deepcopy(self.evidence)
        del evidence['families']['t8140']['checks']['glCTS']
        with self.assertRaisesRegex(ValueError, 'gates'):
            edge_train.verify_evidence(evidence, self.cohort, self.train)

    def test_rows_on_another_pair_are_refused(self):
        evidence = copy.deepcopy(self.evidence)
        evidence['families']['t600x']['pair']['mesaDsoSHA256'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(ValueError, 'pair'):
            edge_train.verify_evidence(evidence, self.cohort, self.train)

    def test_a_row_must_come_from_a_machine_of_its_family(self):
        evidence = copy.deepcopy(self.evidence)
        evidence['families']['t603x']['machine'] = 'apple,j613'
        with self.assertRaisesRegex(ValueError, 'machine'):
            edge_train.verify_evidence(evidence, self.cohort, self.train)

    def test_a_reviewed_stand_in_family_is_accepted_and_recorded(self):
        cohort = copy.deepcopy(self.cohort)
        cohort['families']['t603x']['qualifiedBy'] = 't8122'
        evidence = copy.deepcopy(self.evidence)
        evidence['families']['t603x']['machine'] = 'apple,j613'
        edge_train.verify_evidence(evidence, cohort, self.train)
        cohort['families']['t603x']['qualifiedBy'] = 't8140'
        with self.assertRaisesRegex(ValueError, 'stand-in'):
            edge_train.verify_cohort(cohort)

    def test_distribution_smoke_binds_that_distributions_own_build(self):
        evidence = copy.deepcopy(self.evidence)
        evidence['distributions']['arch']['pair'] = dict(fx.DISTRIBUTION_FILES['fedora'])
        proof = fx.image_proof(self.train, 'arch-kde')
        with self.assertRaisesRegex(ValueError, 'own kernel and Mesa'):
            edge_train.profile_evidence(evidence, self.cohort, self.train, 'arch-kde', proof)

    def test_rider_evidence_is_required_once_it_rides(self):
        train = fx.train(riders=['t6050'])
        with self.assertRaisesRegex(ValueError, 'evidence'):
            edge_train.verify_evidence(self.evidence, self.cohort, train)
        edge_train.verify_evidence(fx.evidence(train), self.cohort, train)


class BindTests(unittest.TestCase):
    def setUp(self):
        self.cohort, self.train = fx.cohort(), fx.staged(fx.train(riders=['t6050']))
        self.evidence = fx.evidence(self.train)

    def bind(self, profile, proof=None, run=None):
        catalog, receipt, raw_metadata = fx.template(self.train, profile)
        proof = proof or fx.image_proof(self.train, profile)
        run = run or fx.image_run(self.train, profile)
        raw = edge_train.bind(catalog, receipt, run, json.dumps(proof).encode(), self.evidence,
                              self.cohort, self.train, profile)
        return catalog, receipt, raw, raw_metadata, run, proof

    def test_bound_catalog_passes_the_channel_policy_and_metadata(self):
        from channel_policy import validate_catalog, verify_edge_delivery
        for profile in self.cohort['profiles']:
            with self.subTest(profile=profile):
                catalog, receipt, raw, raw_metadata, run, proof = self.bind(profile)
                validate_catalog(catalog, 'edge')
                base = 'https://github.com/%s/releases/download/%s' % (
                    edge_train.owner(profile), edge_train.release_tag(self.train, profile))
                verify_edge_delivery(catalog, raw, receipt, run, base)
                edge_train.verify_metadata(catalog, raw_metadata, self.cohort, self.train, profile)
                edge_train.verify_binding(catalog, receipt, run, json.dumps(proof).encode(),
                                          self.cohort, self.train, profile)
                ids = [row['deviceIdentifier'] for row in catalog['models']]
                self.assertNotIn('apple,j813', ids)
                self.assertEqual(ids, edge_train.profile_models(self.cohort, self.train, profile))
                report = json.loads(raw)
                self.assertEqual(report['edgeTrain']['release'], 'alpha.4')
                self.assertEqual(receipt['packaged_sources'], self.train['pair'])
                self.assertEqual(report['distributionEvidence'],
                                 self.evidence['distributions'][edge_train.distribution(profile)])
                self.assertEqual('t6050' in report['familyEvidence'], profile == 'fedora-kde')

    def test_an_image_from_another_snapshot_or_pair_is_refused(self):
        for key, value, message in (('snapshot', 'edge-20261101.1', 'snapshot'),
                                    ('feed', 'https://aurorasilicon.org/alarm-aurora/testing/aarch64', 'feed'),
                                    ('channel', 'testing', 'Edge image proof'),
                                    ('profile', 'arch-gnome', 'Edge image proof')):
            with self.subTest(key=key):
                proof = fx.image_proof(self.train, 'arch-kde')
                proof[key] = value
                with self.assertRaisesRegex(ValueError, message):
                    self.bind('arch-kde', proof=proof)
        proof = fx.image_proof(self.train, 'arch-kde')
        proof['packaged_sources']['linux']['revision'] = 'e' * 40
        with self.assertRaisesRegex(ValueError, 'packaged'):
            self.bind('arch-kde', proof=proof)

    def test_reference_build_must_be_the_fedora_image(self):
        proof = fx.image_proof(self.train, 'fedora-gnome')
        proof['files']['kernelImageSHA256'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(ValueError, 'reference'):
            self.bind('fedora-gnome', proof=proof)

    def test_image_run_must_be_the_trains_main_images_run(self):
        for key, value in (('head_branch', 'edge/neo-f44-image-e0ee46a1'), ('id', 1),
                           ('path', '.github/workflows/edge-f44-image.yml'), ('conclusion', 'failure')):
            with self.subTest(key=key):
                run = fx.image_run(self.train, 'nixos-kde')
                run[key] = value
                with self.assertRaisesRegex(ValueError, 'image run'):
                    self.bind('nixos-kde', run=run)

    def test_missing_model_rows_are_refused(self):
        catalog, receipt, _ = fx.template(self.train, 'arch-kde')
        catalog['models'] = [row for row in catalog['models'] if row['deviceIdentifier'] != 'apple,j293']
        with self.assertRaisesRegex(ValueError, 'model rows'):
            edge_train.bind(catalog, receipt, fx.image_run(self.train, 'arch-kde'),
                            json.dumps(fx.image_proof(self.train, 'arch-kde')).encode(),
                            self.evidence, self.cohort, self.train, 'arch-kde')

    def test_metadata_must_admit_exactly_the_cohort_boards(self):
        catalog, receipt, raw, raw_metadata, run, proof = self.bind('arch-kde')
        wider = json.loads(raw_metadata)
        wider['os_list'][0]['allowed_device_classes'].append('j813ap')
        with self.assertRaisesRegex(ValueError, 'install policy'):
            edge_train.verify_metadata(catalog, json.dumps(wider).encode(), self.cohort, self.train, 'arch-kde')

    def test_binding_rejects_a_report_from_another_train(self):
        catalog, receipt, raw, raw_metadata, run, proof = self.bind('omarchy-hyprland')
        other = copy.deepcopy(self.train)
        other['reference']['mesaSigningRunID'] += 1
        with self.assertRaisesRegex(ValueError, 'train'):
            edge_train.verify_binding(catalog, receipt, run, json.dumps(proof).encode(),
                                      self.cohort, other, 'omarchy-hyprland')


if __name__ == '__main__':
    unittest.main()
