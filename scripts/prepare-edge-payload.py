#!/usr/bin/env python3
"""Assemble one profile's unsigned Edge train payload and catalog template. Creates no release.

The image must be the train's `images.yml` run on the distribution's main
branch, built from the train's Edge snapshot and following the Edge feed
(`edge-image-proof.json` schema 2). The install policy and the catalog rows are
restricted to the train cohort for this profile.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import edge_train  # noqa: E402


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def restrict_metadata(metadata, models):
    """Admit exactly the cohort's boards, each with its Stage 1 admission and board Stage 2."""
    template, = metadata['os_list']
    boards = [edge_train.board(model) for model in models]
    stage1 = template['aurora_stage1']['qualified_machines']
    members = template['aurora_stage2']['board_members']
    for board in boards:
        if not stage1.get(board) or not members.get(board):
            raise ValueError(board + ' Stage 1 or Stage 2 metadata is missing')
    template['allowed_device_classes'] = boards
    template['aurora_stage1']['qualified_machines'] = {board: stage1[board] for board in boards}
    return metadata


def select_rows(catalog, models):
    if catalog.pop('channel', None) not in (None, 'testing') or 'edgeQualification' in catalog:
        raise ValueError('unexpected channel or qualification in the generated catalog')
    rows = {row['deviceIdentifier']: row for row in catalog['models']}
    if not set(models) <= set(rows):
        raise ValueError('the assembler did not return all of the cohort model rows')
    catalog['models'] = [rows[model] for model in models]
    return catalog


def assemble(producer, filesystems, recovery, image_run, image_proof, assets, profile, cohort, train):
    edge_train.verify_train(train, cohort)
    run = json.loads(image_run.read_text())
    proof = image_proof.read_bytes()
    edge_train.verify_image(run, proof, cohort, train, profile)
    native = producer / 'macOS'
    models = edge_train.profile_models(cohort, train, profile)
    release_id = edge_train.release_id(train, profile)
    base = 'https://github.com/%s/releases/download/%s' % (edge_train.owner(profile), edge_train.release_tag(train, profile))
    subprocess.run(['python3', str(native / 'scripts/assemble-distribution-payload.py'),
                    '--filesystems', str(filesystems), '--recovery-app', str(recovery),
                    '--output', str(assets), '--release-id', release_id], check=True)
    meta = assets / ('installer-data-' + profile + '.json')
    write(meta, restrict_metadata(json.loads(meta.read_text()), models))
    receipt_path = assets / 'delivery.json'
    receipt = json.loads(receipt_path.read_text())
    if receipt['release_id'] != release_id or receipt['installation_profile'] != profile:
        raise ValueError('the assembler returned a different delivery')
    receipt['artifacts'][meta.name] = {'bytes': meta.stat().st_size,
                                       'sha256': hashlib.sha256(meta.read_bytes()).hexdigest()}
    write(receipt_path, receipt)
    payload = assets / (profile + '-' + release_id + '.zip')
    if payload.stat().st_size >= 1_900_000_000:
        subprocess.run(['split', '-b', '1073741824', '--numeric-suffixes=0', '--suffix-length=2',
                        str(payload), str(payload) + '.part'], check=True)
    inputs = {
        'schema_version': 5, 'installation_profile': profile,
        'payload_name': payload.name, 'metadata_name': meta.name,
        'evidence_revision': 'image-build-%d' % run['id'],
        'asahi_installer_tag': 'v0.9.2',
        'asahi_installer_revision': 'dffbb38ef0c00c0431c609ecd8a00f42deb5b24c',
        'asahi_installer_data_revision': '42648e71423eba308d2e3e6228253eff679b068b',
        'downstream_revision': subprocess.check_output(['git', '-C', str(producer), 'rev-parse', 'HEAD'],
                                                       text=True).strip(),
        'device_identifiers': json.loads((native / 'scripts/supported-models.json').read_text())['supported'],
        'installer': {'minimum_version': '0.1.0', 'latest_version': '0.1.0',
                      'download_url': 'https://github.com/aurora-silicon/aurora-installer/releases'},
    }
    input_path = assets / 'catalog-inputs.json'
    write(input_path, inputs)
    catalog_path = assets / 'catalog-template.json'
    subprocess.run(['python3', str(native / 'scripts/make-unsigned-catalog.py'), '--base-url', base,
                    '--assets-dir', str(assets), '--inputs', str(input_path), '--output', str(catalog_path)],
                   check=True)
    catalog = select_rows(json.loads(catalog_path.read_text()), models)
    edge_train.verify_metadata(catalog, meta.read_bytes(), cohort, train, profile)
    write(catalog_path, catalog)
    input_path.unlink()
    (assets / 'image-build.json').write_bytes(image_run.read_bytes())
    (assets / 'edge-image-proof.json').write_bytes(proof)
    key = assets / 'trust-root.ed25519.pub'
    key.write_bytes((native / 'Release/trust-root.ed25519.pub').read_bytes())
    subprocess.run(['bash', str(native / 'scripts/make-release-descriptor'), '--adhoc',
                    '--public-key', str(key), '--base-url',
                    'https://aurorasilicon.org/aurora-distributions/installer/Profiles/' + profile,
                    '--output', str(assets / 'release.json')], check=True)
    if (assets / (payload.name + '.part00')).exists():
        payload.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('producer', 'filesystems', 'recovery', 'image-run', 'image-proof', 'assets'):
        parser.add_argument('--' + option, type=Path, required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--train', type=Path, default=edge_train.TRAIN)
    parser.add_argument('--cohort', type=Path, default=edge_train.COHORT)
    args = parser.parse_args()
    assemble(args.producer, args.filesystems, args.recovery, args.image_run, args.image_proof, args.assets,
             args.profile, edge_train.load(args.cohort), edge_train.load(args.train))
