"""Reject post-hoc, mismatched and incomplete extension publication before sealing."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.archive_existing_portable_run import extension_requirements
from traffic_agent.portable import MANIFEST, digest


class PortableExtensionAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.save(self.root/MANIFEST, {'code_commit':'fixed-code'})
        task = dict(scenario='oneway',demand_factors=[0.9,1.0,1.1],repeat_baseline=True,
                    max_runs=13,recommendation_version='tolerance-v2')
        self.task_path = self.root/'智能体实验/acceptance_requests/task.json'
        self.save(self.task_path,task)
        self.state = dict(started='2026-09-22T01:00:00+00:00',task=task,
                          runs=[dict(hard_access_evidence=dict(passed=True,native_calls=20)) for _ in range(13)])
        self.protocol = dict(kind='portable_extension_acceptance_v1',runtime_root=str(self.root),
                             runtime_manifest_sha256=digest(self.root/MANIFEST),runtime_code='fixed-code',
                             registered_at='2026-09-22T00:59:00+00:00',tasks=[dict(name='oneway',task=dict(task),
                             runtime_task_file=str(self.task_path),task_sha256=digest(self.task_path),
                             expected_scenarios=13,expected_top_level_calls=91,expected_nested_native_calls=260)])
        self.path = self.root/'protocol.json'

    def save(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value),encoding='utf-8')

    def check(self):
        self.save(self.path,self.protocol)
        return extension_requirements(self.root,self.state,self.path,'oneway')

    def test_valid_frozen_protocol_binds_hash_and_native_count(self):
        value=self.check()
        self.assertEqual(value['observed_nested_native_calls'],260)
        self.assertEqual(value['protocol_sha256'],digest(self.path))

    def test_late_protocol_rejected(self):
        self.protocol['registered_at']=self.state['started']
        with self.assertRaisesRegex(ValueError,'早于'):self.check()

    def test_changed_actual_task_rejected(self):
        self.state['task']['max_runs']=12
        with self.assertRaisesRegex(ValueError,'实际任务'):self.check()

    def test_edited_frozen_file_rejected(self):
        self.task_path.write_text('{}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'冻结任务'):self.check()

    def test_incomplete_native_calls_rejected(self):
        self.state['runs'][-1]['hard_access_evidence']['native_calls']=19
        with self.assertRaisesRegex(ValueError,'求解次数'):self.check()

    def test_unverified_native_evidence_rejected(self):
        self.state['runs'][-1]['hard_access_evidence']['passed']=False
        with self.assertRaisesRegex(ValueError,'证据不完整'):self.check()


if __name__=='__main__':unittest.main()
