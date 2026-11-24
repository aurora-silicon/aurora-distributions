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

    def test_the_m5_rider_has_no_fresh_install_gate(self):
        # A fresh install cannot be done or restored inside an M5 rig slot; it is
        # verified from the live channel after adoption, as for the Neo's alpha.1.
        self.assertEqual(fx.cohort()['riders']['t6050']['gates'], ['releaseGate', 'deepSleep', 'restoration'])

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


class HeldFamilyTests(unittest.TestCase):
    def test_a_held_family_stays_off_edge_and_needs_no_rows(self):
        cohort, train = fx.cohort(), fx.train()
        train['held'] = ['t8122', 't8122-desktop', 't603x']
        edge_train.verify_train(train, cohort)
        models = edge_train.profile_models(cohort, train, 'arch-kde')
        self.assertNotIn('apple,j613', models)
        self.assertNotIn('apple,j514s', models)
        self.assertIn('apple,j413', models)
        self.assertEqual(len(models), 35 - 5 - 6)
        evidence = fx.evidence(train)
        self.assertNotIn('t8122', evidence['families'])
        edge_train.verify_evidence(evidence, cohort, train)

    def test_a_stand_in_family_is_held_with_the_family_it_rides_on(self):
        train = fx.train()
        train['held'] = ['t8122']
        with self.assertRaisesRegex(ValueError, 'held'):
            edge_train.verify_train(train, fx.cohort())

    def test_held_names_known_families_and_never_a_rider(self):
        for held in (['t9999'], ['t6050'], ['t8122', 't8122']):
            with self.subTest(held=held):
                train = fx.train(riders=['t6050'])
                train['held'] = held
                with self.assertRaisesRegex(ValueError, 'held'):
                    edge_train.verify_train(train, fx.cohort())


class ChainTests(unittest.TestCase):
    """A family row may come in parts on different boot chains (Joshua's Omarchy m1n1/U-Boot Macs)."""

    def setUp(self):
        self.cohort, self.train = fx.cohort(), fx.train()
        self.evidence = fx.evidence(self.train)
        record = self.evidence['families']['t8103']
        record['chains'] = {'own': {'bootChain': 'm1n1-uboot'},
                            'aura': {'bootChain': 'aura', 'stage2SHA256': 'sha256:' + 'd8e9ee04' * 8}}
        for gate in ('vulkanCTS', 'glCTS'):
            record['checks'][gate]['chain'] = 'own'
        for gate in ('suspend', 'restoration'):
            record['checks'][gate]['chain'] = 'aura'

    def test_a_two_part_row_is_accepted_and_carried_into_device_records(self):
        edge_train.verify_evidence(self.evidence, self.cohort, self.train)
        catalog, receipt, _ = fx.template(self.train, 'fedora-minimal')
        edge_train.bind(catalog, receipt, fx.image_run(self.train, 'fedora-minimal'),
                        json.dumps(fx.image_proof(self.train, 'fedora-minimal')).encode(),
                        self.evidence, self.cohort, self.train, 'fedora-minimal')
        records = edge_train.device_evidence(catalog, self.cohort, self.train, 'fedora-minimal')
        record = json.loads(records[edge_train.device_record_name('apple,j293')])
        self.assertEqual(record['chains'], self.evidence['families']['t8103']['chains'])
        self.assertEqual(record['checks']['vulkanCTS']['chain'], 'own')
        edge_train.verify_devices(catalog, records)

    def test_chain_references_must_resolve(self):
        self.evidence['families']['t8103']['checks']['glCTS']['chain'] = 'other'
        with self.assertRaisesRegex(ValueError, 'chain'):
            edge_train.verify_evidence(self.evidence, self.cohort, self.train)

    def test_freeze_must_run_on_the_shipped_aura_chain(self):
        self.evidence['families']['t8103']['checks']['suspend']['chain'] = 'own'
        with self.assertRaisesRegex(ValueError, 'aura'):
            edge_train.verify_evidence(self.evidence, self.cohort, self.train)

    def test_unknown_boot_chains_are_refused(self):
        self.evidence['families']['t8103']['chains']['own']['bootChain'] = 'grub'
        with self.assertRaisesRegex(ValueError, 'chain'):
            edge_train.verify_evidence(self.evidence, self.cohort, self.train)


class WithdrawnTests(unittest.TestCase):
    def test_a_withdrawn_mac_leaves_every_catalog_and_its_device_proof(self):
        cohort, train = fx.cohort(), fx.train()
        train['withdrawn'] = ['apple,j413']
        edge_train.verify_train(train, cohort)
        self.assertNotIn('apple,j413', edge_train.profile_models(cohort, train, 'nixos-kde'))
        self.assertNotIn('apple,j413', edge_train.proven_models(cohort, train, 'nixos-kde'))
        self.assertIn('apple,j415', edge_train.profile_models(cohort, train, 'nixos-kde'))

    def test_a_stand_in_family_leaves_with_the_family_it_rides_on(self):
        cohort = fx.cohort()
        laptops = list(cohort['families']['t8112']['models'])
        train = fx.train()
        train['withdrawn'] = laptops
        with self.assertRaisesRegex(ValueError, 't8112-desktop'):
            edge_train.verify_train(train, cohort)
        train['withdrawn'] = laptops + cohort['families']['t8112-desktop']['models']
        edge_train.verify_train(train, cohort)
        train['withdrawn'], train['held'] = laptops, ['t8112-desktop']
        edge_train.verify_train(train, cohort)
        # Withdrawing only some laptops keeps the row and its stand-in.
        train = fx.train()
        train['withdrawn'] = laptops[:1]
        edge_train.verify_train(train, cohort)

    def test_withdrawn_names_distinct_cohort_macs(self):
        for withdrawn in (['apple,j999'], ['apple,j413', 'apple,j413'], 'apple,j413'):
            with self.subTest(withdrawn=withdrawn):
                train = fx.train()
                train['withdrawn'] = withdrawn
                with self.assertRaisesRegex(ValueError, 'withdrawn'):
                    edge_train.verify_train(train, fx.cohort())


class ManifestTests(unittest.TestCase):
    """Every check digest names a published sha256sum manifest of the train record."""

    def setUp(self):
        self.cohort, self.train = fx.cohort(), fx.train()
        self.evidence = fx.evidence(self.train)
        self.manifests = fx.manifests()

    def test_published_manifests_back_every_digest(self):
        edge_train.verify_manifests(self.evidence, self.manifests)

    def test_a_missing_altered_or_malformed_manifest_is_refused(self):
        name = self.evidence['families']['t600x']['checks']['vulkanCTS']['sha256'].removeprefix('sha256:')
        for change in (lambda m: m.pop(name), lambda m: m.update({name: m[name] + b'x'}),
                       lambda m: m.update({name: b'not a manifest\n'})):
            with self.subTest(change=change):
                manifests = dict(self.manifests)
                change(manifests)
                with self.assertRaisesRegex(ValueError, 'manifest'):
                    edge_train.verify_manifests(self.evidence, manifests)


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
                                          self.cohort, self.train, profile, self.evidence)
                ids = [row['deviceIdentifier'] for row in catalog['models']]
                self.assertNotIn('apple,j813', ids)
                self.assertEqual(ids, edge_train.profile_models(self.cohort, self.train, profile))
                report = json.loads(raw)
                self.assertEqual(report['edgeTrain']['release'], 'alpha.4')
                self.assertEqual(receipt['packaged_sources'], self.train['pair'])
                self.assertEqual(report['distributionEvidence'],
                                 self.evidence['distributions'][edge_train.distribution(profile)])
                self.assertEqual('t6050' in report['familyEvidence'], profile == 'fedora-kde')

    def test_every_proven_mac_carries_its_own_detached_device_evidence(self):
        catalog, receipt, raw, raw_metadata, run, proof = self.bind('fedora-kde')
        report = json.loads(raw)
        models = edge_train.proven_models(self.cohort, self.train, 'fedora-kde')
        records = edge_train.device_evidence(catalog, self.cohort, self.train, 'fedora-kde')
        self.assertEqual(set(report['devices']), set(models))
        self.assertEqual(set(records), {edge_train.device_record_name(model) for model in models})
        for model in models:
            record = records[edge_train.device_record_name(model)]
            self.assertEqual(report['devices'][model], {'evidenceDigest': edge_train.digest(record)})
            parsed = json.loads(record)
            self.assertEqual(parsed['deviceIdentifier'], model)
            family = parsed['family']
            self.assertEqual({k: parsed[k] for k in ('machine', 'pair', 'checks') + (('chains',) if 'chains' in parsed else ())},
                             report['familyEvidence'][family])
            self.assertEqual({k: parsed[k] for k in ('linux', 'mesa')}, self.train['pair'])
        edge_train.verify_devices(catalog, records)
        # The Alpha 22 installer reads the Neo's record in the old shape.
        self.assertEqual(report['neoEvidence'], {k: report['familyEvidence']['t8140'][k] for k in ('pair', 'checks')})

    def test_ultra_parts_and_desktops_are_listed_without_device_proof(self):
        catalog, receipt, raw, raw_metadata, run, proof = self.bind('fedora-gnome')
        listed = [row['deviceIdentifier'] for row in catalog['models']]
        devices = json.loads(raw)['devices']
        for model in ('apple,j375d', 'apple,j475d', 'apple,j180d', 'apple,j375c', 'apple,j475c',
                      'apple,j274', 'apple,j473', 'apple,j474s', 'apple,j456'):
            self.assertIn(model, listed)
            self.assertNotIn(model, devices)
        for model in ('apple,j293', 'apple,j314s', 'apple,j413', 'apple,j414c', 'apple,j700'):
            self.assertIn(model, devices)

    def test_a_stand_in_family_is_listed_without_device_proof(self):
        cohort = copy.deepcopy(self.cohort)
        cohort['families']['t603x']['qualifiedBy'] = 't8122'
        evidence = copy.deepcopy(self.evidence)
        evidence['families']['t603x']['machine'] = 'apple,j613'
        catalog, receipt, _ = fx.template(self.train, 'arch-kde', cohort)
        raw = edge_train.bind(catalog, receipt, fx.image_run(self.train, 'arch-kde'),
                              json.dumps(fx.image_proof(self.train, 'arch-kde')).encode(),
                              evidence, cohort, self.train, 'arch-kde')
        devices = json.loads(raw)['devices']
        self.assertIn('apple,j514s', [row['deviceIdentifier'] for row in catalog['models']])
        self.assertNotIn('apple,j514s', devices)
        self.assertIn('apple,j613', devices)

    def test_binding_rejects_evidence_other_than_the_train_record(self):
        catalog, receipt, raw, raw_metadata, run, proof = self.bind('arch-gnome')
        other = copy.deepcopy(self.evidence)
        other['families']['t8112']['checks']['glCTS'] = fx.check('another-run')
        with self.assertRaisesRegex(ValueError, 'train'):
            edge_train.verify_binding(catalog, receipt, run, json.dumps(proof).encode(),
                                      self.cohort, self.train, 'arch-gnome', other)

    def test_device_records_are_refused_when_altered_or_misnamed(self):
        catalog, *_ = self.bind('nixos-gnome')
        records = edge_train.device_evidence(catalog, self.cohort, self.train, 'nixos-gnome')
        name = edge_train.device_record_name('apple,j413')
        for change, message in (
                (lambda r: r.update({name: r[name] + b' '}), 'device evidence'),
                (lambda r: r.pop(name), 'device evidence'),
                (lambda r: r.update({name: json.dumps(dict(json.loads(r[name]), deviceIdentifier='apple,j415'),
                                                      sort_keys=True, indent=2).encode() + b'\n'}), 'names another device')):
            with self.subTest(message=message):
                altered = dict(records)
                change(altered)
                if message == 'names another device':
                    catalog['edgeQualification']['devices']['apple,j413'] = {
                        'evidenceDigest': edge_train.digest(altered[name])}
                with self.assertRaisesRegex(ValueError, message):
                    edge_train.verify_devices(catalog, altered)
                catalog['edgeQualification']['devices']['apple,j413'] = {'evidenceDigest': edge_train.digest(records[name])}
        catalog['edgeQualification']['devices']['apple,j813'] = {'evidenceDigest': 'sha256:' + 'a' * 64}
        with self.assertRaisesRegex(ValueError, 'not listed'):
            edge_train.verify_devices(catalog, records)

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
                                      self.cohort, other, 'omarchy-hyprland', self.evidence)


if __name__ == '__main__':
    unittest.main()
