import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.finalize_challenge import finalize
from traffic_agent.challenge import KIND
from traffic_agent.data import save_json


class ChallengeFinalizeTests(unittest.TestCase):
    def test_failed_or_running_job_cannot_publish(self):
        for status, exception in (('failed', RuntimeError), ('running', TimeoutError)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                job=Path(temp)
                save_json(job/'state.json',dict(kind=KIND,status=status))
                with patch('scripts.finalize_challenge.verify') as audit, patch('scripts.finalize_challenge.export') as export:
                    with self.assertRaises(exception):
                        finalize(job,job/'output',0)
                    audit.assert_not_called()
                    export.assert_not_called()

    def test_incomplete_final_report_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            job=Path(temp)
            save_json(job/'state.json',dict(kind=KIND,status='completed'))
            save_json(job/'selection.json',{})
            (job/'report.md').write_text('partial',encoding='utf-8')
            with patch('scripts.finalize_challenge.render_report',return_value='complete'), patch('scripts.finalize_challenge.verify') as audit:
                with self.assertRaises(TimeoutError):
                    finalize(job,job/'output',0)
                audit.assert_not_called()

    def test_failed_raw_audit_never_seals_or_exports(self):
        with tempfile.TemporaryDirectory() as temp:
            job=Path(temp)
            save_json(job/'state.json',dict(kind=KIND,status='completed'))
            save_json(job/'selection.json',{})
            (job/'report.md').write_text('complete',encoding='utf-8')
            with patch('scripts.finalize_challenge.render_report',return_value='complete'), \
                 patch('scripts.finalize_challenge.verify',side_effect=ValueError('tampered')), \
                 patch('scripts.finalize_challenge.write_archive') as seal, patch('scripts.finalize_challenge.export') as export:
                with self.assertRaises(ValueError):
                    finalize(job,job/'output',0)
                seal.assert_not_called()
                export.assert_not_called()


if __name__ == '__main__':
    unittest.main()
