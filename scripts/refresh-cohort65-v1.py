#!/usr/bin/env python3
"""Authenticate a 58-model delivery and add seven DT-backed Mac admissions.

The original ZIP is downloaded once, hashed and inspected in place. No ZIP
member is changed or copied: installer-data is a separate signed-catalog
artifact, written after ZIP assembly by assemble-distribution-payload.py.
"""
import argparse
import copy
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import time
import urllib.request
import zipfile

NEW_BOARDS = ('j714c', 'j714s', 'j716c', 'j716s', 'j775c', 'j775d', 'j873s')
OWNERS = {'fedora': 'fedora-aurora', 'arch': 'alarm-aurora', 'nixos': 'nixos-aurora'}
WORKFLOW = 'aurora-silicon/aurora-distributions/.github/workflows/build-payload.yml'
SMALL_LIMIT = 1 << 20
STAGE2_LIMIT = 16 << 20
FREE_FLOOR = 8 << 30
PUBLIC_DER_PREFIX = bytes.fromhex('302a300506032b6570032100')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(SMALL_LIMIT + 1)
    if len(data) > SMALL_LIMIT:
        raise ValueError('delivery record exceeds its size limit')
    return data


def verify_signature(catalog, signature, public):
    if len(public) != 32 or len(signature) != 64:
        raise ValueError('invalid catalog signature or trust root')
    with tempfile.TemporaryDirectory(prefix='cohort65-signature-') as temporary:
        directory = Path(temporary)
        (directory / 'public.der').write_bytes(PUBLIC_DER_PREFIX + public)
        (directory / 'catalog.json').write_bytes(catalog)
        (directory / 'catalog.sig').write_bytes(signature)
        subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-keyform', 'DER',
                        '-inkey', str(directory / 'public.der'), '-rawin', '-in',
                        str(directory / 'catalog.json'), '-sigfile', str(directory / 'catalog.sig')],
                       check=True, capture_output=True)


def authenticate(repository, profile, run, public, directory):
    if (not re.fullmatch(r'(fedora|arch|nixos)-(kde|gnome|minimal)', profile)
            or repository != 'aurora-silicon/' + OWNERS[profile.split('-')[0]]):
        raise ValueError('repository/profile mismatch')
    tag = f'payload-{profile}-{run}'
    base = f'https://github.com/{repository}/releases/download/{tag}/'
    names = ('catalog.json', 'catalog.json.sig', 'trust-root.ed25519.pub',
             'delivery.json', 'image-build.json', 'release.json',
             f'installer-data-{profile}.json')
    data = {name: fetch(base + name) for name in names}
    if data['trust-root.ed25519.pub'] != public:
        raise ValueError('base catalog root differs from the pinned root')
    verify_signature(data['catalog.json'], data['catalog.json.sig'], public)
    # The hosted build attestation must cover every retained source record.
    sums = {}
    for line in fetch(base + 'SHA256SUMS').decode().splitlines():
        digest, name = line.split(maxsplit=1)
        name = name.lstrip('*')
        if not re.fullmatch('[0-9a-f]{64}', digest) or name in sums:
            raise ValueError('invalid source checksum inventory')
        sums[name] = digest
    if any(sums.get(name) != sha(value) for name, value in data.items()):
        raise ValueError('source records differ from the attested inventory')
    path = directory / 'catalog.json'
    path.write_bytes(data['catalog.json'])
    result = subprocess.check_output(['gh', 'attestation', 'verify', str(path), '--repo', repository,
        '--signer-workflow', WORKFLOW, '--deny-self-hosted-runners', '--format', 'json'], text=True)
    subjects = {(subject['name'], subject['digest']['sha256']) for verification in json.loads(result)
                for subject in verification['verificationResult']['statement']['subject']}
    if not set(sums.items()) <= subjects:
        raise ValueError('hosted provenance does not cover the source delivery')
    image = json.loads(data['image-build.json'])
    if (image['id'] != run or image['conclusion'] != 'success' or image['head_branch'] != 'main'
            or image['head_repository']['full_name'] != repository
            or image['path'] != '.github/workflows/images.yml'):
        raise ValueError('base image provenance differs')
    catalog = json.loads(data['catalog.json'])
    metadata = json.loads(data[f'installer-data-{profile}.json'])
    return data, catalog, metadata, image, {
        'repository': repository, 'tag': tag, 'source_revision': image['head_sha'],
        'catalog_sha256': sha(data['catalog.json']), 'provenance': 'verified'}


def download_payload(model, repository, tag, destination):
    artifact = model['payloadArtifact']
    base = f'https://github.com/{repository}/releases/download/{tag}/'
    name = artifact['fileName']
    if (not re.fullmatch(r'[a-z0-9.-]+\.zip', name)
            or artifact['sourceURL'] != base + name
            or not isinstance(artifact['sizeBytes'], int) or artifact['sizeBytes'] <= 0):
        raise ValueError('unsafe source payload artifact')
    size = artifact['sizeBytes']
    if shutil.disk_usage(destination.parent).free - size - SMALL_LIMIT < FREE_FLOOR:
        raise ValueError('payload download would cross the free-space floor')
    parts = artifact.get('parts')
    if parts is None:
        parts = [dict(artifact, sha256=model['payloadDigest'])]
    elif not 2 <= len(parts) <= 16:
        raise ValueError('invalid source payload parts')
    whole = hashlib.sha256()
    total = 0
    with destination.open('xb') as target:
        for index, part in enumerate(parts):
            expected = name + f'.part{index:02d}' if artifact.get('parts') is not None else name
            if (part['fileName'] != expected or part['sourceURL'] != base + expected
                    or not isinstance(part['sizeBytes'], int) or part['sizeBytes'] <= 0):
                raise ValueError('unsafe or non-contiguous source payload part')
            count, digest = 0, hashlib.sha256()
            with urllib.request.urlopen(part['sourceURL'], timeout=60) as response:
                while block := response.read(SMALL_LIMIT):
                    count += len(block)
                    total += len(block)
                    if count > part['sizeBytes'] or total > size:
                        raise ValueError('source payload exceeds its signed size')
                    if shutil.disk_usage(destination.parent).free - len(block) < FREE_FLOOR:
                        raise ValueError('payload download reached the free-space floor')
                    target.write(block)
                    digest.update(block)
                    whole.update(block)
            if count != part['sizeBytes'] or 'sha256:' + digest.hexdigest() != part['sha256']:
                raise ValueError('source payload part identity differs')
    if total != size or 'sha256:' + whole.hexdigest() != model['payloadDigest']:
        raise ValueError('source payload identity differs')


def root_compatible(blob):
    """Read only a bounded FDT root compatible property, never arbitrary code."""
    if len(blob) < 40:
        raise ValueError('short device tree')
    magic, total, structure, strings = struct.unpack_from('>4I', blob)
    strings_size, structure_size = struct.unpack_from('>2I', blob, 32)
    if (magic != 0xd00dfeed or total > len(blob) or total < 40
            or structure < 40 or structure + structure_size > total
            or strings < 40 or strings + strings_size > total):
        raise ValueError('invalid device tree header')
    end = structure + structure_size
    if blob[structure:structure + 8] != b'\0\0\0\1\0\0\0\0':
        raise ValueError('invalid device tree root')
    cursor = structure + 8
    while cursor + 12 <= end:
        token, size, name = struct.unpack_from('>3I', blob, cursor)
        if token == 4:
            cursor += 4
            continue
        if token != 3 or cursor + 12 + size > end or name >= strings_size:
            break
        cursor += 12
        name_end = blob.find(b'\0', strings + name, strings + strings_size)
        if name_end < 0:
            raise ValueError('unterminated device tree property')
        key = blob[strings + name:name_end]
        value = blob[cursor:cursor + size]
        cursor = (cursor + size + 3) & ~3
        if key == b'compatible' and value.endswith(b'\0'):
            return value.rstrip(b'\0').decode('ascii').split('\0')
    raise ValueError('device tree has no root compatible')


def validate_board(data, board):
    if not data or len(data) > STAGE2_LIMIT:
        raise ValueError('board Stage 2 exceeds its size limit')
    start = data.find(b'\xd0\x0d\xfe\xed')
    matched = False
    while start >= 0:
        try:
            compatible = root_compatible(data[start:])
        except (ValueError, UnicodeError, struct.error):
            compatible = []
        if compatible and re.fullmatch(r'apple,[jk][0-9]+[a-z]*', compatible[0]):
            if compatible[0] != 'apple,' + board or 'apple,t6050' not in compatible:
                raise ValueError('board Stage 2 has no matching T6050-only device tree: ' + board)
            matched = True
        start = data.find(b'\xd0\x0d\xfe\xed', start + 1)
    if not matched:
        raise ValueError('board Stage 2 has no matching T6050 device tree: ' + board)


def refresh(payload, catalog, metadata, delivery, image, lineage, identifiers, output, base_url):
    if output.exists() or len(identifiers) != 65 or len(set(identifiers)) != 65:
        raise ValueError('expected all 65 models and a new output directory')
    before = set(identifiers) - {'apple,' + board for board in NEW_BOARDS}
    models = catalog['models']
    if (catalog['schemaVersion'] != 5 or len(models) != 58
            or {m['deviceIdentifier'] for m in models} != before
            or any(m['status'] != 'enabled' for m in models)):
        raise ValueError('expected the complete signed 58-model catalog')
    profile = delivery['installation_profile']
    if (lineage.get('provenance') != 'verified' or lineage['source_revision'] != image['head_sha']
            or image['conclusion'] != 'success' or image['head_branch'] != 'main'
            or image['path'] != '.github/workflows/images.yml'
            or image['head_repository']['full_name'] != lineage['repository']
            or lineage['tag'] != f"payload-{profile}-{image['id']}"):
        raise ValueError('source image or hosted provenance lineage differs')
    if (delivery['private_neo_inputs'] or delivery.get('native_neo_reset_qa', False)
            or any(m['installationProfile'] != profile for m in models)):
        raise ValueError('private or incorrectly bound source delivery')
    model = models[0]
    fields = ('metadataArtifact', 'metadataDigest', 'payloadArtifact', 'payloadDigest')
    if any(any(m[field] != model[field] for field in fields) for m in models):
        raise ValueError('source models do not share one authenticated delivery')
    if 'sha256:' + file_sha(payload) != model['payloadDigest'] or payload.stat().st_size != model['payloadArtifact']['sizeBytes']:
        raise ValueError('source ZIP identity differs')
    expected_payload = {'bytes': payload.stat().st_size, 'sha256': file_sha(payload)}
    if delivery['artifacts'].get(model['payloadArtifact']['fileName']) != expected_payload:
        raise ValueError('source payload receipt differs')
    metadata = copy.deepcopy(metadata)
    if len(metadata['os_list']) != 1:
        raise ValueError('expected one shared template')
    template = metadata['os_list'][0]
    old_classes = {identifier.removeprefix('apple,') + 'ap' for identifier in before}
    if (template['installation_profile'] != profile
            or set(template['aurora_stage1']['qualified_machines']) != old_classes
            or template['aurora_stage1']['kind'] != 'common-raw-v1'
            or template.get('allowed_device_classes') is not None
            or template.get('excluded_device_classes')):
        raise ValueError('source template admission differs')
    metadata_name = model['metadataArtifact']['fileName']
    # Authenticate exact original metadata bytes in main(), before JSON decoding.
    if (template['aurora_stage2'].get('kind') != 'board-file-v1'
            or template['aurora_macho_boot'].get('kind') != 'padded-ctxr-v1'):
        raise ValueError('source metadata lacks the board Stage 2 contract')
    members = template['aurora_stage2']['board_members']
    confined = template['aurora_macho_boot']['confined_members']
    evidence = {}
    with zipfile.ZipFile(payload) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('duplicate source ZIP member')
        if metadata_name in names:
            raise ValueError('source ZIP embeds installer metadata; metadata-only refresh refused')
        for board in NEW_BOARDS:
            normal = 'esp/m1n1/boot-' + board + '.bin'
            ctxr = 'esp/m1n1/boot-ctxr-' + board + '.bin'
            if members.get(board + 'ap') != normal or ctxr not in confined:
                raise ValueError('source metadata lacks own board Stage 2: ' + board)
            for name in (normal, ctxr):
                if (name not in names or archive.getinfo(name).file_size > STAGE2_LIMIT
                        or (archive.getinfo(name).external_attr >> 16) & 0o170000 not in (0, 0o100000)):
                    raise ValueError('source ZIP lacks bounded own board Stage 2: ' + board)
                data = archive.read(name)
                validate_board(data, board)
                evidence[name] = {'bytes': len(data), 'sha256': sha(data)}
    for board in NEW_BOARDS:
        # T6050 customer Restore identities are present in pinned 26B123 (27.2).
        # This is admission policy, not evidence of a physical installer run.
        template['aurora_stage1']['qualified_machines'][board + 'ap'] = ['27.2']
    raw = (json.dumps(metadata, indent=2) + '\n').encode()
    refreshed = copy.deepcopy(catalog)
    refreshed['sequence'] = max(int(time.time()), catalog['sequence'] + 1)
    refreshed['issuedAt'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    example = copy.deepcopy(model)
    existing = {row['deviceIdentifier']: row for row in models}
    refreshed['models'] = []
    for identifier in identifiers:
        row = copy.deepcopy(existing.get(identifier, example))
        row['deviceIdentifier'] = identifier
        row['metadataArtifact'] = {'sourceURL': base_url + '/' + metadata_name,
                                   'fileName': metadata_name, 'sizeBytes': len(raw)}
        row['metadataDigest'] = 'sha256:' + sha(raw)
        refreshed['models'].append(row)
    receipt = copy.deepcopy(delivery)
    receipt['release_id'] = f"build-{image['id']}-models65"
    receipt['physical_qualification'] = 'pending'
    receipt['artifacts'][metadata_name] = {'bytes': len(raw), 'sha256': sha(raw)}
    patch = {'schema': 1, 'kind': 'metadata-only-models65', 'payload_unchanged': True,
             'payload_name': model['payloadArtifact']['fileName'], 'payload_sha256': model['payloadDigest'],
             'payload_bytes': payload.stat().st_size,
             'source_metadata_sha256': model['metadataDigest'], 'metadata_sha256': 'sha256:' + sha(raw),
             'source_catalog_sha256': lineage['catalog_sha256'], 'board_members': evidence}
    output.mkdir(parents=True)
    for name, value in [(metadata_name, raw), ('catalog.json', (json.dumps(refreshed, indent=2) + '\n').encode()),
                        ('delivery.json', (json.dumps(receipt, indent=2) + '\n').encode()),
                        ('metadata-refresh.json', (json.dumps(patch, indent=2) + '\n').encode())]:
        (output / name).write_bytes(value)
    return patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--image-run', required=True, type=int)
    parser.add_argument('--producer', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.producer / 'scripts/supported-models.json').read_text())
    public = (args.producer / 'Release/trust-root.ed25519.pub').read_bytes()
    with tempfile.TemporaryDirectory(prefix='cohort65-base-') as temporary:
        directory = Path(temporary)
        data, catalog, metadata, image, lineage = authenticate(
            args.repository, args.profile, args.image_run, public, directory)
        model = catalog['models'][0]
        metadata_name = f'installer-data-{args.profile}.json'
        if (model['metadataArtifact']['sourceURL'] !=
                f'https://github.com/{args.repository}/releases/download/{lineage["tag"]}/{metadata_name}'
                or model['metadataArtifact']['fileName'] != metadata_name
                or 'sha256:' + sha(data[metadata_name]) != model['metadataDigest']
                or len(data[metadata_name]) != model['metadataArtifact']['sizeBytes']):
            raise ValueError('signed source metadata identity differs')
        payload = directory / 'payload.zip'
        download_payload(model, args.repository, lineage['tag'], payload)
        base_url = (f'https://github.com/{args.repository}/releases/download/'
                    f'payload-{args.profile}-{args.image_run}-models65')
        patch = refresh(payload, catalog, metadata, json.loads(data['delivery.json']), image,
                        lineage, manifest['supported'], args.output, base_url)
        for name in ('release.json', 'trust-root.ed25519.pub', 'image-build.json'):
            (args.output / name).write_bytes(data[name])
        (args.output / 'source-delivery.json').write_text(json.dumps(lineage, indent=2) + '\n')
        print(json.dumps(patch))


if __name__ == '__main__':
    main()
