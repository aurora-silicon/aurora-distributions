#!/usr/bin/env python3
"""Adopt one complete Edge train into the installer resource tree: every profile, or nothing.

Each of the ten deliveries must be the signed, attested, qualified release the
merged train names (`payload-<profile>-<image run>-edge-alpha.<n>`). All are
downloaded and verified first; the new served state is assembled and verified
beside the site, and only then replaces the profiles' Edge directories, the
Fedora KDE root fallback and the train record `site/installer/edge-train.json`.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import edge_train  # noqa: E402
from channel_policy import (CHANNELS, descriptor as channel_descriptor, validate_catalog,  # noqa: E402
                            verify_edge_delivery, verify_edge_source_refs)


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_delivery = _module('adopt_delivery_for_edge_train', ROOT / 'scripts/adopt-delivery.py')
_channels = _module('verify_channels_for_edge_train', ROOT / 'scripts/verify-channel-catalogs.py')
verify_provenance = _delivery.verify_provenance
VM_CHECKS = _delivery.VM_CHECKS
PUBLIC_BASE = 'https://aurorasilicon.org/aurora-distributions/installer/Profiles/'
RECORD = Path('installer/edge-train.json')


def _envelope(path):
    if not path.is_file():
        return None
    envelope = json.loads(path.read_text())
    return json.loads(base64.b64decode(envelope['catalog'], validate=True))


def _verify_signature(trusted, raw, signature):
    with tempfile.TemporaryDirectory(prefix='aurora-edge-train-verify-') as temporary:
        directory = Path(temporary)
        (directory / 'public.der').write_bytes(bytes.fromhex('302a300506032b6570032100') + trusted)
        (directory / 'catalog.json').write_bytes(raw)
        (directory / 'catalog.json.sig').write_bytes(signature)
        subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-keyform', 'DER',
                        '-inkey', str(directory / 'public.der'), '-rawin', '-in', str(directory / 'catalog.json'),
                        '-sigfile', str(directory / 'catalog.json.sig')], check=True, capture_output=True)


def names(profile):
    result = ['release.json', 'trust-root.ed25519.pub', 'catalog.json', 'catalog.json.sig', 'delivery.json',
              'image-build.json', 'edge-qualification.json', 'edge-image-proof.json',
              'installer-data-' + profile + '.json']
    if profile == 'omarchy-hyprland':
        result.append('vm-result.json')
    return result


def verify_delivery(item, cohort, train, site, evidence):
    """Authenticate one train delivery exactly as the single-profile Edge path does."""
    profile, repository, tag = item['profile'], item['repository'], item['tag']
    base = 'https://github.com/' + repository + '/releases/download/' + tag
    data = {name: urllib.request.urlopen(base + '/' + name, timeout=60).read() for name in names(profile)}
    trusted = (ROOT / 'keys/catalog.ed25519.pub').read_bytes()
    if len(trusted) != 32 or data['trust-root.ed25519.pub'] != trusted or len(data['catalog.json.sig']) != 64:
        raise ValueError('delivery trust root differs from the installer root: ' + tag)
    _verify_signature(trusted, data['catalog.json'], data['catalog.json.sig'])
    if verify_provenance(repository, base, data) != 'verified':
        raise ValueError('Edge requires hosted payload and qualification provenance: ' + tag)
    receipt = json.loads(data['delivery.json'])
    if (receipt.get('installation_profile') != profile or receipt.get('private_neo_inputs') is not False
            or receipt.get('native_neo_reset_qa', False)):
        raise ValueError('delivery is not a public profile for this repository: ' + tag)
    if profile == 'omarchy-hyprland':
        vm = json.loads(data['vm-result.json'])
        if not all(vm.get(check) is True for check in VM_CHECKS):
            raise ValueError('Omarchy delivery has no complete passing VM qualification')
    run = json.loads(data['image-build.json'])
    catalog = json.loads(data['catalog.json'])
    models = catalog['models']
    if (catalog.get('schemaVersion') != 5
            or [model.get('deviceIdentifier') for model in models] != edge_train.profile_models(cohort, train, profile)
            or any(model.get('status') != 'enabled' or model.get('installationProfile') != profile
                   for model in models)):
        raise ValueError('catalog model coverage or profile binding differs: ' + tag)
    descriptor = json.loads(data['release.json'])
    if (descriptor.get('schema_version') != 3
            or descriptor.get('trust_root_fingerprint') != 'sha256:' + hashlib.sha256(trusted).hexdigest()):
        raise ValueError('profile descriptor differs from the pinned root: ' + tag)
    descriptor = json.loads(channel_descriptor(data['release.json']))
    for lane in CHANNELS:
        expected = PUBLIC_BASE + profile + '/channels/' + ('edge-alpha' if lane == 'edge' else lane) + '/catalog.signed.json'
        if descriptor['channels'][lane]['catalog_url'] != expected:
            raise ValueError('profile channel URL differs: ' + tag)
    validate_catalog(catalog, 'edge')
    testing_path = site / 'installer/Profiles' / profile / 'catalog.json'
    testing = json.loads(testing_path.read_text()) if testing_path.exists() else None
    verify_edge_delivery(catalog, data['edge-qualification.json'], receipt, run, base, testing)
    edge_train.verify_binding(catalog, receipt, run, data['edge-image-proof.json'], cohort, train, profile, evidence)
    edge_train.verify_metadata(catalog, data['installer-data-' + profile + '.json'], cohort, train, profile)
    devices = {edge_train.device_record_name(model): urllib.request.urlopen(
        base + '/' + edge_train.device_record_name(model), timeout=60).read()
        for model in catalog['edgeQualification'].get('devices', {})}
    edge_train.verify_devices(catalog, devices)
    data.update(devices)
    verify_edge_source_refs(catalog['edgeQualification'])
    return data, catalog


def adopt_train(items, cohort, train, site, evidence, manifests):
    ordered = edge_train.verify_set(items, cohort, train)
    edge_train.verify_evidence(evidence, cohort, train)
    edge_train.verify_manifests(evidence, manifests)
    served = {profile: _envelope(site / 'installer/Profiles' / profile / 'channels/edge-alpha/catalog.signed.json')
              for profile in cohort['profiles']}
    fallback = _envelope(site / 'installer/channels/edge-alpha/catalog.signed.json')
    verified = {}
    for item in ordered:
        profile = item['profile']
        data, catalog = verify_delivery(item, cohort, train, site, evidence)
        # Each profile keeps its own rollback floor; the train's ten catalogs may share
        # one sequence. The root fallback mirrors Fedora GNOME.
        served_profile = [served[profile]] + ([fallback] if profile == 'fedora-gnome' else [])
        floor = max([previous['sequence'] for previous in served_profile if previous] or [0])
        if catalog['sequence'] <= floor:
            raise ValueError('Edge catalog sequence %d must exceed the served %s Edge sequence (%d)'
                             % (catalog['sequence'], profile, floor))
        previous = served[profile] or {'models': []}
        # Only a reviewed `withdrawn` entry takes a served Mac off Edge.
        dropped = ({model['deviceIdentifier'] for model in previous['models']}
                   - {model['deviceIdentifier'] for model in catalog['models']} - set(train.get('withdrawn', [])))
        if dropped:
            raise ValueError('the train would remove %s from Edge %s' % (', '.join(sorted(dropped)), profile))
        verified[profile] = (item, data, catalog)

    record = {
        'schema_version': 1, 'release': train['release'], 'trainDigest': edge_train.train_digest(train),
        'cohortDigest': edge_train.digest(edge_train.canonical(cohort)), 'pair': train['pair'],
        'distributions': train['distributions'], 'profiles': {},
    }
    with tempfile.TemporaryDirectory(prefix='aurora-edge-train-site-') as temporary:
        staged = Path(temporary) / 'site'
        shutil.copytree(site, staged, symlinks=True)
        for profile, (item, data, catalog) in verified.items():
            target = staged / 'installer/Profiles' / profile / 'channels/edge-alpha'
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            for name, content in data.items():
                (target / name).write_bytes(channel_descriptor(content) if name == 'release.json' else content)
            (target / 'catalog.signed.json').write_text(json.dumps({
                'schema_version': 1, 'catalog': base64.b64encode(data['catalog.json']).decode(),
                'signature': base64.b64encode(data['catalog.json.sig']).decode()}, separators=(',', ':')) + '\n')
            record['profiles'][profile] = {
                'repository': item['repository'], 'tag': item['tag'], 'sequence': catalog['sequence'],
                'catalogSHA256': hashlib.sha256(data['catalog.json']).hexdigest(),
                'payloadDigest': catalog['models'][0]['payloadDigest']}
        default_edge = staged / 'installer/Profiles/fedora-gnome/channels/edge-alpha'
        root_channel = staged / 'installer/channels/edge-alpha'
        if root_channel.exists():
            shutil.rmtree(root_channel)
        root_channel.mkdir(parents=True)
        mirrored = ['catalog.signed.json', 'edge-qualification.json'] + sorted(
            path.name for path in default_edge.glob('edge-device-*.json'))
        for name in mirrored:
            (root_channel / name).write_bytes((default_edge / name).read_bytes())
        (staged / RECORD).write_text(json.dumps(record, indent=2) + '\n')
        _channels.verify_channels(staged, ROOT / 'keys/catalog.ed25519.pub')
        # Everything verified: replace the served Edge state.
        for profile in verified:
            relative = Path('installer/Profiles') / profile / 'channels/edge-alpha'
            if (site / relative).exists():
                shutil.rmtree(site / relative)
            shutil.copytree(staged / relative, site / relative)
        if (site / 'installer/channels/edge-alpha').exists():
            shutil.rmtree(site / 'installer/channels/edge-alpha')
        shutil.copytree(root_channel, site / 'installer/channels/edge-alpha')
        shutil.copyfile(staged / RECORD, site / RECORD)
    print('Adopted the qualified Edge train', train['release'], 'for', len(verified), 'profiles')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--train', type=Path, default=ROOT / 'edge/train.json')
    parser.add_argument('--cohort', type=Path, default=ROOT / 'edge/cohort.json')
    parser.add_argument('--deliveries', help='JSON array of repository/tag objects; default: the train\'s ten')
    parser.add_argument('--site', type=Path, default=ROOT / 'site')
    args = parser.parse_args()
    cohort_record, train_record = edge_train.load(args.cohort), edge_train.load(args.train)
    selection = (json.loads(args.deliveries) if args.deliveries else
                 [{'repository': edge_train.owner(profile), 'tag': edge_train.release_tag(train_record, profile)}
                  for profile in cohort_record['profiles']])
    evidence_record, manifest_files = edge_train.load_evidence(ROOT)
    adopt_train(selection, cohort_record, train_record, args.site, evidence_record, manifest_files)
