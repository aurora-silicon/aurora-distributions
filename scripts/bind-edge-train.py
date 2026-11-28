#!/usr/bin/env python3
"""Bind one staged Edge train payload to the reviewed train record before catalog signing.

`stage` prints the staging run and STAGING.sha256 digest the merged train
records for this profile. `verify-stage` checks that run against the train
revision. `bind` turns the staged template into the catalog the signer signs,
with the evidence of the train record (edge/evidence.json and its published
manifests), after every train, image, metadata, channel and source check.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import edge_train  # noqa: E402
from channel_policy import verify_edge_delivery, verify_edge_source_refs  # noqa: E402


REPOSITORY = 'aurora-silicon/aurora-distributions'
STAGE_WORKFLOW = '.github/workflows/stage-edge-train.yml'
# The code that assembled the staged payload must be the code of the train revision.
STAGE_FILES = (STAGE_WORKFLOW, 'scripts/prepare-edge-payload.py', 'scripts/edge_train.py',
               'scripts/channel_policy.py', 'edge/cohort.json')


def github_json(path):
    return json.loads(subprocess.run(['gh', 'api', path], check=True, capture_output=True, text=True).stdout)


def verify_stage_run(run, revision, fetch=github_json):
    """The staging run ran the train revision's staging code, from that revision's history."""
    if (run.get('conclusion') != 'success' or run.get('path') != STAGE_WORKFLOW
            or (run.get('head_repository') or {}).get('full_name') != REPOSITORY):
        raise ValueError('not a successful Edge staging run of ' + REPOSITORY)
    head = run.get('head_sha', '')
    comparison = fetch('repos/%s/compare/%s...%s' % (REPOSITORY, head, revision))
    if (comparison.get('status') not in ('ahead', 'identical')
            or (comparison.get('base_commit') or {}).get('sha') != head):
        raise ValueError('the staging run is not in the history of the train revision')
    for path in STAGE_FILES:
        staged = fetch('repos/%s/contents/%s?ref=%s' % (REPOSITORY, path, head)).get('sha')
        reviewed = fetch('repos/%s/contents/%s?ref=%s' % (REPOSITORY, path, revision)).get('sha')
        if not staged or staged != reviewed:
            raise ValueError('the staging run used another %s than the train revision' % path)
    return head


def stage_record(cohort, train, profile, repository, image_run):
    edge_train.verify_train(train, cohort, staged=True)
    if profile not in train['profiles'] or edge_train.owner(profile) != repository:
        raise ValueError('the calling repository is not the owner of ' + str(profile))
    if image_run != str(train['profiles'][profile]['image_run']):
        raise ValueError('the image run differs from the merged train')
    return train['profiles'][profile]['stage']


def bind_assets(assets, evidence, manifests, cohort, train, profile):
    edge_train.verify_evidence(evidence, cohort, train)
    edge_train.verify_manifests(evidence, manifests)
    catalog = json.loads((assets / 'catalog-template.json').read_text())
    receipt = json.loads((assets / 'delivery.json').read_text())
    run = json.loads((assets / 'image-build.json').read_text())
    proof = (assets / 'edge-image-proof.json').read_bytes()
    metadata = (assets / ('installer-data-' + profile + '.json')).read_bytes()
    report = edge_train.bind(catalog, receipt, run, proof, evidence, cohort, train, profile)
    base = 'https://github.com/%s/releases/download/%s' % (edge_train.owner(profile),
                                                          edge_train.release_tag(train, profile))
    verify_edge_delivery(catalog, report, receipt, run, base)
    edge_train.verify_binding(catalog, receipt, run, proof, cohort, train, profile, evidence)
    edge_train.verify_metadata(catalog, metadata, cohort, train, profile)
    devices = edge_train.device_evidence(catalog, cohort, train, profile)
    edge_train.verify_devices(catalog, devices)
    verify_edge_source_refs(catalog['edgeQualification'])
    for name, record in devices.items():
        (assets / name).write_bytes(record)
    (assets / 'edge-qualification.json').write_bytes(report)
    for name, document in (('catalog.json', catalog), ('delivery.json', receipt)):
        (assets / name).write_text(json.dumps(document, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['stage', 'verify-stage', 'bind'])
    parser.add_argument('--train', type=Path, required=True)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--revision', help='the train revision (verify-stage)')
    parser.add_argument('--run-id', help='the staging run (verify-stage)')
    parser.add_argument('--manifests', type=Path, help="the train revision's edge/manifests")
    parser.add_argument('--repository')
    parser.add_argument('--image-run')
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--assets', type=Path)
    args = parser.parse_args()
    cohort_record, train_record = edge_train.load(args.cohort), edge_train.load(args.train)
    if args.command == 'verify-stage':
        head = verify_stage_run(github_json('repos/%s/actions/runs/%s' % (REPOSITORY, args.run_id)), args.revision)
        print('STAGE_HEAD=' + head)
    elif args.command == 'stage':
        record = stage_record(cohort_record, train_record, args.profile, args.repository, args.image_run)
        print('STAGE_RUN=%d\nSTAGE_SHA256=%s\nEDGE_RELEASE=%s' % (record['run'], record['sha256'], train_record['release']))
    else:
        manifests = {path.name.removesuffix('.sha256'): path.read_bytes()
                     for path in sorted(args.manifests.glob('*.sha256')) if path.is_file()}
        bind_assets(args.assets, json.loads(args.evidence.read_text()), manifests, cohort_record, train_record,
                    args.profile)
