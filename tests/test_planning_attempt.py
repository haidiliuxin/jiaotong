import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from traffic_agent.planning_attempt import PlanningAttempt, listing
from traffic_agent.data import save_json
from traffic_agent.llm_client import ModelError


class PreviewAccountingTests(unittest.TestCase):
    def test_failed_preview_preserves_the_actual_retrieved_sources(self):
        from traffic_agent.knowledge_retrieval import retrieve
        with tempfile.TemporaryDirectory() as tmp:
            bundle=retrieve('停车影响类别','initial')
            client=Mock(request=Mock(return_value=dict(metadata=dict(tokens=10),calls=[])))
            attempt=PlanningAttempt(client,Path(tmp),'停车影响类别')
            attempt.request('instructions',[dict(role='user',content=json.dumps(
                dict(request='停车影响类别',knowledge_retrieval=bundle)))],[])
            attempt.finish('validation_failed',ValueError('无效提议'))
            self.assertEqual(json.loads(attempt.path.read_text('utf-8'))['knowledge_retrieval'],bundle)
            shown=listing(Path(tmp))[0]['knowledge_retrieval']
            self.assertEqual([h['id'] for h in shown['hits']],[h['id'] for h in bundle['hits']])

    def test_success_metadata_survives_later_validation_error_without_raw_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            client=Mock(request=Mock(return_value=dict(metadata=dict(tokens=123,response_id='x',authorization='never-save'),
                                                       output='PRIVATE_RAW_OUTPUT',calls=[])))
            a=PlanningAttempt(client,Path(tmp),'测试规划')
            a.request('not recorded',[],[])
            a.finish('validation_failed',ValueError('未通过任务约束'))
            data=json.loads(a.path.read_text('utf-8'))
            self.assertEqual(data['usage'],dict(tokens=123,response_id='x'))
            self.assertEqual(data['status'],'validation_failed')
            self.assertFalse(data['native_started'])
            self.assertNotIn('PRIVATE_RAW_OUTPUT',a.path.read_text('utf-8'))

    def test_model_failure_is_not_overwritten_and_does_not_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            client=Mock(request=Mock(side_effect=ModelError('模型服务 HTTP 401')))
            a=PlanningAttempt(client,Path(tmp),'规划')
            with self.assertRaises(ModelError):a.request('',[],[])
            a.finish('validation_failed',RuntimeError('unsafe error'))
            data=json.loads(a.path.read_text('utf-8'))
            self.assertEqual(data['status'],'model_failed')
            self.assertIsNone(data['usage'])
            self.assertTrue(data['requested'])
            self.assertEqual(data['error'],'模型服务 HTTP 401')
            client.request.assert_called_once()

    def test_local_preflight_rejection_has_zero_model_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=PlanningAttempt(Mock(),Path(tmp),'invalid')
            a.finish('validation_failed',ValueError('缺少预算'))
            self.assertFalse(a.record['requested'])
            a.client.request.assert_not_called()

    def test_failed_transport_parse_preserves_known_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=PlanningAttempt(Mock(request=Mock(side_effect=ModelError('截断',usage=dict(tokens=321)))),Path(tmp),'规划')
            with self.assertRaises(ModelError):a.request('',[],[])
            self.assertEqual(a.record['usage']['tokens'],321)

    def test_readonly_history_keeps_unknown_cost_and_never_exposes_extra_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            a=PlanningAttempt(Mock(),root,'LOCAL_PREFLIGHT')
            a.finish('validation_failed',ValueError('预算不足'))
            path=root/'planning_attempts/preview_remote.json'
            save_json(path,dict(id='remote',started='2099-01-01',status='model_failed',requested=True,
                                authorization='PRIVATE',response='RAW',usage=None))
            original=path.read_bytes()
            (root/'planning_attempts/preview_partial.json').write_text('{','utf-8')
            rows=listing(root,1)
            self.assertEqual(rows[0]['id'],'remote')
            self.assertIsNone(rows[0]['usage'])
            self.assertTrue(rows[0]['requested'])
            self.assertEqual(path.read_bytes(),original)
            self.assertNotIn('PRIVATE',json.dumps(rows))
            self.assertNotIn('RAW',json.dumps(rows))
            self.assertEqual(len(listing(root,limit=None)),2)

    def test_error_usage_and_returned_explanation_remain_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=PlanningAttempt(Mock(),Path(tmp),'规划')
            a.finish('validation_failed',ModelError('无效回复',usage=dict(tokens=2819,authorization='PRIVATE')))
            self.assertEqual(a.record['usage'],dict(tokens=2819))
            b=PlanningAttempt(Mock(),Path(tmp),'另一规划')
            b.finish('needs_input',explanation='需要明确关闭方向。'*100)
            self.assertEqual(len(b.record['explanation']),600)

    def test_invalid_plan_retains_only_bounded_public_proposal_for_diagnosis(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Generated synthetic marker, not an account credential.
            marker='sk_'+'x'*32
            proposed=dict(goal='比较两个区域',reasoning='使用新基准'+marker,
                experiments=[dict(study='standard',strategies=['parking'],road_name='测试路',target_ref='auto',
                    demand_profile='base',rationale='核对区域影响',execute_shell='PRIVATE_RAW_FIELD')]*2,
                authorization='PRIVATE_HEADER',reasoning_content='PRIVATE_REASONING')
            response=dict(metadata=dict(tokens=111),calls=[dict(name='compose_traffic_plan',arguments=json.dumps(proposed))],
                          output='PRIVATE_FULL_RESPONSE')
            client=Mock(request=Mock(return_value=response));root=Path(tmp)
            a=PlanningAttempt(client,root,'两个区域');a.request('',[],[])
            a.finish('validation_failed',ValueError('重复安排'))
            data=json.loads(a.path.read_text('utf-8'));draft=data['proposal']
            self.assertEqual(draft['status'],'unvalidated')
            self.assertEqual(len(draft['studies']),2)
            self.assertEqual(draft['studies'][0]['target'],'auto')
            self.assertEqual(draft['reason'],'使用新基准[REDACTED]')
            self.assertEqual(data['usage']['tokens'],111)
            self.assertEqual(listing(root)[0]['proposal'],draft)
            for forbidden in ('PRIVATE_',marker,'execute_shell','reasoning_content'):
                self.assertNotIn(forbidden,a.path.read_text('utf-8'))
            client.request.assert_called_once()

    def test_unverifiable_knowledge_does_not_hide_failed_attempt(self):
        from traffic_agent.knowledge_retrieval import retrieve
        with tempfile.TemporaryDirectory() as tmp:
            attempt=PlanningAttempt(Mock(),Path(tmp),'停车治理')
            attempt.record['knowledge_retrieval']=retrieve('另一个请求','initial')
            attempt.finish('validation_failed',ValueError('规划失败'))
            rows=listing(Path(tmp))
            self.assertEqual(rows[0]['id'],attempt.record['id'])
            self.assertEqual(rows[0]['status'],'validation_failed')
            self.assertNotIn('knowledge_retrieval',rows[0])

    def test_wrong_tool_or_malformed_reply_cannot_create_proposal(self):
        from traffic_agent.planning_attempt import proposal_from_reply,safe_proposal
        for value in ({},dict(calls=[]),dict(calls=[dict(name='another_tool',arguments='{}')]),
                      dict(calls=[dict(name='compose_traffic_plan',arguments='{')])):
            self.assertIsNone(proposal_from_reply(value))
        draft=safe_proposal(dict(goal='g'*2000,experiments=[dict(rationale='r'*2000)]*20))
        self.assertEqual(len(draft['goal']),400)
        self.assertEqual(len(draft['studies']),5)
        self.assertEqual(len(draft['studies'][0]['rationale']),400)


if __name__=='__main__':unittest.main()
