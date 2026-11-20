#!/usr/bin/env python3
"""Bind one staged Edge train payload to the owner evidence before catalog signing.

`stage` prints the staging run and STAGING.sha256 digest the merged train
records for this profile. `bind` turns the staged template into the catalog the
signer signs, after every train, image, metadata, channel and source check.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import edge_train  # noqa: E402
from channel_policy import verify_edge_delivery, verify_edge_source_refs  # noqa: E402


def stage_record(cohort, train, profile, repository, image_run):
    edge_train.verify_train(train, cohort, staged=True)
    if profile not in cohort['profiles'] or edge_train.owner(profile) != repository:
        raise ValueError('the calling repository is not the owner of ' + str(profile))
    if image_run != str(train['profiles'][profile]['image_run']):
        raise ValueError('the image run differs from the merged train')
    return train['profiles'][profile]['stage']


def bind_assets(assets, evidence, cohort, train, profile):
    edge_train.verify_evidence(evidence, cohort, train)
    catalog = json.loads((assets / 'catalog-template.json').read_text())
    receipt = json.loads((assets / 'delivery.json').read_text())
    run = json.loads((assets / 'image-build.json').read_text())
    proof = (assets / 'edge-image-proof.json').read_bytes()
    metadata = (assets / ('installer-data-' + profile + '.json')).read_bytes()
    report = edge_train.bind(catalog, receipt, run, proof, evidence, cohort, train, profile)
    base = 'https://github.com/%s/releases/download/%s' % (edge_train.owner(profile),
                                                          edge_train.release_tag(train, profile))
    verify_edge_delivery(catalog, report, receipt, run, base)
    edge_train.verify_binding(catalog, receipt, run, proof, cohort, train, profile)
    edge_train.verify_metadata(catalog, metadata, cohort, train, profile)
    verify_edge_source_refs(catalog['edgeQualification'])
    (assets / 'edge-qualification.json').write_bytes(report)
    for name, document in (('catalog.json', catalog), ('delivery.json', receipt)):
        (assets / name).write_text(json.dumps(document, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['stage', 'bind'])
    parser.add_argument('--train', type=Path, required=True)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--repository')
    parser.add_argument('--image-run')
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--assets', type=Path)
    args = parser.parse_args()
    cohort_record, train_record = edge_train.load(args.cohort), edge_train.load(args.train)
    if args.command == 'stage':
        record = stage_record(cohort_record, train_record, args.profile, args.repository, args.image_run)
        print('STAGE_RUN=%d\nSTAGE_SHA256=%s\nEDGE_RELEASE=%s' % (record['run'], record['sha256'], train_record['release']))
    else:
        bind_assets(args.assets, json.loads(args.evidence.read_text()), cohort_record, train_record, args.profile)
