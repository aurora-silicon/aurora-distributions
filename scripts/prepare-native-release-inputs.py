#!/usr/bin/env python3
"""Prepare signed app-download metadata without changing the live feeds."""
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

ROOT = Path(__file__).resolve().parents[1]
APP_VERSION = '2.1.2'
PROFILES = tuple(f'{distribution}-{desktop}' for distribution in ('fedora', 'arch', 'nixos')
                 for desktop in ('kde', 'gnome', 'minimal'))


def verify(data, signature, public):
    with tempfile.TemporaryDirectory(prefix='aurora-catalog-check-') as temporary:
        directory = Path(temporary)
        (directory/'key.der').write_bytes(bytes.fromhex('302a300506032b6570032100') + public)
        (directory/'catalog').write_bytes(data)
        (directory/'signature').write_bytes(signature)
        subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-keyform', 'DER',
                        '-inkey', str(directory/'key.der'), '-rawin', '-in', str(directory/'catalog'),
                        '-sigfile', str(directory/'signature')], check=True, stdout=subprocess.DEVNULL)


def prepare(source, output, signing_key, tag, source_revision):
    if not re.fullmatch(r'native-dev-[0-9]{8}(?:\.[1-9][0-9]*)?', tag):
        raise ValueError('expected an immutable native development release tag')
    if not re.fullmatch(r'[a-f0-9]{40}', source_revision) or output.exists():
        raise ValueError('expected a source revision and a new output directory')
    public = (ROOT/'keys/catalog.ed25519.pub').read_bytes()
    if len(public) != 32:
        raise ValueError('invalid pinned catalog root')
    actual = subprocess.check_output(['openssl', 'pkey', '-in', str(signing_key),
                                      '-pubout', '-outform', 'DER'])
    if actual != bytes.fromhex('302a300506032b6570032100') + public:
        raise ValueError('signing key does not match the pinned catalog root')
    allowed = set(json.loads((ROOT/'keys/supported-models.json').read_text())['supported'])
    url = ('https://github.com/aurora-silicon/aurora-installer/releases/download/'
           + tag + f'/Aurora-Installer-{APP_VERSION}-Developer.zip')
    now = int(time.time())
    issued = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    prepared = {}
    receipt = {'schema': 1, 'source_revision': source_revision, 'release_tag': tag,
               'installer_url': url, 'installer_version': APP_VERSION, 'profiles': {}}
    # Authenticate the complete set before emitting anything.
    for profile in PROFILES:
        directory = source/'Profiles'/profile
        data = {}
        for name in ('release.json', 'trust-root.ed25519.pub', 'catalog.json', 'catalog.json.sig',
                     'delivery.json', 'image-build.json'):
            path = directory/name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1048576:
                raise ValueError(f'unsafe or missing delivery file: {profile}/{name}')
            data[name] = path.read_bytes()
        if data['trust-root.ed25519.pub'] != public or len(data['catalog.json.sig']) != 64:
            raise ValueError('profile root differs from the pinned root')
        verify(data['catalog.json'], data['catalog.json.sig'], public)
        catalog = json.loads(data['catalog.json'])
        models = catalog['models']
        if (catalog['schemaVersion'] != 4 or len(models) != len(allowed)
                or {model['deviceIdentifier'] for model in models} != allowed
                or any(model['status'] != 'enabled' or model['installationProfile'] != profile
                       for model in models)):
            raise ValueError('expected the complete public model set for this profile')
        def version(value):
            if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value):
                raise ValueError('expected a numeric installer version')
            return tuple(map(int, value.split('.')))
        if not ((2, 1, 0) <= version(catalog['installer']['minimumVersion'])
                <= version(catalog['installer']['latestVersion']) <= version(APP_VERSION)):
            raise ValueError('catalog installer version would be downgraded or is unsupported')
        if catalog['sequence'] >= now:
            raise ValueError('wait for the clock to advance beyond the current catalog sequence')
        previous = hashlib.sha256(data['catalog.json']).hexdigest()
        catalog['installer']['downloadURL'] = url
        catalog['installer']['minimumVersion'] = APP_VERSION
        catalog['installer']['latestVersion'] = APP_VERSION
        catalog['sequence'] = now
        catalog['issuedAt'] = issued
        data['catalog.json'] = (json.dumps(catalog, indent=2) + '\n').encode()
        prepared[profile] = data
        receipt['profiles'][profile] = {'previous_catalog_sha256': previous,
            'catalog_sha256': hashlib.sha256(data['catalog.json']).hexdigest(), 'sequence': now}
    output.mkdir(parents=True)
    for profile, data in prepared.items():
        directory = output/'Profiles'/profile
        directory.mkdir(parents=True)
        for name, content in data.items():
            if name != 'catalog.json.sig':
                (directory/name).write_bytes(content)
        subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(signing_key), '-rawin',
                        '-in', str(directory/'catalog.json'), '-out', str(directory/'catalog.json.sig')], check=True)
        signature = (directory/'catalog.json.sig').read_bytes()
        verify(data['catalog.json'], signature, public)
        channel = directory/'channels/edge'
        channel.mkdir(parents=True)
        (channel/'catalog.signed.json').write_text(json.dumps({'schema_version': 1,
            'catalog': base64.b64encode(data['catalog.json']).decode(),
            'signature': base64.b64encode(signature).decode()}, separators=(',', ':')) + '\n')
    for name in ('release.json', 'trust-root.ed25519.pub'):
        (output/name).write_bytes((source/name).read_bytes())
    for name in ('catalog.json', 'catalog.json.sig'):
        (output/name).write_bytes((output/'Profiles/fedora-kde'/name).read_bytes())
    (output/'channels/edge').mkdir(parents=True)
    (output/'channels/edge/catalog.signed.json').write_bytes(
        (output/'Profiles/fedora-kde/channels/edge/catalog.signed.json').read_bytes())
    (output/'app-download-amendment.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(f'Prepared {len(prepared)} signed development profiles; live feeds unchanged')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--signing-key', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--source-revision', required=True)
    arguments = parser.parse_args()
    prepare(arguments.source, arguments.output, arguments.signing_key,
            arguments.tag, arguments.source_revision)
