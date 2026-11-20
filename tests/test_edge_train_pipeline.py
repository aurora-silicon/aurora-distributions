import copy
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import edge_fixtures as fx  # noqa: E402
from edge_fixtures import edge_train  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


prepare = module('prepare_edge_payload', 'scripts/prepare-edge-payload.py')
binder = module('bind_edge_train', 'scripts/bind-edge-train.py')


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.cohort, self.train = fx.cohort(), fx.train()

    def test_install_policy_is_restricted_to_the_cohort_boards(self):
        everything = json.loads((ROOT / 'keys/supported-models-65.json').read_text())['supported']
        metadata = fx.metadata(everything)
        models = edge_train.profile_models(self.cohort, self.train, 'nixos-gnome')
        prepare.restrict_metadata(metadata, models)
        template, = metadata['os_list']
        boards = [edge_train.board(model) for model in models]
        self.assertEqual(template['allowed_device_classes'], boards)
        self.assertEqual(set(template['aurora_stage1']['qualified_machines']), set(boards))
        self.assertNotIn('j714sap', template['aurora_stage1']['qualified_machines'])

    def test_a_cohort_board_without_stage1_or_stage2_fails_staging(self):
        models = edge_train.profile_models(self.cohort, self.train, 'arch-kde')
        for part in ('aurora_stage1', 'aurora_stage2'):
            with self.subTest(part=part):
                metadata = fx.metadata(models)
                template = metadata['os_list'][0]
                key = 'qualified_machines' if part == 'aurora_stage1' else 'board_members'
                del template[part][key]['j613ap']
                with self.assertRaisesRegex(ValueError, 'j613ap'):
                    prepare.restrict_metadata(metadata, models)

    def test_catalog_rows_are_selected_in_cohort_order(self):
        catalog, _, _ = fx.template(self.train, 'fedora-minimal')
        catalog['channel'] = 'testing'
        models = edge_train.profile_models(self.cohort, self.train, 'fedora-minimal')
        rows = prepare.select_rows(catalog, models)
        self.assertEqual([row['deviceIdentifier'] for row in rows['models']], models)
        self.assertNotIn('channel', rows)
        catalog['models'] = catalog['models'][1:]
        with self.assertRaisesRegex(ValueError, 'model rows'):
            prepare.select_rows(catalog, models)

    def test_staging_refuses_an_image_outside_the_train(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            run = fx.image_run(self.train, 'arch-kde')
            run['head_branch'] = 'edge-staging/linux-x'
            (directory / 'image-build.json').write_text(json.dumps(run))
            (directory / 'edge-image-proof.json').write_text(json.dumps(fx.image_proof(self.train, 'arch-kde')))
            with self.assertRaisesRegex(ValueError, 'image run'):
                prepare.assemble(directory, directory, directory, directory / 'image-build.json',
                                 directory / 'edge-image-proof.json', directory / 'assets', 'arch-kde',
                                 self.cohort, self.train)


class BinderTests(unittest.TestCase):
    def setUp(self):
        self.cohort, self.train = fx.cohort(), fx.staged(fx.train())
        self.evidence = fx.evidence(self.train)

    def test_stage_record_comes_only_from_the_merged_staged_train(self):
        record = binder.stage_record(self.cohort, self.train, 'nixos-kde', 'aurora-silicon/nixos-aurora',
                                     str(self.train['profiles']['nixos-kde']['image_run']))
        self.assertEqual(record, self.train['profiles']['nixos-kde']['stage'])
        for repository, run, message in (('aurora-silicon/alarm-aurora', None, 'owner'),
                                         ('aurora-silicon/nixos-aurora', '1', 'image run')):
            with self.subTest(repository=repository):
                with self.assertRaisesRegex(ValueError, message):
                    binder.stage_record(self.cohort, self.train, 'nixos-kde', repository,
                                        run or str(self.train['profiles']['nixos-kde']['image_run']))
        unstaged = fx.train()
        with self.assertRaisesRegex(ValueError, 'staging record'):
            binder.stage_record(self.cohort, unstaged, 'nixos-kde', 'aurora-silicon/nixos-aurora',
                                str(unstaged['profiles']['nixos-kde']['image_run']))

    def test_bind_writes_a_verified_signed_input(self):
        profile = 'arch-minimal'
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary)
            catalog, receipt, raw_metadata = fx.template(self.train, profile)
            (assets / 'catalog-template.json').write_text(json.dumps(catalog))
            (assets / 'delivery.json').write_text(json.dumps(receipt))
            (assets / 'image-build.json').write_text(json.dumps(fx.image_run(self.train, profile)))
            (assets / 'edge-image-proof.json').write_text(json.dumps(fx.image_proof(self.train, profile)))
            (assets / ('installer-data-' + profile + '.json')).write_bytes(raw_metadata)
            with patch.object(binder, 'verify_edge_source_refs', return_value=None):
                binder.bind_assets(assets, self.evidence, self.cohort, self.train, profile)
            signed = json.loads((assets / 'catalog.json').read_text())
            self.assertEqual(signed['channel'], 'edge')
            self.assertEqual(len(signed['models']), 35)
            report = (assets / 'edge-qualification.json').read_bytes()
            self.assertEqual(signed['edgeQualification']['evidenceDigest'], edge_train.digest(report))
            self.assertEqual(json.loads((assets / 'delivery.json').read_text())['release_channel'], 'edge')
            records = {path.name: path.read_bytes() for path in assets.glob('edge-device-*.json')}
            self.assertEqual(set(records), {edge_train.device_record_name(model)
                                            for model in edge_train.profile_models(self.cohort, self.train, profile)})
            edge_train.verify_devices(signed, records)

    def test_bind_refuses_incomplete_evidence_before_writing(self):
        profile = 'fedora-kde'
        evidence = copy.deepcopy(self.evidence)
        evidence['families']['t602x']['checks']['suspend']['status'] = 'pending'
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary)
            catalog, receipt, raw_metadata = fx.template(self.train, profile)
            (assets / 'catalog-template.json').write_text(json.dumps(catalog))
            (assets / 'delivery.json').write_text(json.dumps(receipt))
            (assets / 'image-build.json').write_text(json.dumps(fx.image_run(self.train, profile)))
            (assets / 'edge-image-proof.json').write_text(json.dumps(fx.image_proof(self.train, profile)))
            (assets / ('installer-data-' + profile + '.json')).write_bytes(raw_metadata)
            with self.assertRaisesRegex(ValueError, 'passing'):
                binder.bind_assets(assets, evidence, self.cohort, self.train, profile)
            self.assertFalse((assets / 'catalog.json').exists())


class WorkflowContractTests(unittest.TestCase):
    def test_staging_covers_every_profile_and_publishes_nothing(self):
        text = (ROOT / '.github/workflows/stage-edge-train.yml').read_text()
        cohort = fx.cohort()
        for profile in cohort['profiles']:
            self.assertIn('- ' + profile + '\n', text)
        self.assertNotIn('secrets.', text)
        self.assertNotIn('release create', text)
        self.assertNotIn('contents: write', text)
        self.assertIn('fail-fast: false', text)
        self.assertIn('scripts/prepare-edge-payload.py', text)

    def test_adoption_workflow_runs_only_the_atomic_train_adopter(self):
        text = (ROOT / '.github/workflows/adopt-edge-set.yml').read_text()
        self.assertIn('python3 scripts/adopt-edge-set.py', text)
        self.assertNotIn('adopt-delivery.py', text)
        self.assertIn('bash scripts/verify-catalogs', text)
        self.assertIn('group: distribution-profile-adoption', text)

    def test_signer_binds_the_train_before_the_signing_job(self):
        text = (ROOT / '.github/workflows/build-payload.yml').read_text()
        payload = text.split('\n  sign:\n', 1)[0]
        self.assertIn('edge-train-revision:', text)
        self.assertIn('scripts/bind-edge-train.py', payload)
        self.assertIn('compare/$EDGE_TRAIN_REVISION...main', payload)
        sign = text.split('\n  sign:\n', 1)[1].split('\n  publish:', 1)[0]
        self.assertIn('needs: payload', sign)
        # Every Testing assembly step stays off on the train path.
        for step in re.findall(r"if: inputs\.neo-edge-evidence == ''[^\n]*", payload):
            self.assertIn("inputs.edge-train-revision == ''", step)


if __name__ == '__main__':
    unittest.main()
