import json
import tempfile
import unittest
import zipfile
import warnings
from pathlib import Path

from traffic_agent.delivery import REQUIRED, digest, read_bound_study, safe_name, seal_package, test_count, verify_package


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def study(self, **updates):
        state = dict(status='completed', kind='prospective_region_study', repeatability={'exact_match': True})
        state.update(updates)
        verification = {'passed': True}
        for name, value in [('state', state), ('protocol', {'version': 1}), ('selection', {'regions': []})]:
            data = json.dumps(value).encode()
            (self.root/f'{name}.json').write_bytes(data)
            verification[f'{name}_sha256'] = digest(data)
        (self.root/'verification.json').write_text(json.dumps(verification), encoding='utf-8')

    def package(self):
        for name in REQUIRED:
            (self.root/name).write_bytes(b'content')
        seal_package(self.root, {'evidence_commit': 'fixture'})

    def test_stale_verification_is_rejected_even_with_passed_flag(self):
        self.study()
        read_bound_study(self.root)
        for name in ('state', 'protocol', 'selection'):
            path = self.root/f'{name}.json'
            original = path.read_bytes()
            path.write_bytes(original+b' ')
            with self.assertRaisesRegex(ValueError, '过期'):
                read_bound_study(self.root)
            path.write_bytes(original)

    def test_truthy_nonboolean_verification_is_rejected(self):
        self.study()
        p = self.root/'verification.json'
        v = json.loads(p.read_text())
        v['passed'] = 'false'
        p.write_text(json.dumps(v))
        with self.assertRaises(ValueError):
            read_bound_study(self.root)

    def test_incomplete_or_unrepeated_study_is_not_publishable(self):
        for update in ({'status': 'running'}, {'repeatability': {'exact_match': False}}):
            self.study(**update)
            with self.assertRaises(ValueError):
                read_bound_study(self.root)

    def test_failed_or_multiple_test_runs_do_not_supply_success_count(self):
        path = self.root/'tests.log'
        good = 'test ... ok\nRan 12 tests in 1.0s\n\nOK\n'
        path.write_text(good)
        self.assertEqual(test_count(path), 12)
        for bad in (good+'FAILED (failures=1)', good+good, 'Ran 12 tests in 1.0s\nFAIL: x\nOK'):
            path.write_text(bad)
            with self.assertRaises(ValueError):
                test_count(path)

    def test_same_size_package_tampering_is_detected(self):
        self.package()
        self.assertTrue(verify_package(self.root)['passed'])
        (self.root/'研究报告.pdf').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '内容摘要不符'):
            verify_package(self.root)

    def test_missing_and_extra_package_members_are_rejected(self):
        self.package()
        (self.root/'extra.txt').write_text('x')
        with self.assertRaises(ValueError):
            verify_package(self.root)
        (self.root/'extra.txt').unlink()
        (self.root/'研究报告.pdf').unlink()
        with self.assertRaises(ValueError):
            verify_package(self.root)

    def test_archive_names_cannot_escape_or_duplicate(self):
        for name in ('../x', '/x', 'C:/x', 'a\\b', 'a/../b', 'a//b', './x'):
            with self.assertRaises(ValueError):
                safe_name(name)
        self.package()
        with tempfile.TemporaryDirectory() as other:
            target = Path(other)/'reading.zip'
            with zipfile.ZipFile(target, 'w') as z:
                for p in self.root.iterdir():
                    z.write(p, p.name)
            self.assertTrue(verify_package(target)['passed'])
            with zipfile.ZipFile(target, 'a') as z:
                z.writestr('../outside', b'x')
            with self.assertRaises(ValueError):
                verify_package(target)
            duplicate = Path(other)/'duplicate.zip'
            with zipfile.ZipFile(duplicate, 'w') as z, warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                z.writestr('x', b'first')
                z.writestr('x', b'second')
            with self.assertRaisesRegex(ValueError, '重复'):
                verify_package(duplicate)

    def test_lfs_pointer_is_not_a_delivered_artifact(self):
        for name in REQUIRED:
            (self.root/name).write_bytes(b'content')
        (self.root/'研究报告.pdf').write_bytes(b'version https://git-lfs.github.com/spec/v1\n')
        with self.assertRaisesRegex(ValueError, 'LFS'):
            seal_package(self.root, {})
