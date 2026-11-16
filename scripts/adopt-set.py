#!/usr/bin/env python3
"""Validate a delivery set with its model cohort before the workflow commits."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def adopt_set(items, cohort, root=ROOT, run=subprocess.run):
    if (cohort not in ('current', 'models58', 'models65') or not isinstance(items, list)
            or not 1 <= len(items) <= 10
            or any(not isinstance(item, dict) or set(item) != {'repository', 'tag'}
                   or not isinstance(item['repository'], str) or not isinstance(item['tag'], str)
                   for item in items)):
        raise ValueError('invalid delivery set or model cohort')
    if len({(item['repository'], item['tag']) for item in items}) != len(items):
        raise ValueError('duplicate delivery in set')
    restore_manifest = None
    if cohort == 'models65':
        required = {('aurora-silicon/' + repository, prefix + '-' + desktop)
                    for prefix, repository in [('fedora', 'fedora-aurora'), ('arch', 'alarm-aurora'),
                                               ('nixos', 'nixos-aurora')]
                    for desktop in ('kde', 'gnome', 'minimal')}
        required.add(('aurora-silicon/omarchy-aurora', 'omarchy-hyprland'))
        actual = set()
        for item in items:
            core = re.fullmatch(r'payload-((?:fedora|arch|nixos)-(?:kde|gnome|minimal))-'
                                r'[1-9][0-9]*-models65', item['tag'])
            omarchy = re.fullmatch(r'payload-(omarchy-hyprland)-[1-9][0-9]*', item['tag'])
            if core or omarchy:
                actual.add((item['repository'], (core or omarchy)[1]))
        if len(items) != 10 or actual != required:
            raise ValueError('65-model adoption requires the complete ten-profile set: '
                             'nine models65 variants and one normal Omarchy delivery')
        current = root / 'keys/supported-models.json'
        target = root / 'keys/supported-models-65.json'
        before, after = json.loads(current.read_text()), json.loads(target.read_text())
        if (len(after['supported']) != 65 or len(set(after['supported'])) != 65
                or not set(before['supported']) <= set(after['supported'])
                or not set(before.get('stage1_caution', {})) <= set(after.get('stage1_caution', {}))):
            raise ValueError('65-model cohort drops an existing model or caution')
        restore_manifest = (current, current.read_bytes())
        shutil.copyfile(target, current)
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
    try:
        for item in items:
            run(['python3', str(root / 'scripts/adopt-delivery.py'), '--repository', item['repository'],
                 '--tag', item['tag']], check=True)
        run(['bash', str(root / 'scripts/verify-catalogs')], check=True)
    except Exception:
        if restore_manifest is not None:
            path, previous = restore_manifest
            path.write_bytes(previous)
        raise



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deliveries', required=True)
    parser.add_argument('--model-cohort', choices=['current', 'models58', 'models65'], default='current')
    args = parser.parse_args()
    adopt_set(json.loads(args.deliveries), args.model_cohort)
