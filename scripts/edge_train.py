"""One Edge release train: every profile, every cohort Mac, one aurora-edge pair.

`edge/cohort.json` is the reviewed policy: the ten profiles, the chip families
with their Macs and hardware gates, optional riders and exclusions.
`edge/train.json` pins one release: the Linux and Mesa aurora-edge pair, the
reference signed build the hardware rows ran, each distribution's Edge package
snapshot and each profile's image run (commit A), then each profile's staging
run and STAGING.sha256 digest (commit B). Nothing here publishes anything.
"""
import copy
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COHORT = ROOT / 'edge/cohort.json'
TRAIN = ROOT / 'edge/train.json'
SUPPORTED = ROOT / 'keys/supported-models-65.json'

DISTRIBUTIONS = {
    'fedora': {'repository': 'aurora-silicon/fedora-aurora',
               'feed': 'https://aurorasilicon.org/fedora-aurora/44/edge/aarch64',
               'snapshot': r'edge-[0-9]{8}\.[1-9][0-9]*'},
    'arch': {'repository': 'aurora-silicon/alarm-aurora',
             'feed': 'https://aurorasilicon.org/alarm-aurora/edge/aarch64',
             'snapshot': r'edge-[0-9]{8}\.[1-9][0-9]*'},
    'nixos': {'repository': 'aurora-silicon/nixos-aurora',
              'feed': 'github:aurora-silicon/nixos-aurora/channel-edge',
              'snapshot': r'[0-9a-f]{40}'},
    # Omarchy takes the kernel and Mesa from the ALARM Edge feed; its own
    # snapshot is the Omarchy overlay package set.
    'omarchy': {'repository': 'aurora-silicon/omarchy-aurora',
                'feed': 'https://aurorasilicon.org/alarm-aurora/edge/aarch64',
                'snapshot': r'testing-[0-9]{8}\.[1-9][0-9]*'},
}
PROFILE = re.compile(r'(?:fedora|arch|nixos)-(?:kde|gnome|minimal)|omarchy-hyprland')
RELEASE = re.compile(r'alpha\.[1-9][0-9]*')
REVISION = re.compile(r'[0-9a-f]{40}')
SHA256 = re.compile(r'sha256:[0-9a-f]{64}')
HEX64 = re.compile(r'[0-9a-f]{64}')
MODEL = re.compile(r'apple,[a-z0-9]+')
PAIR_KEYS = ('kernelImageSHA256', 'mesaDsoSHA256')


def _match(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _positive(value):
    return type(value) is int and 0 < value < 1 << 63


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def distribution(profile):
    if not _match(PROFILE, profile):
        raise ValueError('unknown Edge profile: ' + str(profile))
    return profile.split('-')[0]


def owner(profile):
    return DISTRIBUTIONS[distribution(profile)]['repository']


def board(model):
    return model.split(',', 1)[1] + 'ap'


def verify_cohort(cohort):
    if (not isinstance(cohort, dict) or cohort.get('schema_version') != 1
            or set(cohort) != {'schema_version', 'profiles', 'gates', 'families', 'riders', 'excluded'}):
        raise ValueError('invalid Edge cohort')
    profiles = cohort['profiles']
    if (not isinstance(profiles, list) or len(profiles) != 10 or len(set(profiles)) != 10
            or not all(_match(PROFILE, profile) for profile in profiles)
            or {distribution(profile) for profile in profiles} != set(DISTRIBUTIONS)):
        raise ValueError('the Edge cohort must name all ten profiles')
    gates = cohort['gates']
    if (not isinstance(gates, dict) or set(gates) != {'family', 'distribution'}
            or not all(isinstance(names, list) and names and len(set(names)) == len(names)
                       for names in gates.values())):
        raise ValueError('invalid Edge gate sets')
    supported = load(SUPPORTED)['supported']
    seen = set()
    groups = dict(cohort['families'])
    groups.update(cohort['riders'])
    if set(cohort['families']) & set(cohort['riders']):
        raise ValueError('a rider cannot share a family name')
    for name, spec in groups.items():
        models = spec.get('models')
        if (not re.fullmatch(r't[0-9]{3,4}x?', name) or not isinstance(models, list) or not models
                or not all(_match(MODEL, model) and model in supported for model in models)
                or seen & set(models) or len(set(models)) != len(models)):
            raise ValueError('invalid or overlapping Edge family: ' + name)
        seen |= set(models)
        if 'gates' in spec and (not isinstance(spec['gates'], list) or not spec['gates']):
            raise ValueError('invalid gates for Edge family: ' + name)
        stand_in = spec.get('qualifiedBy')
        if stand_in is not None and (name not in cohort['families'] or stand_in not in cohort['families']
                                     or stand_in == name or 'qualifiedBy' in cohort['families'][stand_in]
                                     or family_gates(cohort, stand_in) != family_gates(cohort, name)):
            raise ValueError('invalid Edge stand-in family for ' + name)
    for name, spec in cohort['riders'].items():
        if (not isinstance(spec.get('profiles'), list) or not spec['profiles']
                or not set(spec['profiles']) <= set(profiles) or 'gates' not in spec):
            raise ValueError('invalid Edge rider: ' + name)
    excluded = cohort['excluded']
    if (not isinstance(excluded, dict) or seen & set(excluded)
            or not all(_match(MODEL, model) and isinstance(reason, str) and reason
                       for model, reason in excluded.items())):
        raise ValueError('an excluded Mac is also listed, or has no reason')


def family_gates(cohort, name):
    spec = cohort['families'].get(name) or cohort['riders'][name]
    return list(spec.get('gates', cohort['gates']['family']))


def qualified_families(cohort, train, profile=None):
    """Families (and riders this train carries) with their gates, for one profile or all."""
    result = {}
    for name, spec in cohort['families'].items():
        result[name] = {'models': list(spec['models']), 'gates': family_gates(cohort, name),
                        'machines': list(cohort['families'][spec.get('qualifiedBy', name)]['models']),
                        # A sibling family's row admits a Mac to Edge, but proves nothing about
                        # its own chip: such Macs are listed without per-device evidence.
                        'proven': 'qualifiedBy' not in spec}
    for name in train.get('riders', []):
        spec = cohort['riders'][name]
        if profile is None or profile in spec['profiles']:
            result[name] = {'models': list(spec['models']), 'gates': family_gates(cohort, name),
                            'machines': list(spec['models']), 'proven': True}
    return result


def profile_models(cohort, train, profile):
    wanted = {model for spec in qualified_families(cohort, train, profile).values() for model in spec['models']}
    return [model for model in load(SUPPORTED)['supported'] if model in wanted]


def _source(record, name):
    return (isinstance(record, dict) and set(record) == {'repository', 'branch', 'revision', 'tree'}
            and record['repository'] == 'aurora-silicon/' + name and record['branch'] == 'aurora-edge'
            and _match(REVISION, record['revision']) and _match(REVISION, record['tree']))


def verify_train(train, cohort, staged=False):
    verify_cohort(cohort)
    if (not isinstance(train, dict) or train.get('schema_version') != 1
            or set(train) != {'schema_version', 'release', 'riders', 'pair', 'reference', 'distributions', 'profiles'}):
        raise ValueError('invalid Edge train record')
    if not _match(RELEASE, train['release']):
        raise ValueError('the Edge release must be alpha.<n>')
    riders = train['riders']
    if not isinstance(riders, list) or len(set(riders)) != len(riders):
        raise ValueError('invalid Edge riders')
    for name in riders:
        if name not in cohort['riders']:
            raise ValueError('unknown rider: ' + str(name))
    pair = train['pair']
    if not isinstance(pair, dict) or set(pair) != {'linux', 'mesa'} or not all(
            _source(pair[name], name) for name in pair):
        raise ValueError('the train pair must be Linux and Mesa aurora-edge revisions with their trees')
    reference = train['reference']
    if (not isinstance(reference, dict) or set(reference) != set(PAIR_KEYS) | {'mesaSigningRunID'}
            or not all(_match(SHA256, reference[key]) for key in PAIR_KEYS)
            or not _positive(reference['mesaSigningRunID'])):
        raise ValueError('invalid reference build for the hardware rows')
    distributions = train['distributions']
    if not isinstance(distributions, dict) or set(distributions) != set(DISTRIBUTIONS):
        raise ValueError('the train must name every distribution')
    for name, entry in distributions.items():
        expected = {'repository', 'snapshot'} | ({'alarm_snapshot'} if name == 'omarchy' else set())
        if (not isinstance(entry, dict) or set(entry) != expected
                or entry['repository'] != DISTRIBUTIONS[name]['repository']
                or not _match(re.compile(DISTRIBUTIONS[name]['snapshot']), entry['snapshot'])):
            raise ValueError('invalid Edge snapshot for ' + name)
    if distributions['omarchy']['alarm_snapshot'] != distributions['arch']['snapshot']:
        raise ValueError('Omarchy must build on the same ALARM Edge snapshot')
    profiles = train['profiles']
    if not isinstance(profiles, dict) or set(profiles) != set(cohort['profiles']):
        raise ValueError('the train must carry every Edge profile')
    runs = []
    for profile, entry in profiles.items():
        keys = {'image_run', 'stage'} if staged else {'image_run'}
        if not isinstance(entry, dict) or not keys <= set(entry) <= {'image_run', 'stage'}:
            raise ValueError('incomplete Edge staging record for ' + profile)
        if not _positive(entry['image_run']):
            raise ValueError('invalid image run for ' + profile)
        runs.append(entry['image_run'])
        stage = entry.get('stage')
        if stage is not None and (not isinstance(stage, dict) or set(stage) != {'run', 'sha256'}
                                  or not _positive(stage['run']) or not _match(HEX64, stage['sha256'])):
            raise ValueError('invalid Edge staging record for ' + profile)
    if len(set(runs)) != len(runs):
        raise ValueError('every profile needs its own image run')


def train_identity(train):
    """The train without its staging records, which commit B adds after staging."""
    identity = copy.deepcopy(train)
    for entry in identity['profiles'].values():
        entry.pop('stage', None)
    return identity


def train_digest(train):
    return digest(canonical(train_identity(train)))


def release_tag(train, profile):
    return 'payload-%s-%d-edge-%s' % (profile, train['profiles'][profile]['image_run'], train['release'])


def release_id(train, profile):
    return 'build-%d-edge-%s' % (train['profiles'][profile]['image_run'], train['release'])


def verify_image(run, raw_proof, cohort, train, profile):
    name = distribution(profile)
    repository = DISTRIBUTIONS[name]['repository']
    if (run.get('id') != train['profiles'][profile]['image_run'] or run.get('status') != 'completed'
            or run.get('conclusion') != 'success' or run.get('path') != '.github/workflows/images.yml'
            or run.get('head_branch') != 'main' or (run.get('head_repository') or {}).get('full_name') != repository
            or run.get('event') not in ('workflow_dispatch', 'workflow_run', 'push')
            or not _match(REVISION, run.get('head_sha'))):
        raise ValueError('expected the train image run on main for ' + profile)
    proof = json.loads(raw_proof)
    expected = {'schema', 'distribution', 'profile', 'channel', 'snapshot', 'feed', 'packaged_sources', 'files'}
    if name == 'omarchy':
        expected.add('alarm_snapshot')
    if (not isinstance(proof, dict) or not expected <= set(proof) <= expected | {'packages'}
            or proof['schema'] != 2 or proof['distribution'] != name or proof['profile'] != profile
            or proof['channel'] != 'edge'):
        raise ValueError('invalid Edge image proof for ' + profile)
    entry = train['distributions'][name]
    if proof['snapshot'] != entry['snapshot'] or proof.get('alarm_snapshot', entry.get('alarm_snapshot')) != entry.get(
            'alarm_snapshot'):
        raise ValueError('the image was built from another Edge snapshot')
    if proof['feed'] != DISTRIBUTIONS[name]['feed']:
        raise ValueError('the image does not follow the Edge feed')
    if proof['packaged_sources'] != train['pair']:
        raise ValueError('the image packaged a different Linux/Mesa pair')
    files = proof['files']
    if not isinstance(files, dict) or set(files) != set(PAIR_KEYS) or not all(
            _match(SHA256, files[key]) for key in PAIR_KEYS):
        raise ValueError('the image proof lacks its kernel and Mesa digests')
    if name == 'fedora' and files != {key: train['reference'][key] for key in PAIR_KEYS}:
        raise ValueError('the Fedora image must carry the reference build the rows ran')
    return proof


def _checks(record, gates, label):
    if not isinstance(record.get('checks'), dict) or set(record['checks']) != set(gates):
        raise ValueError('%s evidence must carry exactly the gates %s' % (label, ', '.join(gates)))
    for gate, check in record['checks'].items():
        if (not isinstance(check, dict) or set(check) != {'status', 'sha256'}
                or check['status'] != 'passed' or not _match(SHA256, check['sha256'])):
            raise ValueError('missing passing %s evidence: %s' % (label, gate))


def verify_family_record(record, spec, name, train):
    if not isinstance(record, dict) or set(record) != {'machine', 'pair', 'checks'}:
        raise ValueError('incomplete evidence for family ' + name)
    if record['machine'] not in spec['machines']:
        raise ValueError('the %s row ran on a machine outside its family' % name)
    if record['pair'] != train['reference']:
        raise ValueError('the %s row ran another kernel/Mesa pair' % name)
    _checks(record, spec['gates'], name)


def verify_distribution_record(record, cohort, train, name, files=None):
    machines = {model for spec in qualified_families(cohort, train).values() for model in spec['models']}
    if not isinstance(record, dict) or set(record) != {'machine', 'pair', 'checks'}:
        raise ValueError('incomplete evidence for distribution ' + name)
    if record['machine'] not in machines:
        raise ValueError('the %s smoke ran on a machine outside the cohort' % name)
    pair = record['pair']
    if not isinstance(pair, dict) or set(pair) != set(PAIR_KEYS) or not all(
            _match(SHA256, pair[key]) for key in PAIR_KEYS):
        raise ValueError('invalid %s smoke pair' % name)
    if files is not None and pair != files:
        raise ValueError('the %s smoke did not run that distribution\'s own kernel and Mesa' % name)
    _checks(record, cohort['gates']['distribution'], name)


def verify_evidence(evidence, cohort, train):
    """The complete owner evidence for a train: every family and every distribution."""
    families = qualified_families(cohort, train)
    if (not isinstance(evidence, dict) or set(evidence) != {'families', 'distributions'}
            or not isinstance(evidence['families'], dict) or set(evidence['families']) != set(families)
            or not isinstance(evidence['distributions'], dict)
            or set(evidence['distributions']) != set(DISTRIBUTIONS)):
        raise ValueError('Edge evidence must cover every family and every distribution')
    for name, spec in families.items():
        verify_family_record(evidence['families'][name], spec, name, train)
    for name in DISTRIBUTIONS:
        verify_distribution_record(evidence['distributions'][name], cohort, train, name)


def profile_evidence(evidence, cohort, train, profile, proof):
    verify_evidence(evidence, cohort, train)
    name = distribution(profile)
    verify_distribution_record(evidence['distributions'][name], cohort, train, name, proof['files'])
    families = qualified_families(cohort, train, profile)
    return ({family: copy.deepcopy(evidence['families'][family]) for family in families},
            copy.deepcopy(evidence['distributions'][name]))


def device_record_name(model):
    return 'edge-device-' + model.replace(',', '-') + '.json'


def _device_records(families, cohort, train, profile):
    """Detached per-device evidence: the exact bytes `edgeQualification.devices` digests.

    Each record names its Mac, the family row that qualified it ({machine, pair,
    checks}) and the shared Linux/Mesa pair. The installer defaults a Mac to
    Edge only when its record is present.
    """
    records = {}
    for name, spec in qualified_families(cohort, train, profile).items():
        if not spec['proven']:
            continue
        row = families[name]
        for model in spec['models']:
            record = {'schemaVersion': 1, 'deviceIdentifier': model, 'family': name,
                      'machine': row['machine'], 'pair': row['pair'], 'checks': row['checks'],
                      **copy.deepcopy(train['pair'])}
            records[model] = (json.dumps(record, sort_keys=True, indent=2) + '\n').encode()
    return records


def device_evidence(catalog, cohort, train, profile):
    """{asset name: bytes} of a bound catalog's detached device records."""
    families = catalog['edgeQualification']['familyEvidence']
    return {device_record_name(model): raw
            for model, raw in _device_records(families, cohort, train, profile).items()}


def verify_devices(catalog, records):
    """Check every `devices` digest against its detached record and its Mac."""
    proof = catalog['edgeQualification']
    listed = {row['deviceIdentifier'] for row in catalog['models']}
    devices = proof.get('devices')
    if not isinstance(devices, dict):
        raise ValueError('Edge qualification lacks per-device evidence')
    for model, entry in devices.items():
        if model not in listed:
            raise ValueError('device evidence for a Mac the catalog has not listed: ' + str(model))
        raw = records.get(device_record_name(model))
        if (not isinstance(entry, dict) or set(entry) != {'evidenceDigest'} or raw is None
                or digest(raw) != entry['evidenceDigest']):
            raise ValueError('missing or altered device evidence: ' + model)
        if json.loads(raw).get('deviceIdentifier') != model:
            raise ValueError('device evidence for %s names another device' % model)


def bind(catalog, receipt, run, raw_proof, evidence, cohort, train, profile):
    verify_train(train, cohort)
    if receipt.get('installation_profile') != profile or receipt.get('release_id') != release_id(train, profile):
        raise ValueError('the delivery receipt is not this train profile')
    proof = verify_image(run, raw_proof, cohort, train, profile)
    families, smoke = profile_evidence(evidence, cohort, train, profile, proof)
    models = profile_models(cohort, train, profile)
    rows = [row for row in catalog['models'] if row.get('deviceIdentifier') in models]
    if (catalog.get('schemaVersion') != 5 or catalog.get('channel') is not None or 'edgeQualification' in catalog
            or [row.get('deviceIdentifier') for row in rows] != models
            or any(row.get('installationProfile') != profile or row.get('status') != 'enabled' for row in rows)
            or not _match(SHA256, rows[0].get('payloadDigest'))
            or any(row.get('payloadDigest') != rows[0]['payloadDigest'] for row in rows)):
        raise ValueError('the unsigned template lacks the cohort model rows on one payload')
    report = {
        'schemaVersion': 1, 'releaseKind': 'alpha', 'status': 'passed',
        'imageRunID': run['id'], 'payloadDigest': rows[0]['payloadDigest'],
        **copy.deepcopy(train['pair']),
        'imageProofDigest': digest(raw_proof),
        'edgeTrain': {'release': train['release'], 'trainDigest': train_digest(train),
                      'cohortDigest': digest(canonical(cohort))},
        'familyEvidence': families, 'distributionEvidence': smoke,
        'devices': {model: {'evidenceDigest': digest(raw)}
                    for model, raw in _device_records(families, cohort, train, profile).items()},
    }
    if 't8140' in families:
        # The Alpha 22 installer reads the Neo's proof in its original shape.
        report['neoEvidence'] = {key: copy.deepcopy(families['t8140'][key]) for key in ('pair', 'checks')}
    raw = (json.dumps(report, sort_keys=True, indent=2) + '\n').encode()
    catalog.update(channel='edge', edgeQualification={**report, 'evidenceDigest': digest(raw)}, models=rows)
    receipt.update(release_channel='edge', release_kind='alpha', packaged_sources=copy.deepcopy(train['pair']))
    return raw


def verify_binding(catalog, receipt, run, raw_proof, cohort, train, profile):
    """Re-derive at adoption what bind() recorded; call after the channel policy checks."""
    verify_train(train, cohort)
    proof = verify_image(run, raw_proof, cohort, train, profile)
    report = catalog['edgeQualification']
    if ({name: report.get(name) for name in ('linux', 'mesa')} != train['pair']
            or report.get('imageProofDigest') != digest(raw_proof)
            or report.get('edgeTrain') != {'release': train['release'], 'trainDigest': train_digest(train),
                                           'cohortDigest': digest(canonical(cohort))}):
        raise ValueError('the Edge qualification belongs to another train, image or pair')
    if [row.get('deviceIdentifier') for row in catalog['models']] != profile_models(cohort, train, profile):
        raise ValueError('the Edge catalog does not carry exactly the train cohort')
    families = qualified_families(cohort, train, profile)
    recorded = report.get('familyEvidence')
    if not isinstance(recorded, dict) or set(recorded) != set(families):
        raise ValueError('the Edge qualification lacks a family record')
    for name, spec in families.items():
        verify_family_record(recorded[name], spec, name, train)
    verify_distribution_record(report.get('distributionEvidence'), cohort, train, distribution(profile),
                               proof['files'])
    expected = {model: {'evidenceDigest': digest(raw)}
                for model, raw in _device_records(recorded, cohort, train, profile).items()}
    if report.get('devices') != expected:
        raise ValueError('Edge per-device evidence differs from the family rows')
    neo = {key: recorded['t8140'][key] for key in ('pair', 'checks')} if 't8140' in recorded else None
    if report.get('neoEvidence') != neo:
        raise ValueError('Edge Neo evidence differs from the t8140 row')
    if receipt.get('release_id') != release_id(train, profile):
        raise ValueError('Edge receipt identity differs from the train')


def verify_metadata(catalog, raw, cohort, train, profile):
    template, = json.loads(raw)['os_list']
    boards = [board(model) for model in profile_models(cohort, train, profile)]
    stage1 = template['aurora_stage1']['qualified_machines']
    members = template['aurora_stage2']['board_members']
    name = 'installer-data-' + profile + '.json'
    if (len(catalog['models']) != len(boards)
            or any(model['metadataArtifact']['fileName'] != name
                   or model['metadataArtifact']['sizeBytes'] != len(raw)
                   or model['metadataDigest'] != digest(raw) for model in catalog['models'])
            or template.get('allowed_device_classes') != boards or set(stage1) != set(boards)
            or not all(stage1[item] and members.get(item) for item in boards)):
        raise ValueError('Edge metadata must bind exactly the cohort install policy')


def verify_set(items, cohort, train):
    """An Edge adoption is the whole train or nothing."""
    verify_train(train, cohort, staged=True)
    if (not isinstance(items, list) or any(
            not isinstance(item, dict) or set(item) != {'repository', 'tag'} for item in items)):
        raise ValueError('invalid Edge delivery set')
    expected = {(owner(profile), release_tag(train, profile)) for profile in cohort['profiles']}
    actual = {(item['repository'], item['tag']) for item in items}
    if len(items) != len(expected) or actual != expected:
        missing = ', '.join(sorted(tag for _, tag in expected - actual)) or 'unexpected deliveries'
        raise ValueError('Edge adoption requires the complete ten-profile train: ' + missing)
    return [{'repository': owner(profile), 'tag': release_tag(train, profile), 'profile': profile}
            for profile in cohort['profiles']]
