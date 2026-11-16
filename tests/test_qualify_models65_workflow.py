"""Keep actual-payload qualification read-only and bind its unsigned evidence."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/qualify-models65.yml'
PRODUCER = '68148f7b1b820059f65780e17bc27ccb576def49'


def run_blocks():
    lines = WORKFLOW.read_text().splitlines()
    blocks = []
    for index, line in enumerate(lines):
        if line != '        run: |':
            continue
        body = []
        for later in lines[index + 1:]:
            if later and not later.startswith('          '):
                break
            body.append(later[10:])
        blocks.append('\n'.join(body))
    return blocks


def python_blocks():
    return [re.search(r"python3 - <<'PY'\n(.*?)\nPY", block, re.S)[1]
            for block in run_blocks() if "python3 - <<'PY'" in block]


class QualificationPermissionsTests(unittest.TestCase):
    def test_only_read_permissions_and_fixed_hosted_matrix(self):
        workflow = WORKFLOW.read_text()
        permission_block = re.search(r'^permissions:\n((?:  [^\n]+\n)+)', workflow, re.M)[1]
        self.assertEqual({line.strip() for line in permission_block.splitlines()},
                         {'contents: read', 'attestations: read'})
        self.assertEqual(workflow.count('permissions:'), 1)
        self.assertIn('runs-on: ubuntu-24.04-arm', workflow)
        self.assertIn('max-parallel: 2', workflow)
        pairs = re.findall(r'- profile: ([a-z-]+)\n\s+repository: (aurora-silicon/[a-z-]+)', workflow)
        expected = [(distribution + '-' + desktop, 'aurora-silicon/' + repository)
                    for distribution, repository in [('fedora', 'fedora-aurora'),
                                                     ('arch', 'alarm-aurora'), ('nixos', 'nixos-aurora')]
                    for desktop in ('kde', 'gnome', 'minimal')]
        self.assertEqual(sorted(pairs), sorted(expected))
        self.assertEqual(len(pairs), 9)
        self.assertIn('ref: ' + PRODUCER, workflow)
        self.assertIn('sparse-checkout-cone-mode: false', workflow)

    def test_only_explicit_workflow_changes_trigger_pr_downloads(self):
        header = WORKFLOW.read_text().split('permissions:', 1)[0]
        self.assertIn('  workflow_dispatch:', header)
        self.assertIn('  pull_request:', header)
        self.assertEqual(re.findall(r"^      - '([^']+)'$", header, re.M),
                         ['.github/workflows/qualify-models65.yml'])
        self.assertNotRegex(header, r'pull_request_target|schedule:|push:|inputs:')

    def test_production_mutations_and_secrets_are_absent(self):
        workflow = WORKFLOW.read_text()
        self.assertNotIn('secrets.', workflow)
        self.assertNotIn('environment:', workflow)
        self.assertNotIn('catalog-signing', workflow)
        self.assertNotRegex(workflow, r'gh release (create|upload|edit)|gh workflow run|adopt-set|publish-release|pkeyutl -sign')
        self.assertIn('python3 scripts/refresh-cohort65-v1.py', workflow)
        self.assertIn('df -B1 "$RUNNER_TEMP" "$GITHUB_WORKSPACE"', workflow)
        helper = (ROOT / 'scripts/refresh-cohort65-v1.py').read_text()
        self.assertNotRegex(helper, r'gh release (create|upload|edit)|pkeyutl[^\n]*-sign')
        self.assertIn('FREE_FLOOR = 8 << 30', helper)

    def test_artifact_upload_is_an_explicit_compact_allowlist(self):
        workflow = WORKFLOW.read_text()
        paths = workflow.split('          path: |\n', 1)[1].splitlines()
        paths = [line.strip() for line in paths if line.strip()]
        self.assertEqual(len(paths), 9)
        for path in paths:
            self.assertTrue(path.startswith('${{ runner.temp }}/models65-unsigned/'))
            self.assertRegex(path, r'\.(json|pub)$')
            self.assertNotIn('*', path)
            self.assertNotIn('.zip', path)
        self.assertIn('qualification.json', paths[-1])

    def test_all_local_python_and_bash_blocks_parse(self):
        for block in run_blocks():
            subprocess.run(['bash', '-n'], input=block, text=True, capture_output=True, check=True)
        for script in python_blocks():
            compile(script, str(WORKFLOW), 'exec')


class QualificationInputsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = json.loads((ROOT / 'keys/supported-models-65.json').read_text())
        self.write('keys/supported-models-65.json', self.target)
        self.write('producer/macOS/scripts/supported-models.json', self.target)
        for path in ['keys/catalog.ed25519.pub', 'producer/macOS/Release/trust-root.ed25519.pub']:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'k' * 32)
        self.image = {'id': 123, 'conclusion': 'success', 'head_branch': 'main',
                      'head_sha': 'a' * 40, 'path': '.github/workflows/images.yml',
                      'head_repository': {'full_name': 'aurora-silicon/fedora-aurora'}}
        self.write('site/installer/Profiles/fedora-kde/image-build.json', self.image)
        self.delivery = {'installation_profile': 'fedora-kde', 'private_neo_inputs': False,
                         'native_neo_reset_qa': False}
        self.write('site/installer/Profiles/fedora-kde/delivery.json', self.delivery)
        self.environment = dict(os.environ, PROFILE='fedora-kde',
                                SOURCE_REPOSITORY='aurora-silicon/fedora-aurora',
                                GITHUB_OUTPUT=str(self.root / 'github-output'),
                                OUTPUT_DIRECTORY=str(self.root / 'output'),
                                PRODUCER_REVISION=PRODUCER)

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def select(self):
        return subprocess.run([sys.executable, '-c', python_blocks()[0]], cwd=self.root,
                              env=self.environment, text=True, capture_output=True)

    def test_committed_selection_passes_before_download(self):
        result = self.select()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'github-output').read_text(), 'image-run=123\n')
        self.assertFalse((self.root / 'output').exists())

    def test_different_producer_cohort_is_refused_before_download(self):
        producer = dict(self.target, supported=self.target['supported'][:-1])
        self.write('producer/macOS/scripts/supported-models.json', producer)
        result = self.select()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('same 65 models', result.stderr)
        self.assertFalse((self.root / 'github-output').exists())

    def test_private_and_wrong_source_image_records_are_refused(self):
        self.delivery['private_neo_inputs'] = True
        self.write('site/installer/Profiles/fedora-kde/delivery.json', self.delivery)
        result = self.select()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not a public profile', result.stderr)
        self.delivery['private_neo_inputs'] = False
        self.write('site/installer/Profiles/fedora-kde/delivery.json', self.delivery)
        self.image['head_branch'] = 'unmerged'
        self.write('site/installer/Profiles/fedora-kde/image-build.json', self.image)
        result = self.select()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('original image record differs', result.stderr)

    def proof(self):
        # The proof block reads Git identities; fake only those read-only calls.
        prefix = ("import subprocess\n"
                  "subprocess.check_output = lambda command, **kw: "
                  "'" + PRODUCER + "\\n' if '-C' in command else '" + 'b' * 40 + "\\n'\n")
        return subprocess.run([sys.executable, '-c', prefix + python_blocks()[1]], cwd=self.root,
                              env=self.environment, text=True, capture_output=True)

    def prepare_proof(self):
        names = ['catalog.json', 'delivery.json', 'metadata-refresh.json', 'source-delivery.json',
                 'image-build.json', 'release.json', 'trust-root.ed25519.pub', 'installer-data-fedora-kde.json']
        for name in names:
            self.write('output/' + name, {})
        self.write('output/image-build.json', self.image)
        self.write('output/delivery.json', self.delivery)
        self.write('output/metadata-refresh.json', {'kind': 'metadata-only-models65',
                   'payload_unchanged': True, 'payload_sha256': 'sha256:' + 'c' * 64,
                   'board_members': {f'esp/m1n1/{prefix}{board}.bin': {}
                       for board in ('j714c', 'j714s', 'j716c', 'j716s', 'j775c', 'j775d', 'j873s')
                       for prefix in ('boot-', 'boot-ctxr-')}})

    def test_proof_binds_source_and_all_fourteen_members(self):
        self.prepare_proof()
        result = self.proof()
        self.assertEqual(result.returncode, 0, result.stderr)
        proof = json.loads((self.root / 'output/qualification.json').read_text())
        self.assertTrue(proof['artifacts_unsigned'])
        self.assertEqual(proof['verified_boot_members'], 14)
        self.assertEqual(proof['producer_revision'], PRODUCER)
        self.assertEqual(proof['source_image_run'], self.image['id'])
        self.assertEqual(len(proof['artifacts']), 8)

    def test_extra_archives_and_changed_image_cannot_be_uploaded(self):
        self.prepare_proof()
        archive = self.root / 'output/payload.zip'
        archive.write_bytes(b'do not upload')
        result = self.proof()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unexpected files', result.stderr)
        archive.unlink()
        self.write('output/image-build.json', dict(self.image, head_sha='d' * 40))
        result = self.proof()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('differs from the committed selection', result.stderr)
        self.assertFalse((self.root / 'output/qualification.json').exists())


if __name__ == '__main__':
    unittest.main()
