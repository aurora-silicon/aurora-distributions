#!/usr/bin/env python3
"""Adopt a signed public profile delivery into the installer resource tree."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_WORKFLOW = 'aurora-silicon/aurora-distributions/.github/workflows/build-payload.yml'


def verify_provenance(repository, base, data):
    """Deliveries published since build-payload.yml attests them carry a
    SHA256SUMS that a GitHub build provenance attestation covers. Verify it
    and that the downloaded files are the attested ones; older deliveries
    predate attestations."""
    try:
        sums = urllib.request.urlopen(base + '/SHA256SUMS', timeout=60).read().decode()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return 'none (delivery predates attestations)'
        raise
    listed = {}
    for line in sums.splitlines():
        digest, name = line.split(maxsplit=1)
        listed[name.lstrip('*')] = digest
    for name, content in data.items():
        if listed.get(name) != hashlib.sha256(content).hexdigest():
            raise ValueError(f'{name} differs from the attested SHA256SUMS')
    with tempfile.TemporaryDirectory(prefix='aurora-provenance-') as temporary:
        catalog = Path(temporary) / 'catalog.json'
        catalog.write_bytes(data['catalog.json'])
        result = subprocess.run(['gh', 'attestation', 'verify', str(catalog), '--repo', repository,
                                 '--signer-workflow', PAYLOAD_WORKFLOW, '--deny-self-hosted-runners',
                                 '--format', 'json'], check=True, capture_output=True, text=True)
    subjects = {(s['name'], s['digest']['sha256']) for v in json.loads(result.stdout)
                for s in v['verificationResult']['statement']['subject']}
    if not set(listed.items()) <= subjects:
        raise ValueError('the provenance attestation does not cover every delivery asset')
    return 'verified'


def adopt(repository, tag, site):
    prefixes = {'aurora-silicon/fedora-aurora': 'fedora', 'aurora-silicon/alarm-aurora': 'arch',
                'aurora-silicon/nixos-aurora': 'nixos'}
    if repository not in prefixes or not re.fullmatch(r'payload-(fedora|arch|nixos)-(kde|gnome|minimal)-[0-9]+', tag):
        raise ValueError('expected an immutable Aurora profile delivery')
    base = 'https://github.com/' + repository + '/releases/download/' + tag
    names = ('release.json', 'trust-root.ed25519.pub', 'catalog.json', 'catalog.json.sig',
             'delivery.json', 'image-build.json')
    data = {name: urllib.request.urlopen(base + '/' + name, timeout=60).read() for name in names}
    trusted = (ROOT / 'keys/catalog.ed25519.pub').read_bytes()
    if len(trusted) != 32 or data['trust-root.ed25519.pub'] != trusted or len(data['catalog.json.sig']) != 64:
        raise ValueError('delivery trust root differs from the installer root')
    with tempfile.TemporaryDirectory(prefix='aurora-catalog-verify-') as temporary:
        directory = Path(temporary)
        (directory/'public.der').write_bytes(bytes.fromhex('302a300506032b6570032100') + trusted)
        for name in ('catalog.json', 'catalog.json.sig'): (directory/name).write_bytes(data[name])
        subprocess.run(['openssl','pkeyutl','-verify','-pubin','-keyform','DER','-inkey',str(directory/'public.der'),
                        '-rawin','-in',str(directory/'catalog.json'),'-sigfile',str(directory/'catalog.json.sig')],check=True)
    provenance = verify_provenance(repository, base, data)
    receipt = json.loads(data['delivery.json']); profile = receipt['installation_profile']
    if not re.fullmatch(prefixes[repository] + r'-(kde|gnome|minimal)', profile) or receipt['private_neo_inputs']:
        raise ValueError('delivery is not a public profile for this repository')
    run = json.loads(data['image-build.json'])
    if (run['conclusion'] != 'success' or run['head_branch'] != 'main'
            or run['head_repository']['full_name'] != repository or run['path'] != '.github/workflows/images.yml'
            or tag != 'payload-' + profile + '-' + str(run['id'])):
        raise ValueError('image provenance differs from the delivery tag')
    catalog = json.loads(data['catalog.json'])
    models = catalog['models']
    allowed = set(json.loads((ROOT/'keys/supported-models.json').read_text())['supported'])
    if (catalog['schemaVersion'] != 4 or {model['deviceIdentifier'] for model in models} != allowed
            or len(models) != len(allowed) or any(model['status'] != 'enabled'
               or model['installationProfile'] != profile for model in models)):
        raise ValueError('catalog model coverage or profile binding differs')
    descriptor = json.loads(data['release.json'])
    if descriptor['schema_version'] != 3 or descriptor['trust_root_fingerprint'] != 'sha256:' + hashlib.sha256(trusted).hexdigest():
        raise ValueError('profile descriptor differs from the pinned root')
    public_base = 'https://aurorasilicon.org/aurora-distributions/installer/Profiles/' + profile
    for channel in ('stable','rc','edge'):
        if descriptor['channels'][channel]['catalog_url'] != public_base + '/channels/' + channel + '/catalog.signed.json':
            raise ValueError('profile channel URL differs')
    target = site/'installer/Profiles'/profile
    target.mkdir(parents=True, exist_ok=True)
    if (target/'catalog.json').exists():
        previous = json.loads((target/'catalog.json').read_text())
        if catalog['sequence'] <= previous['sequence'] and data['catalog.json'] != (target/'catalog.json').read_bytes():
            raise ValueError('catalog sequence does not advance the current profile')
    for name in names: (target/name).write_bytes(data[name])
    channel = target/'channels/edge'; channel.mkdir(parents=True, exist_ok=True)
    (channel/'catalog.signed.json').write_text(json.dumps({'schema_version':1,
        'catalog':base64.b64encode(data['catalog.json']).decode(),
        'signature':base64.b64encode(data['catalog.json.sig']).decode()},separators=(',',':'))+'\n')
    if profile == 'fedora-kde':
        root = site/'installer'
        for name in ('catalog.json', 'catalog.json.sig', 'trust-root.ed25519.pub'):
            (root/name).write_bytes(data[name])
        root_channel = root/'channels/edge'
        root_channel.mkdir(parents=True, exist_ok=True)
        (root_channel/'catalog.signed.json').write_bytes((channel/'catalog.signed.json').read_bytes())
    status_path = site/'installer/profile-builds.json'; status = json.loads(status_path.read_text())
    for record in status['profiles']:
        if record['profile'] == profile:
            record.update(assembly='passed', delivery='signed',
                          run=f'https://github.com/{repository}/actions/runs/{run["id"]}',
                          source=run['head_sha'], release=base, physical_installation='pending')
    status_path.write_text(json.dumps(status,indent=2)+'\n')
    print('Adopted signed public profile:', profile, '(attestation:', provenance + ')')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',required=True)
    parser.add_argument('--tag',required=True)
    parser.add_argument('--site',type=Path,default=ROOT/'site')
    args=parser.parse_args();adopt(args.repository,args.tag,args.site)
