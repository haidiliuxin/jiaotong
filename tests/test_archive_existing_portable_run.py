"""Publication must not turn unfinished or unrelated jobs into passed evidence."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.archive_existing_portable_run import audit
from traffic_agent.portable import MANIFEST, digest


class ExistingPortableAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.evidence = self.root/'evidence'
        self.environment = self.root/'environment.json'
        self.job = self.root/'智能体实验/jobs/example'
        self.job.mkdir(parents=True)
        (self.root/MANIFEST).write_text('{}',encoding='utf-8')
        self.save(self.environment,dict(status='passed',extracted_to=str(self.root),
                  checks=dict(final_integrity=dict(manifest_sha256=digest(self.root/MANIFEST)))))

    def save(self,path,value):
        path.write_text(json.dumps(value),encoding='utf-8')

    def rejected(self,pattern,job_id='example'):
        with patch('scripts.archive_existing_portable_run.subprocess.run') as invoke:
            with self.assertRaisesRegex(ValueError,pattern):
                audit(self.root,job_id,self.environment,self.evidence)
            invoke.assert_not_called()
        self.assertFalse(self.evidence.exists())

    def test_external_job_path_is_rejected_before_reading(self):
        self.rejected('jobs目录','../outside')

    def test_environment_record_cannot_be_reused_for_different_content(self):
        (self.root/MANIFEST).write_text('{"changed":true}',encoding='utf-8')
        self.rejected('清单不同')

    def test_running_job_cannot_be_sealed_or_marked_passed(self):
        self.save(self.job/'state.json',dict(status='running',runs=[]))
        self.rejected('尚未完成')

    def test_already_sealed_job_is_not_overwritten(self):
        self.save(self.job/'state.json',dict(status='completed',runs=[dict(execution=dict(modules=[{}]*7))]*4,
                                           task=dict(repeat_baseline=True)))
        archive=self.job/'archive_manifest.json'
        archive.write_bytes(b'preserved archive')
        self.rejected('已封存')
        self.assertEqual(archive.read_bytes(),b'preserved archive')


if __name__ == '__main__':
    unittest.main()
