#!/usr/bin/env python3
"""Adopt a signed public profile delivery into the installer resource tree."""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.error
import urllib.request

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from channel_policy import (CHANNELS, descriptor as channel_descriptor, validate_catalog,
                            verify_edge_delivery, verify_edge_source_refs)

import neo_edge

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_WORKFLOW = 'aurora-silicon/aurora-distributions/.github/workflows/build-payload.yml'
NEW65_BOARDS = ('j714c', 'j714s', 'j716c', 'j716s', 'j775c', 'j775d', 'j873s')
PROFILE_OWNERS = {
    'aurora-silicon/' + repository: {distribution + '-' + desktop for desktop in ('kde', 'gnome', 'minimal')}
    for distribution, repository in [('fedora', 'fedora-aurora'), ('arch', 'alarm-aurora'), ('nixos', 'nixos-aurora')]
}
PROFILE_OWNERS['aurora-silicon/omarchy-aurora'] = {'omarchy-hyprland'}
VM_CHECKS = ('boot', 'encryption', 'temporary_key_rejected', 'owner', 'sudo', 'second_boot',
             'desktop', 'shell', 'terminal', 'network', 'original_unchanged', 'package_trust')


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


def read_refresh_record(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read((1 << 20) + 1)
    if len(data) > 1 << 20:
        raise ValueError('65-model delivery record exceeds its size limit')
    return data


def verify_models65(repository, tag, data, catalog, receipt, image, trusted):
    """Authenticate unchanged payload pointers and the sole metadata policy delta."""
    lineage = json.loads(data['source-delivery.json'])
    patch = json.loads(data['metadata-refresh.json'])
    profile = receipt['installation_profile']
    source_tag = f"payload-{profile}-{image['id']}"
    if (lineage.get('repository') != repository or lineage.get('tag') != source_tag
            or lineage.get('source_revision') != image['head_sha']
            or lineage.get('provenance') != 'verified'
            or receipt['release_id'] != f"build-{image['id']}-models65"
            or patch.get('kind') != 'metadata-only-models65'
            or patch.get('payload_unchanged') is not True
            or patch.get('source_catalog_sha256') != lineage.get('catalog_sha256')):
        raise ValueError('65-model refresh lineage differs from the selected image')
    base = f'https://github.com/{repository}/releases/download/{source_tag}'
    names = ('catalog.json', 'catalog.json.sig', 'trust-root.ed25519.pub',
             'delivery.json', 'image-build.json', 'release.json',
             f'installer-data-{profile}.json')
    source = {name: read_refresh_record(base + '/' + name) for name in names}
    if (source['trust-root.ed25519.pub'] != trusted
            or hashlib.sha256(source['catalog.json']).hexdigest() != lineage['catalog_sha256']
            or json.loads(source['image-build.json']) != image
            or verify_provenance(repository, base, source) != 'verified'):
        raise ValueError('65-model source delivery identity or provenance differs')
    with tempfile.TemporaryDirectory(prefix='aurora-cohort65-signature-') as temporary:
        directory = Path(temporary)
        (directory / 'public.der').write_bytes(bytes.fromhex('302a300506032b6570032100') + trusted)
        for name in ('catalog.json', 'catalog.json.sig'):
            (directory / name).write_bytes(source[name])
        subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-keyform', 'DER',
                        '-inkey', str(directory / 'public.der'), '-rawin', '-in',
                        str(directory / 'catalog.json'), '-sigfile', str(directory / 'catalog.json.sig')],
                       check=True, capture_output=True)
    original = json.loads(source['catalog.json'])
    original_rows = original['models']
    expected = {row['deviceIdentifier'] for row in catalog['models']} - {
        'apple,' + board for board in NEW65_BOARDS}
    if (original['schemaVersion'] != 5 or len(original_rows) != 58
            or {row['deviceIdentifier'] for row in original_rows} != expected
            or any(row['status'] != 'enabled' or row['installationProfile'] != profile
                   for row in original_rows)):
        raise ValueError('65-model source is not the complete original cohort')
    example = original_rows[0]
    for row in original_rows + catalog['models']:
        if any(row[field] != example[field] for field in ('payloadArtifact', 'payloadDigest')):
            raise ValueError('65-model refresh changes an original payload pointer or digest')
    payload = example['payloadArtifact']
    if (patch.get('payload_name') != payload['fileName']
            or patch.get('payload_sha256') != example['payloadDigest']
            or patch.get('payload_bytes') != payload['sizeBytes']
            or receipt['artifacts'].get(payload['fileName']) != {
                'bytes': payload['sizeBytes'], 'sha256': example['payloadDigest'].removeprefix('sha256:')}):
        raise ValueError('65-model refresh payload lineage differs')
    evidence = patch.get('board_members', {})
    members = {f'esp/m1n1/{prefix}{board}.bin' for board in NEW65_BOARDS
               for prefix in ('boot-', 'boot-ctxr-')}
    if (set(evidence) != members or any(
            not isinstance(record, dict) or set(record) != {'bytes', 'sha256'}
            or type(record['bytes']) is not int or not 0 < record['bytes'] <= 16 << 20
            or not re.fullmatch('[0-9a-f]{64}', record['sha256']) for record in evidence.values())):
        raise ValueError('65-model refresh lacks complete bounded board-member evidence')
    original_by_id = {row['deviceIdentifier']: row for row in original_rows}
    for row in catalog['models']:
        expected_row = copy.deepcopy(original_by_id.get(row['deviceIdentifier'], example))
        expected_row['deviceIdentifier'] = row['deviceIdentifier']
        for field in ('metadataArtifact', 'metadataDigest'):
            expected_row[field] = row[field]
        if row != expected_row:
            raise ValueError('65-model refresh changes model fields beyond metadata admission')
    metadata_name = f'installer-data-{profile}.json'
    source_raw = source[metadata_name]
    if ('sha256:' + hashlib.sha256(source_raw).hexdigest() != example['metadataDigest']
            or patch.get('source_metadata_sha256') != example['metadataDigest']):
        raise ValueError('65-model source metadata digest differs')
    model = catalog['models'][0]
    target_base = f'https://github.com/{repository}/releases/download/{tag}'
    if any(row['metadataArtifact'] != model['metadataArtifact']
           or row['metadataDigest'] != model['metadataDigest'] for row in catalog['models']):
        raise ValueError('65-model refresh does not share one metadata artifact')
    if (model['metadataArtifact']['sourceURL'] != target_base + '/' + metadata_name
            or model['metadataArtifact']['fileName'] != metadata_name
            or model['metadataDigest'] != patch.get('metadata_sha256')):
        raise ValueError('65-model metadata artifact differs')
    raw = read_refresh_record(target_base + '/' + metadata_name)
    if ('sha256:' + hashlib.sha256(raw).hexdigest() != model['metadataDigest']
            or len(raw) != model['metadataArtifact']['sizeBytes']
            or receipt['artifacts'].get(metadata_name) != {
                'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}):
        raise ValueError('65-model metadata identity differs')
    metadata = json.loads(source_raw)
    expected_metadata = copy.deepcopy(metadata)
    template = expected_metadata['os_list'][0]
    for board in NEW65_BOARDS:
        if (template['aurora_stage2']['board_members'].get(board + 'ap') != f'esp/m1n1/boot-{board}.bin'
                or f'esp/m1n1/boot-ctxr-{board}.bin' not in template['aurora_macho_boot']['confined_members']):
            raise ValueError('65-model source metadata lacks own board Stage 2')
        template['aurora_stage1']['qualified_machines'][board + 'ap'] = ['27.2']
    if json.loads(raw) != expected_metadata:
        raise ValueError('65-model refresh changes metadata beyond board admission')


def adopt(repository, tag, site, channel='testing'):
    match = re.fullmatch(r'payload-(.+)-([0-9]+)(-models(?:58|65)|-edge-alpha\.[1-9][0-9]*)?', tag)
    edge = channel == 'edge'
    if channel not in ('testing', 'edge') or edge != bool(match and match[3] and match[3].startswith('-edge-alpha.')):
        raise ValueError('Edge requires a new qualified Edge release, never testing promotion')
    if (match is None or match[1] not in PROFILE_OWNERS.get(repository, set())
            or (repository == 'aurora-silicon/omarchy-aurora' and match[3] and not edge)):
        raise ValueError('expected an immutable Aurora profile delivery')
    base = 'https://github.com/' + repository + '/releases/download/' + tag
    names = ('release.json', 'trust-root.ed25519.pub', 'catalog.json', 'catalog.json.sig',
             'delivery.json', 'image-build.json')
    if repository == 'aurora-silicon/omarchy-aurora':
        names += ('vm-result.json',)
    if tag.endswith('-models58'):
        names += ('source-delivery.json', 'zip-refresh.json')
    if tag.endswith('-models65'):
        names += ('source-delivery.json', 'metadata-refresh.json')
    if edge:
        if (repository, match[1]) != (neo_edge.REPO, neo_edge.PROFILE):
            raise ValueError('Only the F44 KDE Neo Edge producer is supported')
        names += ('edge-qualification.json', 'edge-image-proof.json', 'installer-data-fedora-kde.json')
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
    if (match[3] or repository == 'aurora-silicon/omarchy-aurora') and provenance != 'verified':
        raise ValueError('this delivery requires hosted provenance')
    receipt = json.loads(data['delivery.json']); profile = receipt['installation_profile']
    if (profile != match[1] or profile not in PROFILE_OWNERS[repository]
            or receipt['private_neo_inputs'] or receipt.get('native_neo_reset_qa', False)):
        raise ValueError('delivery is not a public profile for this repository')
    if repository == 'aurora-silicon/omarchy-aurora':
        vm = json.loads(data['vm-result.json'])
        if not all(vm.get(check) is True for check in VM_CHECKS):
            raise ValueError('Omarchy delivery has no complete passing VM qualification')
    run = json.loads(data['image-build.json'])
    if (tag != 'payload-' + profile + '-' + str(run['id']) + (match[3] or '')
            or (not edge and (run['conclusion'] != 'success' or run['head_branch'] != 'main'
                              or run['head_repository']['full_name'] != repository
                              or run['path'] != '.github/workflows/images.yml'))):
        raise ValueError('image provenance differs from the delivery tag')
    if tag.endswith('-models58'):
        lineage, patch = json.loads(data['source-delivery.json']), json.loads(data['zip-refresh.json'])
        if (lineage['repository'] != repository or lineage['source_revision'] != run['head_sha']
                or lineage['tag'] != 'payload-' + profile + '-' + str(run['id'])
                or not receipt['release_id'].endswith('-models58')
                or patch['existing_compressed_members_changed'] is not False
                or receipt['artifacts'][patch['payload_name']] != {
                    'bytes': patch['bytes'], 'sha256': patch['sha256']}):
            raise ValueError('58-model refresh lineage differs from the selected image')
    catalog = json.loads(data['catalog.json'])
    models = catalog['models']
    if tag.endswith('-models65'):
        verify_models65(repository, tag, data, catalog, receipt, run, trusted)
    allowed = set(neo_edge.models()) if edge else set(json.loads((ROOT/'keys/supported-models.json').read_text())['supported'])
    # Deliveries are schema 5 and name no engine. Schema 4 deliveries built
    # before that still adopt; the native-only step strips their engine pins.
    if (catalog['schemaVersion'] not in (4, 5) or {model['deviceIdentifier'] for model in models} != allowed
            or len(models) != len(allowed) or any(model['status'] != 'enabled'
               or model['installationProfile'] != profile for model in models)):
        raise ValueError('catalog model coverage or profile binding differs')
    if catalog['schemaVersion'] == 5 and any(
            field in model for model in models
            for field in ('engineVersion', 'engineDigest', 'engineArtifact',
                          'repairManifestDigest', 'repairManifestArtifact')):
        raise ValueError('a native-only catalog names an engine or repair artifact')
    descriptor = json.loads(data['release.json'])
    if descriptor['schema_version'] != 3 or descriptor['trust_root_fingerprint'] != 'sha256:' + hashlib.sha256(trusted).hexdigest():
        raise ValueError('profile descriptor differs from the pinned root')
    public_base = 'https://aurorasilicon.org/aurora-distributions/installer/Profiles/' + profile
    descriptor = json.loads(channel_descriptor(data['release.json']))
    for lane in CHANNELS:
        if descriptor['channels'][lane]['catalog_url'] != public_base + '/channels/' + ('edge-alpha' if lane == 'edge' else lane) + '/catalog.signed.json':
            raise ValueError('profile channel URL differs')
    validate_catalog(catalog, channel, legacy_development=(provenance == 'verified'))
    profile_root = site/'installer/Profiles'/profile
    if edge:
        testing_path = profile_root/'catalog.json'
        testing = json.loads(testing_path.read_text()) if testing_path.exists() else None
        if provenance != 'verified':
            raise ValueError('Edge requires hosted payload and qualification provenance')
        verify_edge_delivery(catalog, data['edge-qualification.json'], receipt, run, base, testing)
        neo_edge.verify_binding(catalog, receipt, run, data['edge-image-proof.json'])
        neo_edge.verify_metadata(catalog, data['installer-data-fedora-kde.json'])
        verify_edge_source_refs(catalog['edgeQualification'])
        if receipt['release_id'] != 'build-' + str(run['id']) + match[3]:
            raise ValueError('Edge Alpha receipt identity differs')
    target = profile_root/'channels/edge-alpha' if edge else profile_root
    target.mkdir(parents=True, exist_ok=True)
    if (target/'catalog.json').exists():
        previous = json.loads((target/'catalog.json').read_text())
        if catalog['sequence'] <= previous['sequence'] and data['catalog.json'] != (target/'catalog.json').read_bytes():
            raise ValueError('catalog sequence does not advance the current profile')
    for name in names:
        (target/name).write_bytes(channel_descriptor(data[name]) if name == 'release.json' else data[name])
    channel_directory = target if edge else target/'channels/testing'
    channel_directory.mkdir(parents=True, exist_ok=True)
    (channel_directory/'catalog.signed.json').write_text(json.dumps({'schema_version':1,
        'catalog':base64.b64encode(data['catalog.json']).decode(),
        'signature':base64.b64encode(data['catalog.json.sig']).decode()},separators=(',',':'))+'\n')
    if not edge:
        legacy = profile_root/'channels/edge'
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy/'catalog.signed.json').write_bytes((channel_directory/'catalog.signed.json').read_bytes())
    if profile == 'fedora-kde':
        root = site/'installer'
        if not edge:
            for name in ('catalog.json', 'catalog.json.sig', 'trust-root.ed25519.pub'):
                (root/name).write_bytes(data[name])
        root_channel = root/('channels/edge-alpha' if edge else 'channels/testing')
        root_channel.mkdir(parents=True, exist_ok=True)
        (root_channel/'catalog.signed.json').write_bytes((channel_directory/'catalog.signed.json').read_bytes())
        if edge:
            (root_channel/'edge-qualification.json').write_bytes(data['edge-qualification.json'])
    if edge:
        print('Adopted qualified Edge profile:', profile)
        return
    legacy = site/'installer/channels/edge'
    if profile == 'fedora-kde':
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy/'catalog.signed.json').write_bytes((channel_directory/'catalog.signed.json').read_bytes())
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
    parser.add_argument('--channel', choices=['testing', 'edge'], default='testing')
    args=parser.parse_args();adopt(args.repository,args.tag,args.site,args.channel)
