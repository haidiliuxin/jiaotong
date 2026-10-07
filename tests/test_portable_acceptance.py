"""Pipeline refusal tests use tiny fixtures and never execute a native model."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.accept_portable_runtime import accept
from traffic_agent.data import save_json
from traffic_agent.portable import MANIFEST, digest


class PortableAcceptanceTests(unittest.TestCase):
    def fixture(self,folder):
        root=folder/'bundle'; root.mkdir()
        save_json(root/MANIFEST,dict(code_commit='fixture'))
        env=folder/'environment.json'
        save_json(env,dict(status='passed',extracted_to=str(root),
                           checks=dict(final_integrity=dict(manifest_sha256=digest(root/MANIFEST)))))
        previous=folder/'previous'
        save_json(previous/'state.json',dict(status='running',kind='prospective_backtracking_challenge'))
        return root,env,previous,folder/'evidence'

    def run_failure(self,change,error,status):
        with tempfile.TemporaryDirectory() as temp:
            args=self.fixture(Path(temp))
            change(*args)
            with patch('scripts.accept_portable_runtime.verify_bundle',return_value={'passed':True}), \
                 patch('scripts.accept_portable_runtime.subprocess.Popen') as launch:
                with self.assertRaises(error):
                    accept(*args,wait_seconds=0)
                launch.assert_not_called()
            record=json.loads((args[3]/'acceptance.json').read_text(encoding='utf-8'))
            self.assertEqual(record['status'],status)
            self.assertEqual(record['new_model_calls'],0)
            self.assertEqual(record['commands'],[])

    def test_failed_environment_does_not_run(self):
        self.run_failure(lambda root,env,*_:save_json(env,dict(status='failed')),ValueError,'failed')

    def test_changed_manifest_does_not_run(self):
        self.run_failure(lambda root,*_:save_json(root/MANIFEST,dict(code_commit='changed')),ValueError,'failed')

    def test_wait_timeout_preserves_predecessor_and_does_not_run(self):
        self.run_failure(lambda *args:None,TimeoutError,'attention_required')

    def test_failed_predecessor_does_not_start_new_model(self):
        def fail(root,env,previous,evidence):
            save_json(previous/'state.json',dict(status='failed',kind='prospective_backtracking_challenge'))
        self.run_failure(fail,RuntimeError,'failed')

    def test_completed_but_unsealed_predecessor_is_insufficient(self):
        def complete(root,env,previous,evidence):
            save_json(previous/'state.json',dict(status='completed',kind='prospective_backtracking_challenge'))
        self.run_failure(complete,TimeoutError,'attention_required')

    def test_existing_evidence_refuses_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            args=self.fixture(Path(temp))
            args[3].mkdir()
            marker=args[3]/'keep.txt'; marker.write_text('keep',encoding='utf-8')
            with patch('scripts.accept_portable_runtime.subprocess.Popen') as launch:
                with self.assertRaises(FileExistsError):
                    accept(*args,wait_seconds=0)
                launch.assert_not_called()
            self.assertEqual(marker.read_text(encoding='utf-8'),'keep')


if __name__=='__main__':
    unittest.main()
