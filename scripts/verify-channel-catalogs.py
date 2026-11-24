#!/usr/bin/env python3
"""Authenticate served channel envelopes and enforce their channel identity."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from channel_policy import validate_catalog, legacy_development_catalog
import edge_train


def verify_channels(site, public_key):
    public = public_key.read_bytes()
    if len(public) != 32:
        raise ValueError('invalid catalog public key')
    count = 0
    with tempfile.TemporaryDirectory(prefix='aurora-channel-verify-') as temporary:
        work = Path(temporary)
        (work/'key.der').write_bytes(bytes.fromhex('302a300506032b6570032100')+public)
        for path in sorted(site.rglob('catalog.signed.json')):
            if path.parent.parent.name != 'channels':
                continue
            lane = path.parent.name
            channel = {'edge': 'testing', 'edge-alpha': 'edge', 'testing': 'testing',
                       'stable': 'stable', 'rc': 'rc'}.get(lane)
            if channel is None or path.is_symlink() or path.stat().st_size > 1500000:
                raise ValueError('unknown or unsafe channel envelope: '+str(path))
            envelope = json.loads(path.read_text())
            if (set(envelope) != {'schema_version', 'catalog', 'signature'}
                    or type(envelope['schema_version']) is not int or envelope['schema_version'] != 1):
                raise ValueError('invalid channel envelope: '+str(path))
            raw = base64.b64decode(envelope['catalog'], validate=True)
            signature = base64.b64decode(envelope['signature'], validate=True)
            if not 0 < len(raw) <= 1048576 or len(signature) != 64:
                raise ValueError('invalid channel catalog size')
            (work/'catalog').write_bytes(raw)
            (work/'signature').write_bytes(signature)
            subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-keyform', 'DER',
                            '-inkey', str(work/'key.der'), '-rawin', '-in', str(work/'catalog'),
                            '-sigfile', str(work/'signature')], check=True, capture_output=True)
            catalog = json.loads(raw)
            validate_catalog(catalog, channel, legacy_development=legacy_development_catalog(catalog))
            plain = path.parent/'catalog.json'
            if plain.exists() or plain.is_symlink():
                detached = path.parent/'catalog.json.sig'
                if (plain.is_symlink() or detached.is_symlink() or not detached.is_file()
                        or plain.read_bytes() != raw or detached.read_bytes() != signature):
                    raise ValueError('channel envelope and raw catalog differ')
            if channel == 'edge' and catalog['models']:
                proof = catalog['edgeQualification']
                report = path.parent/'edge-qualification.json'
                if report.is_symlink() or not report.is_file() or report.stat().st_size > 1048576:
                    raise ValueError('Edge is missing bounded detached qualification evidence')
                evidence = report.read_bytes()
                if ('sha256:'+hashlib.sha256(evidence).hexdigest() != proof['evidenceDigest']
                        or json.loads(evidence) != {k: v for k, v in proof.items() if k != 'evidenceDigest'}):
                    raise ValueError('Edge detached qualification differs from its signed catalog')
                if 'devices' in proof:
                    records = {}
                    for model in proof['devices']:
                        record = path.parent/edge_train.device_record_name(model)
                        if record.is_file() and not record.is_symlink() and record.stat().st_size <= 1048576:
                            records[record.name] = record.read_bytes()
                    edge_train.verify_devices(catalog, records)
            count += 1
    verify_train_record(site)
    return count


def _served(path):
    envelope = json.loads(path.read_text())
    return base64.b64decode(envelope['catalog'], validate=True)


def verify_train_record(site):
    """Once a train is adopted, every profile serves exactly that train's catalog."""
    record_path = site/'installer/edge-train.json'
    if not record_path.exists():
        return
    if record_path.is_symlink() or record_path.stat().st_size > 1048576:
        raise ValueError('unsafe Edge train record')
    record = json.loads(record_path.read_text())
    version = record.get('schema_version')
    edge_train.verify_distributions(record.get('distributions'), version)
    edge_train.verify_source_pair(record.get('pair'), version)
    profiles = edge_train.train_profiles(edge_train.load(ROOT/'edge/cohort.json'), record)
    if set(record.get('profiles', {})) != set(profiles):
        raise ValueError('the Edge train record must name every Edge profile')
    for profile in profiles:
        envelope = site/'installer/Profiles'/profile/'channels/edge-alpha/catalog.signed.json'
        if not envelope.is_file():
            raise ValueError('Edge profile missing from the adopted train record: '+profile)
        raw = _served(envelope)
        catalog = json.loads(raw)
        qualification = catalog.get('edgeQualification', {})
        if (hashlib.sha256(raw).hexdigest() != record['profiles'][profile]['catalogSHA256']
                or not catalog['models']
                or qualification.get('edgeTrain', {}).get('trainDigest') != record['trainDigest']
                or {name: qualification.get(name) for name in record['pair']} != record['pair']):
            raise ValueError('served Edge catalog differs from the adopted train record: '+profile)
    fallback = site/'installer/channels/edge-alpha/catalog.signed.json'
    kde = site/'installer/Profiles/fedora-kde/channels/edge-alpha/catalog.signed.json'
    if not fallback.is_file() or fallback.read_bytes() != kde.read_bytes():
        raise ValueError('the Edge root fallback differs from the adopted train record')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site', type=Path, default=ROOT/'site')
    parser.add_argument('--public-key', type=Path, default=ROOT/'keys/catalog.ed25519.pub')
    args = parser.parse_args()
    print('Authenticated channel envelopes:', verify_channels(args.site, args.public_key))
