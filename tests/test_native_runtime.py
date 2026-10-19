import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/verify-native-runtime.py'
spec = importlib.util.spec_from_file_location('native_runtime', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class NativeRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / 'installer'
        engine = self.checkout / 'macOS/Engine'
        recovery = self.checkout / 'macOS/Recovery'
        engine.mkdir(parents=True)
        recovery.mkdir()
        (recovery / 'source.swift').write_text('qualified recovery source')
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        self.engine = 'installer-v0.9.2-aurora.2.tar.gz'
        self.recovery = 'aurora-recovery-template-20261018.tar.gz'
        self.manifest = {'schema': 1, 'assets': {}}
        for name in (self.engine, self.recovery):
            data = name.encode()
            (self.runtime / name).write_bytes(data)
            self.manifest['assets'][name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        record = self.manifest['assets'][self.engine]
        (engine / 'source-lock.json').write_text(json.dumps({'validation_artifact': {
            'filename': self.engine, 'size_bytes': record['bytes'], 'sha256': record['sha256']}}))
        self.git('init', '-q')
        self.commit()
        self.manifest.update(engine_tree=module.tree(self.checkout, 'Engine'),
                             recovery_tree=module.tree(self.checkout, 'Recovery'))
        self.save()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.checkout), *args], stderr=subprocess.PIPE)

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '-qm', 'fixture')

    def save(self):
        (self.runtime / 'runtime-manifest.json').write_text(json.dumps(self.manifest))

    def test_matching_runtime_matches_both_source_trees_and_exact_asset_bytes(self):
        result = module.verify(self.runtime, self.checkout)
        self.assertEqual(result['assets'], self.manifest['assets'])
        self.assertEqual(result['source_and_asset_verification'], 'passed')

    def test_changed_recovery_source_requires_a_new_runtime(self):
        (self.checkout / 'macOS/Recovery/source.swift').write_text('new unqualified recovery')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'Recovery sources'):
            module.verify(self.runtime, self.checkout)

    def test_changed_asset_is_rejected(self):
        (self.runtime / self.recovery).write_bytes(b'X' * self.manifest['assets'][self.recovery]['bytes'])
        with self.assertRaisesRegex(ValueError, 'digest differs'):
            module.verify(self.runtime, self.checkout)

    def test_symlink_asset_is_rejected(self):
        path = self.runtime / self.recovery
        target = self.root / 'outside-runtime'
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'invalid runtime asset'):
            module.verify(self.runtime, self.checkout)

    def test_a_signed_manifest_cannot_override_the_engine_source_lock(self):
        path = self.runtime / self.engine
        path.write_bytes(b'other engine')
        self.manifest['assets'][self.engine] = {'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        self.save()
        with self.assertRaisesRegex(ValueError, 'source lock'):
            module.verify(self.runtime, self.checkout)


if __name__ == '__main__':
    unittest.main()
