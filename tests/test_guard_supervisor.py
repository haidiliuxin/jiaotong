"""Bounded recovery classification; synthetic logs never claim a model result."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.supervise_guard_challenge import retryable_native_failure
from traffic_agent.guard_challenge import KIND


class NativeFailureClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.job = Path(self.temp.name)/'job'
        self.logs = self.job/'logs/case_002'
        self.logs.mkdir(parents=True)
        self.state = dict(status='failed', kind=KIND,
                          error='RuntimeError: M6AssignVehicle.exe失败，退出码1')
        self.manifest = dict(status='failed', error=self.state['error'],
                             modules=[dict(module='M6AssignVehicle.exe', status='failed')])
        self.stdout = ('[error]在打开以下数据文件或执行以下程序时出错\n'
                       'cache/Rwm_car.dat\n').encode('gb18030')
        self.write()

    def write(self):
        (self.job/'state.json').write_text(json.dumps(self.state, ensure_ascii=False), encoding='utf-8')
        (self.logs/'run.json').write_text(json.dumps(self.manifest, ensure_ascii=False), encoding='utf-8')
        (self.logs/'M6AssignVehicle.exe.stdout.bin').write_bytes(self.stdout)

    def test_only_exact_native_file_open_failure_is_retryable(self):
        evidence = retryable_native_failure(self.job)
        self.assertEqual(evidence['physical'], 'case_002')
        self.assertEqual(evidence['log'], 'logs/case_002/run.json')
        self.assertEqual(len(evidence['stdout_sha256']), 64)

    def test_policy_failure_or_other_file_never_restarts_job(self):
        for change in ('completed', 'other_module', 'other_file', 'no_native_error'):
            with self.subTest(change=change):
                self.setUp()
                if change == 'completed':
                    self.state['status'] = 'completed'
                elif change == 'other_module':
                    self.manifest['modules'][-1]['module'] = 'M6TrafficLoadLinkVeh.exe'
                elif change == 'other_file':
                    self.stdout = self.stdout.replace(b'Rwm_car.dat', b'other.dat')
                else:
                    self.state['error'] = 'ValueError: policy rejected'
                self.write()
                self.assertIsNone(retryable_native_failure(self.job))


if __name__ == '__main__':
    unittest.main()
