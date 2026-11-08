#!/usr/bin/env python3
"""Validate a delivery set with its model cohort before the workflow commits."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def adopt_set(items, cohort, root=ROOT, run=subprocess.run):
    if (cohort not in ('current', 'models58') or not isinstance(items, list)
            or not 1 <= len(items) <= 9
            or any(not isinstance(item, dict) or set(item) != {'repository', 'tag'} for item in items)):
        raise ValueError('invalid delivery set or model cohort')
    if len({(item['repository'], item['tag']) for item in items}) != len(items):
        raise ValueError('duplicate delivery in set')
    if cohort == 'models58':
        if len(items) != 9 or any(not item['tag'].endswith('-models58') for item in items):
            raise ValueError('58-model adoption requires the complete nine-profile variant set')
        current = root / 'keys/supported-models.json'
        target = root / 'keys/supported-models-58.json'
        before, after = json.loads(current.read_text()), json.loads(target.read_text())
        if (len(after['supported']) != 58
                or len(set(after['supported'])) != 58
                or not set(before['supported']) <= set(after['supported'])
                or not set(before.get('stage1_caution', {})) <= set(after.get('stage1_caution', {}))):
            raise ValueError('58-model cohort drops an existing model or caution')
        shutil.copyfile(target, current)
    for item in items:
        run(['python3', str(root / 'scripts/adopt-delivery.py'), '--repository', item['repository'],
             '--tag', item['tag']], check=True)
    run(['bash', str(root / 'scripts/verify-catalogs')], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deliveries', required=True)
    parser.add_argument('--model-cohort', choices=['current', 'models58'], default='current')
    args = parser.parse_args()
    adopt_set(json.loads(args.deliveries), args.model_cohort)
