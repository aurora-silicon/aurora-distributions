#!/usr/bin/env python3
"""Re-sign the image deliveries as native-only catalogs for an installer release.

Deliveries are schema 5 and name no engine. Schema 4 deliveries built before
that are still accepted, and their engine pins are dropped. The empty stable
and rc channel catalogs are re-signed as schema 5 as well, so an installer that
reads only schema 5 sees those channels as having no release yet.
"""

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

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from channel_policy import descriptor as channel_descriptor, validate_catalog, legacy_development_catalog

ROOT = Path(__file__).resolve().parents[1]
CORE_PROFILES = tuple(f'{distribution}-{desktop}' for distribution in ('fedora', 'arch', 'nixos')
                      for desktop in ('kde', 'gnome', 'minimal'))
PROFILES = CORE_PROFILES + ('omarchy-hyprland',)
FILES = ('release.json', 'trust-root.ed25519.pub', 'catalog.json', 'catalog.json.sig',
         'delivery.json', 'image-build.json')
VM_CHECKS = ('boot', 'encryption', 'temporary_key_rejected', 'owner', 'sudo', 'second_boot',
             'desktop', 'shell', 'terminal', 'network', 'original_unchanged', 'package_trust')
PUBLIC_DER_PREFIX = bytes.fromhex('302a300506032b6570032100')
EXECUTION_FIELDS = ('engineVersion', 'engineDigest', 'engineArtifact',
                    'repairManifestDigest', 'repairManifestArtifact')
EMPTY_CHANNELS = ('stable', 'rc')
# Boards that boot only their own board Stage 2, never the combined one: the
# Neo and the M4 and later Macs (the installer's assemble-distribution-payload.py).
BOARD_STAGE2_REQUIRED = ('j700ap', 'j604ap', 'j623ap', 'j624ap', 'j713ap', 'j715ap', 'j773gap',
                         'j614sap', 'j614cap', 'j616sap', 'j616cap', 'j773sap', 'j575cap',
                         'j813ap', 'j873gap', 'j704ap', 'j815ap', 'j804ap', 'j833ctap',
                         'j714cap', 'j714sap', 'j716cap', 'j716sap', 'j775cap', 'j775dap', 'j873sap')


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
    manifest = json.loads((ROOT/'keys/supported-models.json').read_text())
    allowed = set(manifest['supported'])
    legacy_models = set(json.loads((ROOT/'keys/supported-models-58.json').read_text())['supported'])
    expanded_models = set(json.loads((ROOT/'keys/supported-models-65.json').read_text())['supported'])
    if allowed == legacy_models:
        profiles = CORE_PROFILES
    elif allowed == expanded_models:
        profiles = PROFILES
    else:
        raise ValueError('published model manifest does not match a release cohort')
    profile_paths = list((source/'Profiles').iterdir())
    if ({path.name for path in profile_paths} != set(profiles)
            or any(path.is_symlink() or not path.is_dir() for path in profile_paths)):
        raise ValueError('profiles do not contain the complete published model cohort')
    stage1_unqualified = set(manifest.get('stage1_unqualified', {})) & allowed
    now = int(time.time())
    issued = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    url = ('https://github.com/aurora-silicon/aurora-installer/releases/tag/' + release_tag)
    prepared = {}
    edge_catalogs = {}
    receipt = {'schema': 1, 'source_revision': source_revision, 'source_catalog_schemas': [],
               'output_catalog_schema': 5, 'release_tag': release_tag, 'profiles': {}}
    for profile in profiles:
        directory = source/'Profiles'/profile
        data = {}
        names = FILES + (('vm-result.json',) if profile == 'omarchy-hyprland' else ())
        for name in names:
            path = directory/name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1048576:
                raise ValueError(f'unsafe or missing profile file: {profile}/{name}')
            data[name] = path.read_bytes()
        if profile == 'omarchy-hyprland':
            vm = json.loads(data['vm-result.json'])
            if not all(vm.get(check) is True for check in VM_CHECKS):
                raise ValueError('Omarchy release requires complete passing VM qualification')
        if data['trust-root.ed25519.pub'] != public or len(data['catalog.json.sig']) != 64:
            raise ValueError(f'{profile} has a different signing root')
        verify(data['catalog.json'], data['catalog.json.sig'], public)
        catalog = json.loads(data['catalog.json'])
        source_image = json.loads(data['image-build.json'])
        legacy_development = (source_image.get('head_branch') == 'main'
                              and source_image.get('path') == '.github/workflows/images.yml'
                              and source_image.get('conclusion') == 'success'
                              and legacy_development_catalog(catalog))
        validate_catalog(catalog, 'testing', legacy_development=legacy_development)
        models = catalog['models']
        if (catalog['schemaVersion'] not in (4, 5) or catalog['sequence'] >= now
                or len(models) != len(allowed)
                or {model['deviceIdentifier'] for model in models} != allowed
                or any(model['status'] != 'enabled' or model['installationProfile'] != profile
                       or model.get('repairManifestArtifact') is not None
                       or model.get('repairManifestDigest') is not None for model in models)):
            raise ValueError(f'{profile} is not a complete public profile')
        if catalog['schemaVersion'] == 5 and any(field in model for model in models
                                                 for field in EXECUTION_FIELDS):
            raise ValueError(f'{profile} is schema 5 but names an engine')
        if any(not all(key in model for key in ('metadataArtifact', 'metadataDigest',
                                               'payloadArtifact', 'payloadDigest')) for model in models):
            raise ValueError(f'{profile} is missing an authenticated delivery field')
        if catalog['schemaVersion'] not in receipt['source_catalog_schemas']:
            receipt['source_catalog_schemas'].append(catalog['schemaVersion'])
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
        # A board whose iBoot is known to refuse the common Stage 1 is listed in
        # stage1_unqualified may be left out of the qualification (the installer
        # then stops before any disk change); a payload built before it was marked
        # may still qualify it. Every other board needs it.
        unqualified = {identifier.removeprefix('apple,') + 'ap' for identifier in stage1_unqualified}
        if (not isinstance(policy, dict) or set(policy) != {'kind', 'qualified_machines'}
                or policy['kind'] != 'common-raw-v1'
                or not boards - unqualified <= set(policy['qualified_machines']) <= boards
                or any(not versions for versions in policy['qualified_machines'].values())):
            raise ValueError(f'{profile} lacks native Stage 1 policy for a supported board')
        # Every supported board must be admitted by the shared template. The
        # MacBook Neo and the M4 and later Macs boot only their own board Stage 2.
        admitted = template.get('allowed_device_classes')
        excluded = set(template.get('excluded_device_classes') or [])
        stage2 = template.get('aurora_stage2') or {}
        members = (stage2.get('board_members') or {}) if stage2.get('kind') == 'board-file-v1' else {}
        if (boards & excluded or (admitted is not None and not boards <= set(admitted))
                or any(board in boards and not members.get(board) for board in BOARD_STAGE2_REQUIRED)):
            raise ValueError(f'{profile} metadata does not admit every supported board')
        edge_path = directory/'channels/edge-alpha/catalog.signed.json'
        if edge_path.exists() or edge_path.is_symlink():
            if edge_path.is_symlink() or edge_path.stat().st_size > 1500000:
                raise ValueError('unsafe Edge catalog envelope')
            envelope = json.loads(edge_path.read_text())
            if (set(envelope) != {'schema_version', 'catalog', 'signature'}
                    or type(envelope['schema_version']) is not int or envelope['schema_version'] != 1):
                raise ValueError('invalid Edge catalog envelope')
            edge_raw = base64.b64decode(envelope['catalog'], validate=True)
            edge_signature = base64.b64decode(envelope['signature'], validate=True)
            verify(edge_raw, edge_signature, public)
            edge_catalog = json.loads(edge_raw)
            if edge_catalog.get('channel') != 'edge' or edge_catalog.get('schemaVersion') != 5:
                raise ValueError('legacy/testing data cannot occupy the Edge endpoint')
            validate_catalog(edge_catalog, 'edge')
            report = None
            if edge_catalog['models']:
                report_path = edge_path.parent/'edge-qualification.json'
                if report_path.is_symlink() or not report_path.is_file() or report_path.stat().st_size > 1048576:
                    raise ValueError('Edge has no bounded qualification evidence')
                report = report_path.read_bytes()
                proof = edge_catalog['edgeQualification']
                if ('sha256:' + hashlib.sha256(report).hexdigest() != proof['evidenceDigest']
                        or json.loads(report) != {k: v for k, v in proof.items() if k != 'evidenceDigest'}):
                    raise ValueError('Edge qualification differs')
                if any(model['payloadDigest'] == proof['payloadDigest'] for model in models):
                    raise ValueError('an existing testing payload cannot be relabelled as Edge')
            records = {}
            for name in FILES + ('vm-result.json',):
                path = edge_path.parent/name
                if path.exists() or path.is_symlink():
                    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1048576:
                        raise ValueError('unsafe Edge delivery record: '+name)
                    records[name] = path.read_bytes()
            if (records.get('catalog.json', edge_raw) != edge_raw
                    or records.get('catalog.json.sig', edge_signature) != edge_signature
                    or records.get('trust-root.ed25519.pub', public) != public):
                raise ValueError('Edge envelope and delivery records differ')
            edge_catalogs[profile] = (edge_raw, edge_signature, report, records)
        previous = hashlib.sha256(data['catalog.json']).hexdigest()
        catalog['schemaVersion'] = 5
        catalog['channel'] = 'testing'
        catalog['sequence'] = now
        catalog['issuedAt'] = issued
        catalog['installer'] = {'minimumVersion': version, 'latestVersion': version,
                                'downloadURL': url}
        for model in models:
            for field in EXECUTION_FIELDS:
                model.pop(field, None)
        data['catalog.json'] = (json.dumps(catalog, indent=2) + '\n').encode()
        data['release.json'] = channel_descriptor(data['release.json'])
        prepared[profile] = data
        receipt['profiles'][profile] = {
            'previous_catalog_sha256': previous,
            'catalog_sha256': hashlib.sha256(data['catalog.json']).hexdigest(),
            'metadata_sha256': hashlib.sha256(raw).hexdigest(), 'models': len(models)}
    def empty_catalog(channel):
        return (json.dumps({'schemaVersion': 5, 'sequence': now, 'issuedAt': issued,
                            'channel': channel, 'models': []}, indent=2) + '\n').encode()

    def write_empty_channels(directory):
        for channel in EMPTY_CHANNELS:
            target = directory/'channels'/channel
            target.mkdir(parents=True)
            empty = empty_catalog(channel)
            (target/'catalog.json').write_bytes(empty)
            subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(signing_key), '-rawin',
                            '-in', str(target/'catalog.json'),
                            '-out', str(target/'catalog.json.sig')], check=True)
            signature = (target/'catalog.json.sig').read_bytes()
            verify(empty, signature, public)
            (target/'catalog.signed.json').write_text(json.dumps({
                'schema_version': 1, 'catalog': base64.b64encode(empty).decode(),
                'signature': base64.b64encode(signature).decode()}, separators=(',', ':')) + '\n')

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
            channel = directory/'channels/testing'
            channel.mkdir(parents=True)
            (channel/'catalog.signed.json').write_text(json.dumps({
                'schema_version': 1, 'catalog': base64.b64encode(data['catalog.json']).decode(),
                'signature': base64.b64encode(signature).decode()}, separators=(',', ':')) + '\n')
            legacy = directory/'channels/edge'
            legacy.mkdir(parents=True)
            (legacy/'catalog.signed.json').write_bytes((channel/'catalog.signed.json').read_bytes())
            write_empty_channels(directory)
            edge = directory/'channels/edge-alpha'
            edge.mkdir(parents=True)
            if profile in edge_catalogs:
                raw, signature, report, records = edge_catalogs[profile]
                for name, content in records.items():
                    (edge/name).write_bytes(content)
                if report is not None:
                    (edge/'edge-qualification.json').write_bytes(report)
                (edge/'catalog.json').write_bytes(raw)
                (edge/'catalog.json.sig').write_bytes(signature)
            else:
                raw = empty_catalog('edge')
                (edge/'catalog.json').write_bytes(raw)
                subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(signing_key), '-rawin',
                                '-in', str(edge/'catalog.json'), '-out', str(edge/'catalog.json.sig')], check=True)
                signature = (edge/'catalog.json.sig').read_bytes()
            verify(raw, signature, public)
            (edge/'catalog.signed.json').write_text(json.dumps({
                'schema_version': 1, 'catalog': base64.b64encode(raw).decode(),
                'signature': base64.b64encode(signature).decode()}, separators=(',', ':')) + '\n')
        write_empty_channels(staged)
        for name in ('release.json', 'trust-root.ed25519.pub'):
            content = (source/name).read_bytes()
            (staged/name).write_bytes(channel_descriptor(content) if name == 'release.json' else content)
        for name in ('catalog.json', 'catalog.json.sig'):
            (staged/name).write_bytes((staged/'Profiles/fedora-kde'/name).read_bytes())
        for lane in ('testing', 'edge-alpha'):
            (staged/'channels'/lane).mkdir(parents=True)
            (staged/'channels'/lane/'catalog.signed.json').write_bytes(
                (staged/'Profiles/fedora-kde/channels'/lane/'catalog.signed.json').read_bytes())
        (staged/'channels/edge').mkdir(parents=True)
        (staged/'channels/edge/catalog.signed.json').write_bytes(
            (staged/'channels/testing/catalog.signed.json').read_bytes())
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
