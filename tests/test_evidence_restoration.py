import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from check_deduplicated_job import check_agent_audit


class RestorationTests(unittest.TestCase):
    def test_model_case_without_repeat_is_not_misreported_as_rule_search_or_repeat(self):
        audit=dict(passed=True,portable_archive_verified=True,completed_scenarios=3,module_executions=21,
                   repeat_arrays_exact=None,model_planning_audit=dict(passed=True,decisions=3))
        package=dict(completed_scenarios=3,module_executions=21)
        state=dict(task=dict(repeat_baseline=False),planner_trace=[{}])
        kind,check=check_agent_audit(audit,package,state)
        self.assertEqual(kind,'model_observation_audit');self.assertEqual(check['decisions'],3)
        self.assertIsNone(audit['repeat_arrays_exact'])
        state['task']['repeat_baseline']=True
        with self.assertRaisesRegex(ValueError,'复跑'):check_agent_audit(audit,package,state)
        state['task']['repeat_baseline']=False;del audit['model_planning_audit']
        with self.assertRaisesRegex(ValueError,'模型观测'):check_agent_audit(audit,package,state)

    def test_failed_repeat_or_missing_rule_audit_cannot_be_silently_skipped(self):
        package=dict(completed_scenarios=3,module_executions=21)
        audit=dict(passed=True,portable_archive_verified=True,**package,repeat_arrays_exact={'array':False})
        state=dict(task=dict(repeat_baseline=True,search_mode='robust'))
        with self.assertRaisesRegex(ValueError,'复跑'):check_agent_audit(audit,package,state)
        audit['repeat_arrays_exact']={'array':True}
        with self.assertRaisesRegex(ValueError,'规则搜索'):check_agent_audit(audit,package,state)


if __name__=='__main__':unittest.main()
