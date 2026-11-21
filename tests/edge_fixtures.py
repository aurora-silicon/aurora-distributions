"""Shared fixtures for the multi-profile Edge train tests."""
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import edge_train  # noqa: E402

LINUX = {'repository': 'aurora-silicon/linux', 'branch': 'aurora-edge',
         'revision': '1' * 40, 'tree': '2' * 40}
MESA = {'repository': 'aurora-silicon/mesa', 'branch': 'aurora-edge',
        'revision': '3' * 40, 'tree': '4' * 40}
REFERENCE = {'kernelImageSHA256': 'sha256:' + '5' * 64, 'mesaDsoSHA256': 'sha256:' + '6' * 64,
             'mesaSigningRunID': 44700000001}
DISTRIBUTION_FILES = {
    'fedora': {'kernelImageSHA256': REFERENCE['kernelImageSHA256'], 'mesaDsoSHA256': REFERENCE['mesaDsoSHA256']},
    'arch': {'kernelImageSHA256': 'sha256:' + '7' * 64, 'mesaDsoSHA256': 'sha256:' + '8' * 64},
    'nixos': {'kernelImageSHA256': 'sha256:' + '9' * 64, 'mesaDsoSHA256': 'sha256:' + 'a' * 64},
}
DISTRIBUTION_FILES['omarchy'] = dict(DISTRIBUTION_FILES['arch'])
SNAPSHOTS = {'fedora': 'edge-20261121.1', 'arch': 'edge-20261121.1', 'nixos': 'b' * 40,
             'omarchy': 'testing-20261121.2'}


def cohort():
    return json.loads((ROOT / 'edge/cohort.json').read_text())


def train(riders=()):
    record = {
        'schema_version': 1, 'release': 'alpha.4', 'riders': list(riders),
        'pair': {'linux': dict(LINUX), 'mesa': dict(MESA)}, 'reference': dict(REFERENCE),
        'distributions': {
            name: {'repository': edge_train.DISTRIBUTIONS[name]['repository'], 'snapshot': SNAPSHOTS[name]}
            for name in edge_train.DISTRIBUTIONS},
        'profiles': {profile: {'image_run': 45000000000 + index}
                     for index, profile in enumerate(cohort()['profiles'])},
    }
    record['distributions']['omarchy']['alarm_snapshot'] = SNAPSHOTS['arch']
    return record


def staged(record):
    """The train after commit B: every profile carries its staging run and digest."""
    result = copy.deepcopy(record)
    for index, entry in enumerate(result['profiles'].values()):
        entry['stage'] = {'run': 46000000000, 'sha256': '%064x' % (index + 1)}
    return result


MANIFESTS = {}


def manifest(label):
    """A sha256sum manifest, as a row's owner publishes it in edge/manifests/<sha256>.sha256."""
    raw = ('%s  results/%s.log\n' % (hashlib.sha256(label.encode()).hexdigest(), label)).encode()
    MANIFESTS[hashlib.sha256(raw).hexdigest()] = raw
    return raw


def check(label='row'):
    return {'status': 'passed', 'sha256': 'sha256:' + hashlib.sha256(manifest(label)).hexdigest()}


def manifests():
    return dict(MANIFESTS)


def evidence(record, cohort_record=None):
    cohort_record = cohort_record or cohort()
    families = {}
    for name, spec in edge_train.qualified_families(cohort_record, record).items():
        families[name] = {'machine': spec['machines'][0], 'pair': dict(REFERENCE),
                          'checks': {gate: check(name + '-' + gate) for gate in spec['gates']}}
    distributions = {name: {'machine': 'apple,j314s', 'pair': dict(DISTRIBUTION_FILES[name]),
                            'checks': {gate: check(name + '-' + gate) for gate in cohort_record['gates']['distribution']}}
                     for name in edge_train.DISTRIBUTIONS}
    return {'families': families, 'distributions': distributions}


def image_run(record, profile):
    distribution = edge_train.distribution(profile)
    return {'id': record['profiles'][profile]['image_run'], 'status': 'completed', 'conclusion': 'success',
            'event': 'workflow_dispatch', 'head_branch': 'main', 'head_sha': 'c' * 40,
            'path': '.github/workflows/images.yml',
            'head_repository': {'full_name': edge_train.DISTRIBUTIONS[distribution]['repository']}}


def image_proof(record, profile):
    distribution = edge_train.distribution(profile)
    proof = {'schema': 2, 'distribution': distribution, 'profile': profile, 'channel': 'edge',
             'snapshot': record['distributions'][distribution]['snapshot'],
             'feed': edge_train.DISTRIBUTIONS[distribution]['feed'],
             'packaged_sources': copy.deepcopy(record['pair']),
             'files': dict(DISTRIBUTION_FILES[distribution])}
    if distribution == 'omarchy':
        proof['alarm_snapshot'] = record['distributions']['omarchy']['alarm_snapshot']
    return proof


def metadata(models):
    boards = [edge_train.board(model) for model in models]
    return {'os_list': [{'allowed_device_classes': boards,
                         'aurora_stage1': {'qualified_machines': {board: ['27.2'] for board in boards}},
                         'aurora_stage2': {'board_members': {board: 'esp/m1n1/boot-' + board[:-2] + '.bin'
                                                             for board in boards}}}]}


def template(record, profile, cohort_record=None, extra=('apple,j813',)):
    """An unsigned staged catalog template, as prepare-edge-payload.py leaves it."""
    cohort_record = cohort_record or cohort()
    models = edge_train.profile_models(cohort_record, record, profile)
    tag = edge_train.release_tag(record, profile)
    base = 'https://github.com/' + edge_train.owner(profile) + '/releases/download/' + tag
    raw_metadata = json.dumps(metadata(models)).encode()
    name = 'installer-data-' + profile + '.json'
    rows = [{'deviceIdentifier': model, 'installationProfile': profile, 'status': 'enabled',
             'payloadDigest': 'sha256:' + 'd' * 64,
             'payloadArtifact': {'sourceURL': base + '/os.zip', 'fileName': 'os.zip', 'sizeBytes': 123},
             'metadataDigest': 'sha256:' + hashlib.sha256(raw_metadata).hexdigest(),
             'metadataArtifact': {'sourceURL': base + '/' + name, 'fileName': name,
                                  'sizeBytes': len(raw_metadata)}}
            for model in list(models) + list(extra)]
    catalog = {'schemaVersion': 5, 'sequence': 1795200000, 'models': rows}
    receipt = {'installation_profile': profile, 'private_neo_inputs': False, 'native_neo_reset_qa': False,
               'release_id': edge_train.release_id(record, profile),
               'artifacts': {'os.zip': {'bytes': 123, 'sha256': 'd' * 64}}}
    return catalog, receipt, raw_metadata
