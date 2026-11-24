"""Channel identity and qualified Edge Alpha lineage. No publishing side effects."""
import hashlib
import json
import re
import subprocess
import urllib.parse

CHANNELS = ('stable', 'rc', 'testing', 'edge')
LEGACY_CHANNELS = {'stable', 'rc', 'edge'}
REVISION = re.compile(r'^[0-9a-f]{40}$')
DIGEST = re.compile(r'^sha256:[0-9a-f]{64}$')
EDGE_SOURCE_BRANCHES = {
    1: {'linux': 'aurora-edge', 'mesa': 'aurora-edge'},
    2: {'linux': 'main', 'mesa': 'aurora-wip'},
}


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def edge_source_branches(proof):
    branches = {}
    for name in ('linux', 'mesa'):
        source = proof.get(name)
        if (not isinstance(source, dict) or source.get('repository') != 'aurora-silicon/' + name
                or not _matches(REVISION, source.get('revision')) or not _matches(REVISION, source.get('tree'))):
            raise ValueError('invalid packaged Edge source reference')
        branches[name] = source.get('branch')
    if branches not in EDGE_SOURCE_BRANCHES.values():
        raise ValueError('invalid Linux and Mesa Edge source branches')
    return branches


def legacy_development_catalog(catalog):
    repositories = {'fedora': 'fedora-aurora', 'arch': 'alarm-aurora',
                    'nixos': 'nixos-aurora', 'omarchy': 'omarchy-aurora'}
    for model in catalog['models']:
        if model.get('status') == 'disabled':
            continue
        profile = model.get('installationProfile', '')
        distribution = profile.split('-')[0]
        evidence = re.fullmatch(r'image-build-([1-9][0-9]*)', model.get('evidenceRevision', ''))
        if (distribution not in repositories or evidence is None
                or re.fullmatch(r'(?:fedora|arch|nixos)-(?:kde|gnome|minimal)|omarchy-hyprland', profile) is None):
            return False
        artifact = model.get('payloadArtifact', {})
        if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,239}', artifact.get('fileName', '')) is None:
            return False
        url = urllib.parse.urlsplit(artifact.get('sourceURL', ''))
        tag = 'payload-' + profile + '-' + evidence[1]
        base = '/aurora-silicon/' + repositories[distribution] + '/releases/download/'
        if (url.scheme != 'https' or url.netloc != 'github.com' or url.query or url.fragment
                or not any(url.path == base + tag + suffix + '/' + artifact.get('fileName', '')
                           for suffix in ('', '-models58', '-models65'))):
            return False
    return True


def descriptor(content):
    result = json.loads(content)
    channels = result.get('channels', {})
    legacy = set(channels) == LEGACY_CHANNELS
    if result.get('schema_version') != 3 or not (legacy or set(channels) == set(CHANNELS)):
        raise ValueError('descriptor must name stable, rc, testing and edge')
    if legacy:
        url = channels['edge']['catalog_url']
        suffix = '/channels/edge/catalog.signed.json'
        if not url.endswith(suffix):
            raise ValueError('legacy development channel URL is not canonical')
        channels['testing'] = {'catalog_url': url[:-len(suffix)] + '/channels/testing/catalog.signed.json'}
        channels['edge'] = {'catalog_url': url[:-len(suffix)] + '/channels/edge-alpha/catalog.signed.json'}
    default = result.get('default_channel', 'testing')
    if legacy and default == 'edge':
        default = 'testing'
    if default not in ('stable', 'rc', 'testing'):
        raise ValueError('Edge is opt-in and cannot be the descriptor default')
    result['default_channel'] = default
    result['channels'] = {channel: channels[channel] for channel in CHANNELS}
    if len({entry['catalog_url'] for entry in result['channels'].values()}) != len(CHANNELS):
        raise ValueError('channel endpoints must be distinct')
    return (json.dumps(result, indent=2) + '\n').encode()


def validate_catalog(catalog, channel, legacy_development=False):
    if channel not in CHANNELS:
        raise ValueError('unknown release channel')
    declared = catalog.get('channel')
    if declared is not None and declared not in CHANNELS:
        raise ValueError('invalid signed catalog channel')
    if channel != 'edge':
        if channel == 'testing' and declared is None and catalog['models'] and not legacy_development:
            raise ValueError('unchannelled testing requires authenticated legacy-development lineage')
        if declared is not None and declared != channel:
            raise ValueError('signed catalog belongs to a different channel')
        return
    models = catalog['models']
    if not models and declared in (None, 'edge'):
        return
    if declared != 'edge':
        raise ValueError('legacy/testing deliveries cannot become Edge')
    if catalog.get('schemaVersion') != 5:
        raise ValueError('Edge must use a native-only schema 5 catalog')
    if any(field in model for model in models for field in
           ('engineArtifact', 'engineDigest', 'engineVersion', 'repairManifestArtifact', 'repairManifestDigest')):
        raise ValueError('Edge must use only the native installer engine')
    proof = catalog.get('edgeQualification', {})
    if not isinstance(proof, dict):
        raise ValueError('invalid Edge qualification object')
    if (type(proof.get('schemaVersion')) is not int or proof['schemaVersion'] != 1
            or proof.get('releaseKind') != 'alpha' or proof.get('status') != 'passed'
            or type(proof.get('imageRunID')) is not int or not 0 < proof['imageRunID'] < 1 << 64
            or not _matches(DIGEST, proof.get('payloadDigest'))
            or not _matches(DIGEST, proof.get('evidenceDigest'))):
        raise ValueError('Edge requires complete passed release qualification')
    edge_source_branches(proof)
    if any(model.get('payloadDigest') != proof['payloadDigest'] for model in models):
        raise ValueError('Edge qualification does not bind the catalog payload')


def verify_edge_delivery(catalog, raw_report, receipt, image, base, testing=None):
    """Call only after hosted provenance authenticates the report and catalog."""
    validate_catalog(catalog, 'edge')
    proof = catalog['edgeQualification']
    expected = {key: value for key, value in proof.items() if key != 'evidenceDigest'}
    if ('sha256:' + hashlib.sha256(raw_report).hexdigest() != proof['evidenceDigest']
            or json.loads(raw_report) != expected or proof['imageRunID'] != image['id']
            or receipt.get('release_channel') != 'edge' or receipt.get('release_kind') != 'alpha'):
        raise ValueError('Edge evidence differs from its authenticated image and receipt')
    if receipt.get('packaged_sources') != {name: proof[name] for name in ('linux', 'mesa')}:
        raise ValueError('Edge qualification differs from the packaged Linux/Mesa sources')
    if testing and any(model.get('payloadDigest') == proof['payloadDigest']
                       for model in testing['models']):
        raise ValueError('an existing testing payload cannot be relabelled as Edge')
    for model in catalog['models']:
        payload = model['payloadArtifact']
        if receipt.get('artifacts', {}).get(payload['fileName']) != {
                'bytes': payload['sizeBytes'], 'sha256': proof['payloadDigest'].removeprefix('sha256:')}:
            raise ValueError('Edge qualification differs from the payload receipt')
        for field in ('payloadArtifact', 'metadataArtifact'):
            artifact = model[field]
            for part in [artifact] + (artifact.get('parts') or []):
                name = part.get('fileName', '')
                if (not isinstance(name, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,239}', name) is None
                        or part.get('sourceURL') != base + '/' + name):
                    raise ValueError('Edge artifacts must belong to the new Edge Alpha release')


def github_json(path):
    result = subprocess.run(['gh', 'api', path], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def verify_edge_source_refs(proof, fetch=github_json):
    """Resolve packaged revision/tree pairs and current Edge ancestry independently."""
    for name, branch in edge_source_branches(proof).items():
        source = proof[name]
        repository = 'aurora-silicon/' + name
        revision, tree = source['revision'], source['tree']
        commit = fetch(f'repos/{repository}/git/commits/{revision}')
        head = fetch(f'repos/{repository}/git/ref/heads/{branch}').get('object', {}).get('sha')
        if not _matches(REVISION, head):
            raise ValueError('could not resolve Edge source branch ' + branch)
        comparison = fetch(f'repos/{repository}/compare/{revision}...{head}')
        if (commit.get('sha') != revision or commit.get('tree', {}).get('sha') != tree
                or comparison.get('status') not in ('ahead', 'identical')
                or comparison.get('base_commit', {}).get('sha') != revision):
            raise ValueError('packaged source is not the recorded Edge tree or branch ancestor')
