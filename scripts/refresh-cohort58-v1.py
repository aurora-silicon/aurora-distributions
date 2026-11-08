#!/usr/bin/env python3
"""Refresh an authenticated 54-model ZIP without unpacking its OS images."""
import argparse
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

OLED = {'apple,k114c', 'apple,k114s', 'apple,k116c', 'apple,k116s'}
OLD_PRODUCER = '2aa2e04b4072ea0cdecc981792c872e297bd9d1d'
WORKFLOW = 'aurora-silicon/aurora-distributions/.github/workflows/build-payload.yml'
REPOSITORIES = {'fedora': 'fedora-aurora', 'arch': 'alarm-aurora', 'nixos': 'nixos-aurora'}
APPROVED_IMAGES = {
    'fedora-minimal': (42508497105, '7b8be82369684a271b08c5a1139fa54071a47e54'),
    'fedora-kde': (42508499173, '7b8be82369684a271b08c5a1139fa54071a47e54'),
    'fedora-gnome': (42508501457, '7b8be82369684a271b08c5a1139fa54071a47e54'),
    'nixos-minimal': (42517738059, '704543d1e9c469371b60398f03542eb6a24c66d3'),
    'nixos-kde': (42517740678, '704543d1e9c469371b60398f03542eb6a24c66d3'),
    'nixos-gnome': (42517743968, '704543d1e9c469371b60398f03542eb6a24c66d3'),
    'arch-minimal': (42520720916, '73cd02cabd8f68fa9d6408a596532ddd728d12f4'),
    'arch-kde': (42520723419, '73cd02cabd8f68fa9d6408a596532ddd728d12f4'),
    'arch-gnome': (42520725799, '73cd02cabd8f68fa9d6408a596532ddd728d12f4'),
}


def sha(path, limit=None):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        while limit is None or limit:
            chunk = stream.read(min(limit, 1024**2) if limit is not None else 1024**2)
            if not chunk:
                break
            result.update(chunk)
            if limit is not None:
                limit -= len(chunk)
    if limit:
        raise ValueError('short ZIP prefix')
    return result.hexdigest()


def gh(*args):
    return subprocess.check_output(['gh', *args], text=True)


def authenticate(repository, profile, run, sdk, directory):
    if profile not in APPROVED_IMAGES or APPROVED_IMAGES[profile][0] != run:
        raise ValueError('only the nine verified legacy-audio images may be refreshed')
    tag = f'payload-{profile}-{run}'
    release = json.loads(gh('api', f'repos/{repository}/releases/tags/{tag}'))
    if release['draft'] or not re.fullmatch('[a-f0-9]{40}', release['target_commitish']):
        raise ValueError('base delivery is not an immutable published source')
    assets = {a['name']: a for a in release['assets']}
    names = ['catalog.json', 'catalog.json.sig', 'delivery.json', 'image-build.json',
             'trust-root.ed25519.pub', 'SHA256SUMS', f'installer-data-{profile}.json']
    if any(not 0 < assets[name]['size'] <= 2 * 1024**2 for name in names):
        raise ValueError('base delivery metadata exceeds its budget')
    command = ['release', 'download', tag, '--repo', repository, '--dir', str(directory)]
    for name in names:
        command += ['--pattern', name]
    gh(*command)
    sums = {name.lstrip('*'): value for value, name in
            (line.split(maxsplit=1) for line in (directory / 'SHA256SUMS').read_text().splitlines())}
    for name in names:
        if ((directory / name).stat().st_size != assets[name]['size']
                or name != 'SHA256SUMS' and sha(directory / name) != sums[name]):
            raise ValueError('base metadata checksum mismatch: ' + name)
    public = (sdk / 'Release/trust-root.ed25519.pub').read_bytes()
    if len(public) != 32 or (directory / 'trust-root.ed25519.pub').read_bytes() != public:
        raise ValueError('base delivery uses a different trust root')
    key = directory / 'public.der'
    key.write_bytes(bytes.fromhex('302a300506032b6570032100') + public)
    subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-keyform', 'DER', '-inkey',
                    str(key), '-rawin', '-in', str(directory / 'catalog.json'),
                    '-sigfile', str(directory / 'catalog.json.sig')], check=True, capture_output=True)
    verified = json.loads(gh('attestation', 'verify', str(directory / 'catalog.json'),
        '--repo', repository, '--signer-workflow', WORKFLOW, '--signer-digest', OLD_PRODUCER,
        '--source-digest', release['target_commitish'], '--deny-self-hosted-runners', '--format', 'json'))
    subjects = {(s['name'], s['digest']['sha256']) for v in verified
                for s in v['verificationResult']['statement']['subject']}
    if not set(sums.items()) <= subjects:
        raise ValueError('base provenance does not cover every asset')
    image = json.loads((directory / 'image-build.json').read_text())
    if (image['id'] != run or image['conclusion'] != 'success' or image['head_branch'] != 'main'
            or image['head_repository']['full_name'] != repository
            or image['path'] != '.github/workflows/images.yml'
            or image['head_sha'] != release['target_commitish']
            or image['head_sha'] != APPROVED_IMAGES[profile][1]):
        raise ValueError('base delivery does not match the selected native image')
    catalog = json.loads((directory / 'catalog.json').read_text())
    models = catalog['models']
    if len(models) != 54 or len({m['deviceIdentifier'] for m in models}) != 54:
        raise ValueError('expected the complete original 54-model delivery')
    if any(m['status'] != 'enabled' or m['installationProfile'] != profile for m in models):
        raise ValueError('base model/profile binding differs')
    delivery = json.loads((directory / 'delivery.json').read_text())
    if (delivery['installation_profile'] != profile or delivery['private_neo_inputs']
            or delivery['native_neo_reset_qa']):
        raise ValueError('private or machine-specific base delivery refused')
    metadata = json.loads((directory / f'installer-data-{profile}.json').read_text())
    classes = metadata['os_list'][0]['aurora_stage1']['qualified_machines']
    if {m['deviceIdentifier'] for m in models} != {'apple,' + k.removesuffix('ap') for k in classes}:
        raise ValueError('base model cohort differs from its metadata')
    meta = delivery['artifacts'][f'installer-data-{profile}.json']
    if (sha(directory / f'installer-data-{profile}.json') != meta['sha256']
            or (directory / f'installer-data-{profile}.json').stat().st_size != meta['bytes']
            or any(m['metadataDigest'] != 'sha256:' + meta['sha256'] for m in models)):
        raise ValueError('base metadata binding differs')
    payload_name = metadata['os_list'][0]['package']
    expected = delivery['artifacts'][payload_name]
    if any(m['payloadDigest'] != 'sha256:' + expected['sha256'] for m in models):
        raise ValueError('base payload binding differs')
    parts = models[0]['payloadArtifact']['parts']
    if any(m['payloadArtifact']['parts'] != parts for m in models):
        raise ValueError('base models do not share one payload')
    payload = directory / 'base.zip'
    with payload.open('xb') as output:
        for part in parts:
            asset = assets[part['fileName']]
            if asset['size'] != part['sizeBytes'] or asset['digest'] != part['sha256']:
                raise ValueError('base part inventory differs')
            process = subprocess.Popen(['gh', 'api', '-H', 'Accept: application/octet-stream',
                f'repos/{repository}/releases/assets/{asset["id"]}'], stdout=subprocess.PIPE)
            size, digest = 0, hashlib.sha256()
            while chunk := process.stdout.read(1024**2):
                size += len(chunk)
                if size > part['sizeBytes']:
                    process.kill()
                    process.wait()
                    raise ValueError('base part exceeds its signed size')
                digest.update(chunk)
                output.write(chunk)
            if process.wait() or size != part['sizeBytes'] or 'sha256:' + digest.hexdigest() != part['sha256']:
                raise ValueError('base part failed its signed digest/size')
    if payload.stat().st_size != expected['bytes'] or sha(payload) != expected['sha256']:
        raise ValueError('base ZIP failed its signed digest/size')
    return payload, metadata, delivery, image, {'repository': repository, 'tag': tag,
        'source_revision': image['head_sha'], 'catalog_sha256': sha(directory / 'catalog.json')}


def refresh(base, metadata, delivery, assembly, identifiers, output, release_id):
    if len(set(identifiers)) != 58 or not OLED <= set(identifiers) or output.exists():
        raise ValueError('expected all 58 models and a new output directory')
    metadata = copy.deepcopy(metadata)
    template = metadata['os_list'][0]
    profile = template['installation_profile']
    if len(metadata['os_list']) != 1 or not re.fullmatch('(fedora|arch|nixos)-(minimal|kde|gnome)', profile):
        raise ValueError('unexpected base metadata profile')
    old = {'apple,' + name.removesuffix('ap') for name in template['aurora_stage1']['qualified_machines']}
    if old != set(identifiers) - OLED or len(old) != 54:
        raise ValueError('refresh must preserve every original supported model')
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9.-]{0,79}', release_id):
        raise ValueError('invalid refreshed release identifier')
    record = delivery['artifacts'][template['package']]
    if base.stat().st_size != record['bytes'] or sha(base) != record['sha256']:
        raise ValueError('base ZIP identity differs from its verified delivery')
    records = {}
    with zipfile.ZipFile(base) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError('duplicate base ZIP member')
        common_name = 'esp/m1n1/boot.bin'
        if archive.getinfo(common_name).file_size > 64 * 1024**2:
            raise ValueError('common Stage 2 exceeds its budget')
        common = archive.read(common_name)
        stage1 = template['aurora_macho_boot']['stage1_member']
        if stage1 != 'esp/aurora/stage1.macho' or stage1 not in archive.namelist():
            raise ValueError('OLED refresh requires the existing Mach-O Stage 1')
        assembly.aurora_recovery.validate_payload(archive)
        for board, member in template['aurora_stage2']['board_members'].items():
            if not re.fullmatch(r'esp/m1n1/boot-[jk][0-9]+[a-z]*\.bin', member):
                raise ValueError('unsafe base board member')
            with archive.open(member) as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            records[board] = {'member': member, 'bytes': archive.getinfo(member).file_size, 'sha256': digest}
        generated = assembly.oled_stage2_images(common, {
            'sourceSha256': hashlib.sha256(common).hexdigest(), 'boards': records})
        if set(generated) != {name.removeprefix('apple,') for name in OLED}:
            raise ValueError('refresh did not produce every missing OLED board')
        additions = {}
        for board, (data, _) in generated.items():
            for name in (f'esp/m1n1/boot-{board}.bin', f'esp/m1n1/boot-ctxr-{board}.bin'):
                if name in archive.namelist():
                    raise ValueError('OLED member already exists in the base ZIP')
                additions[name] = data
        prefix_bytes = archive.start_dir
    output.mkdir(parents=True)
    payload = output / f'{profile}-{release_id}.zip'
    minimum_free = int(os.environ.get('COHORT58_MIN_FREE_BYTES', str(8 * 1024**3)))
    if shutil.disk_usage(output).free - base.stat().st_size - 80 * 1024**2 < minimum_free:
        raise ValueError('ZIP refresh would cross the free-space floor')
    with base.open('rb') as source, payload.open('xb') as target:
        while chunk := source.read(1024**2):
            if shutil.disk_usage(output).free - len(chunk) < minimum_free:
                raise ValueError('ZIP refresh reached the free-space floor; partial retained')
            target.write(chunk)
    with zipfile.ZipFile(payload, 'a') as archive:
        for name, data in sorted(additions.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_STORED)
    prefix_hash = sha(base, prefix_bytes)
    if sha(payload, prefix_bytes) != prefix_hash:
        raise ValueError('ZIP refresh changed existing compressed OS/boot data')
    tail = output / (payload.name + '.tail')
    with payload.open('rb') as stream, tail.open('xb') as target:
        stream.seek(prefix_bytes)
        shutil.copyfileobj(stream, target, 1024**2)
    if tail.stat().st_size > 80 * 1024**2:
        raise ValueError('ZIP refresh tail exceeds its budget')
    for board in sorted(generated):
        template['aurora_stage1']['qualified_machines'][board + 'ap'] = ['27.2']
        template['aurora_stage2']['board_members'][board + 'ap'] = f'esp/m1n1/boot-{board}.bin'
        template['aurora_macho_boot']['confined_members'].append(f'esp/m1n1/boot-ctxr-{board}.bin')
    template['package'] = payload.name
    meta = output / f'installer-data-{profile}.json'
    meta.write_text(json.dumps(metadata, indent=2) + '\n')
    artifacts = {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)} for p in (payload, meta)}
    refreshed = {'schema': 1, 'installation_profile': profile, 'release_id': release_id,
                 'private_neo_inputs': False, 'native_neo_reset_qa': False,
                 'physical_qualification': 'pending', 'artifacts': artifacts}
    (output / 'delivery.json').write_text(json.dumps(refreshed, indent=2) + '\n')
    patch = {'schema': 1, 'base_payload_sha256': record['sha256'], 'base_payload_bytes': record['bytes'],
             'prefix_bytes': prefix_bytes, 'prefix_sha256': prefix_hash,
             'tail_name': tail.name, 'tail_bytes': tail.stat().st_size, 'tail_sha256': sha(tail),
             'payload_name': payload.name, **artifacts[payload.name],
             'common_stage2_sha256': hashlib.sha256(common).hexdigest(),
             'added_members': {n: {'bytes': len(d), 'sha256': hashlib.sha256(d).hexdigest()}
                               for n, d in sorted(additions.items())},
             'existing_compressed_members_changed': False}
    (output / 'zip-refresh.json').write_text(json.dumps(patch, indent=2) + '\n')
    return patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--image-run', required=True, type=int)
    parser.add_argument('--producer', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--release-id', required=True)
    args = parser.parse_args()
    if (not re.fullmatch(r'(fedora|arch|nixos)-(minimal|kde|gnome)', args.profile)
            or args.repository != 'aurora-silicon/' + REPOSITORIES[args.profile.split('-')[0]]):
        raise ValueError('repository/profile mismatch')
    spec = importlib.util.spec_from_file_location('cohort58_assembly',
        args.producer / 'scripts/assemble-distribution-payload.py')
    assembly = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(assembly)
    import supported_models
    identifiers, _ = supported_models.load()
    with tempfile.TemporaryDirectory(prefix='cohort58-base-') as temporary:
        base, metadata, delivery, image, lineage = authenticate(
            args.repository, args.profile, args.image_run, args.producer, Path(temporary))
        patch = refresh(base, metadata, delivery, assembly, identifiers, args.output, args.release_id)
        (args.output / 'image-build.json').write_text(json.dumps(image, indent=2) + '\n')
        (args.output / 'source-delivery.json').write_text(json.dumps(lineage, indent=2) + '\n')
        print(json.dumps({'models': len(identifiers), 'payload': patch['payload_name'],
                          'sha256': patch['sha256'], 'tail_bytes': patch['tail_bytes']}))


if __name__ == '__main__':
    main()
