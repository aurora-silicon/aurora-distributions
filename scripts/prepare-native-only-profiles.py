#!/usr/bin/env python3
"""Re-sign the existing nine image deliveries as engine-free native catalogs."""

import argparse
import base64
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PROFILES = tuple(f'{distribution}-{desktop}' for distribution in ('fedora', 'arch', 'nixos')
                 for desktop in ('kde', 'gnome', 'minimal'))
FILES = ('release.json', 'trust-root.ed25519.pub', 'catalog.json', 'catalog.json.sig',
         'delivery.json', 'image-build.json')
PUBLIC_DER_PREFIX = bytes.fromhex('302a300506032b6570032100')


def verify(catalog, signature, public):
    with tempfile.TemporaryDirectory(prefix='aurora-native-signature-') as temporary:
        directory = Path(temporary)
        (directory/'key.der').write_bytes(PUBLIC_DER_PREFIX + public)
        (directory/'catalog').write_bytes(catalog)
        (directory/'signature').write_bytes(signature)
        subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-keyform', 'DER',
                        '-inkey', str(directory/'key.der'), '-rawin', '-in', str(directory/'catalog'),
                        '-sigfile', str(directory/'signature')], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def fetch_metadata(url):
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read(1048577)
    if len(data) > 1048576:
        raise ValueError('metadata artifact exceeds the size limit')
    return data


def prepare(source, output, signing_key, release_tag, source_revision, fetch=fetch_metadata):
    match = re.fullmatch(r'v([0-9]+\.[0-9]+\.[0-9]+)-alpha\.([1-9][0-9]*)', release_tag)
    if match is None or output.exists() or not re.fullmatch(r'[0-9a-f]{40}', source_revision):
        raise ValueError('expected a source revision, new output and immutable installer tag')
    version = match.group(1)
    public = (ROOT/'keys/catalog.ed25519.pub').read_bytes()
    if len(public) != 32:
        raise ValueError('invalid pinned catalog root')
    key_der = subprocess.check_output(['openssl', 'pkey', '-in', str(signing_key),
                                       '-pubout', '-outform', 'DER'])
    if key_der != PUBLIC_DER_PREFIX + public:
        raise ValueError('catalog signing key differs from the pinned root')
    allowed = set(json.loads((ROOT/'keys/supported-models.json').read_text())['supported'])
    now = int(time.time())
    issued = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    url = ('https://github.com/aurora-silicon/aurora-installer/releases/tag/' + release_tag)
    prepared = {}
    receipt = {'schema': 1, 'source_revision': source_revision, 'source_catalog_schema': 4,
               'output_catalog_schema': 5, 'release_tag': release_tag, 'profiles': {}}
    for profile in PROFILES:
        directory = source/'Profiles'/profile
        data = {}
        for name in FILES:
            path = directory/name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1048576:
                raise ValueError(f'unsafe or missing profile file: {profile}/{name}')
            data[name] = path.read_bytes()
        if data['trust-root.ed25519.pub'] != public or len(data['catalog.json.sig']) != 64:
            raise ValueError(f'{profile} has a different signing root')
        verify(data['catalog.json'], data['catalog.json.sig'], public)
        catalog = json.loads(data['catalog.json'])
        models = catalog['models']
        if (catalog['schemaVersion'] != 4 or catalog['sequence'] >= now
                or len(models) != len(allowed)
                or {model['deviceIdentifier'] for model in models} != allowed
                or any(model['status'] != 'enabled' or model['installationProfile'] != profile
                       or model.get('repairManifestArtifact') is not None
                       or model.get('repairManifestDigest') is not None for model in models)):
            raise ValueError(f'{profile} is not a complete public schema-4 profile')
        if any(not all(key in model for key in ('engineArtifact', 'engineDigest', 'engineVersion',
                                               'metadataArtifact', 'metadataDigest',
                                               'payloadArtifact', 'payloadDigest')) for model in models):
            raise ValueError(f'{profile} is missing an authenticated delivery field')
        metadata = {(model['metadataArtifact']['sourceURL'], model['metadataDigest'])
                    for model in models}
        if len(metadata) != 1:
            raise ValueError(f'{profile} does not share one metadata artifact')
        metadata_url, metadata_digest = next(iter(metadata))
        raw = fetch(metadata_url)
        if 'sha256:' + hashlib.sha256(raw).hexdigest() != metadata_digest:
            raise ValueError(f'{profile} metadata digest differs from the signed catalog')
        template = json.loads(raw)['os_list'][0]
        policy = template.get('aurora_stage1')
        boards = {model['deviceIdentifier'].removeprefix('apple,') + 'ap' for model in models}
        if (not isinstance(policy, dict) or set(policy) != {'kind', 'qualified_machines'}
                or policy['kind'] != 'common-raw-v1'
                or set(policy['qualified_machines']) != boards
                or any(not versions for versions in policy['qualified_machines'].values())):
            raise ValueError(f'{profile} lacks native Stage 1 policy for a supported board')
        previous = hashlib.sha256(data['catalog.json']).hexdigest()
        catalog['schemaVersion'] = 5
        catalog['sequence'] = now
        catalog['issuedAt'] = issued
        catalog['installer'] = {'minimumVersion': version, 'latestVersion': version,
                                'downloadURL': url}
        for model in models:
            for field in ('engineVersion', 'engineDigest', 'engineArtifact',
                          'repairManifestDigest', 'repairManifestArtifact'):
                model.pop(field, None)
        data['catalog.json'] = (json.dumps(catalog, indent=2) + '\n').encode()
        prepared[profile] = data
        receipt['profiles'][profile] = {
            'previous_catalog_sha256': previous,
            'catalog_sha256': hashlib.sha256(data['catalog.json']).hexdigest(),
            'metadata_sha256': hashlib.sha256(raw).hexdigest(), 'models': len(models)}
    with tempfile.TemporaryDirectory(prefix='aurora-native-profiles-', dir=output.parent) as temporary:
        staged = Path(temporary)/'signed'
        staged.mkdir()
        for profile, data in prepared.items():
            directory = staged/'Profiles'/profile
            directory.mkdir(parents=True)
            for name, content in data.items():
                if name != 'catalog.json.sig':
                    (directory/name).write_bytes(content)
            subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(signing_key), '-rawin',
                            '-in', str(directory/'catalog.json'),
                            '-out', str(directory/'catalog.json.sig')], check=True)
            signature = (directory/'catalog.json.sig').read_bytes()
            verify(data['catalog.json'], signature, public)
            channel = directory/'channels/edge'
            channel.mkdir(parents=True)
            (channel/'catalog.signed.json').write_text(json.dumps({
                'schema_version': 1, 'catalog': base64.b64encode(data['catalog.json']).decode(),
                'signature': base64.b64encode(signature).decode()}, separators=(',', ':')) + '\n')
        for name in ('release.json', 'trust-root.ed25519.pub'):
            (staged/name).write_bytes((source/name).read_bytes())
        for name in ('catalog.json', 'catalog.json.sig'):
            (staged/name).write_bytes((staged/'Profiles/fedora-kde'/name).read_bytes())
        (staged/'channels/edge').mkdir(parents=True)
        (staged/'channels/edge/catalog.signed.json').write_bytes(
            (staged/'Profiles/fedora-kde/channels/edge/catalog.signed.json').read_bytes())
        (staged/'native-only-catalogs.json').write_text(json.dumps(receipt, indent=2) + '\n')
        staged.rename(output)
    print(f'Prepared {len(prepared)} signed native-only profiles; live feeds unchanged')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--signing-key', type=Path, required=True)
    parser.add_argument('--release-tag', required=True)
    parser.add_argument('--source-revision', required=True)
    arguments = parser.parse_args()
    prepare(arguments.source, arguments.output, arguments.signing_key,
            arguments.release_tag, arguments.source_revision)
