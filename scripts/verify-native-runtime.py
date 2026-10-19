#!/usr/bin/env python3
"""Match an already signature-authenticated native runtime to its source checkout."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def tree(checkout, part):
    return subprocess.check_output(
        ['git', '-C', str(checkout), 'rev-parse', 'HEAD:macOS/' + part], text=True).strip()


def verify(runtime, checkout):
    manifest = json.loads((runtime / 'runtime-manifest.json').read_text())
    if manifest['schema'] != 1:
        raise ValueError('unsupported runtime manifest')
    for part, key in (('Engine', 'engine_tree'), ('Recovery', 'recovery_tree')):
        if tree(checkout, part) != manifest[key]:
            raise ValueError('released runtime differs from current ' + part + ' sources')
    lock = json.loads((checkout / 'macOS/Engine/source-lock.json').read_text())['validation_artifact']
    expected = {lock['filename'], 'aurora-recovery-template-20261018.tar.gz'}
    if set(manifest['assets']) != expected:
        raise ValueError('runtime asset inventory differs')
    records = {}
    for name, identity in manifest['assets'].items():
        path = runtime / name
        if (Path(name).name != name or path.is_symlink() or not path.is_file()
                or not 0 < identity['bytes'] < 200_000_000
                or path.stat().st_size != identity['bytes']):
            raise ValueError('invalid runtime asset: ' + name)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != identity['sha256']:
            raise ValueError('runtime digest differs: ' + name)
        if name == lock['filename'] and (identity['bytes'] != lock['size_bytes']
                                         or digest != lock['sha256']):
            raise ValueError('runtime engine differs from the source lock')
        records[name] = identity
    return {'engine_tree': manifest['engine_tree'], 'recovery_tree': manifest['recovery_tree'],
            'assets': records, 'source_and_asset_verification': 'passed'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--checkout', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.runtime, args.checkout), indent=2))
