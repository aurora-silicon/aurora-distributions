#!/usr/bin/env python3
"""Choose the installer producer that builds a plain delivery's catalog.

adopt-delivery.py adopts a plain delivery only when its catalog enables exactly
the Macs in keys/supported-models.json on this repository's main branch: the
model cohort the delivery is adopted under. The producer writes the catalog's
models from its own macOS/scripts/supported-models.json, so a plain delivery is
built with the pinned producer whose list is that cohort. When main adopts
another cohort, the same lookup selects the producer that lists it.

A cohort that no pinned producer lists exactly, or that more than one lists, is
refused, as is a list that is not a schema 1 supported-models list. Nothing is
written to --output unless one producer is selected.
"""
import argparse
import json
import subprocess

# The pinned producers a plain delivery can be built with: aurora-installer
# 9779fa45 lists the 58 Macs of keys/supported-models-58.json, and 68148f7b the
# 65 of keys/supported-models-65.json.
PRODUCERS = ('9779fa452351c5a66ae583918b8e8120c044051f',
             '68148f7b1b820059f65780e17bc27ccb576def49')
COHORT_REPOSITORY = 'aurora-silicon/aurora-distributions'
COHORT_PATH = 'keys/supported-models.json'
PRODUCER_REPOSITORY = 'aurora-silicon/aurora-installer'
PRODUCER_PATH = 'macOS/scripts/supported-models.json'


def gh_fetch(repository, path, revision):
    return subprocess.run(['gh', 'api', '-H', 'Accept: application/vnd.github.raw',
                           f'repos/{repository}/contents/{path}?ref={revision}'],
                          check=True, capture_output=True).stdout


def gh_head(repository):
    return subprocess.run(['gh', 'api', f'repos/{repository}/commits/main', '--jq', '.sha'],
                          check=True, capture_output=True, text=True).stdout.strip()


def supported(raw, name):
    try:
        document = json.loads(raw)
    except ValueError:
        document = None
    models = document.get('supported') if isinstance(document, dict) else None
    if (not isinstance(document, dict) or document.get('schema_version') != 1
            or not isinstance(models, list) or not models
            or not all(isinstance(model, str) for model in models) or len(set(models)) != len(models)):
        raise ValueError(f'{name} is not a supported-models list')
    return frozenset(models)


def select(cohort, manifest):
    """The pinned producer whose list is the cohort, and the cohort's models.
    `manifest(revision)` returns that producer's supported-models.json bytes."""
    wanted = supported(cohort, COHORT_PATH)
    matches = [revision for revision in PRODUCERS
               if supported(manifest(revision), f'{PRODUCER_PATH} at {revision}') == wanted]
    if not matches:
        raise ValueError(f'no pinned installer producer lists exactly the {len(wanted)} Macs of {COHORT_PATH}')
    if len(matches) > 1:
        raise ValueError(f'more than one pinned installer producer lists the Macs of {COHORT_PATH}: '
                         + ', '.join(matches))
    return matches[0], wanted


def main(argv=None, fetch=gh_fetch, head=gh_head):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='file the step outputs are appended to')
    args = parser.parse_args(argv)
    cohort_revision = head(COHORT_REPOSITORY)
    if len(cohort_revision) != 40 or any(c not in '0123456789abcdef' for c in cohort_revision):
        raise ValueError(f'{COHORT_REPOSITORY} main is not a commit: {cohort_revision!r}')
    cohort = fetch(COHORT_REPOSITORY, COHORT_PATH, cohort_revision)
    revision, models = select(cohort, lambda producer: fetch(PRODUCER_REPOSITORY, PRODUCER_PATH, producer))
    with open(args.output, 'a') as output:
        output.write(f'revision={revision}\ncohort-revision={cohort_revision}\nmodels={len(models)}\n')
    print(f'Plain delivery producer: {PRODUCER_REPOSITORY}@{revision}, which lists the {len(models)} Macs '
          f'of {COHORT_PATH} at {COHORT_REPOSITORY}@{cohort_revision}.')


if __name__ == '__main__':
    main()
