import copy
import unittest

from traffic_agent.knowledge_retrieval import retrieve,verify
from traffic_agent.conclusion_review import assess_entry,contract,VERSION
from test_conclusion_review import entry


class AccuracyFollowups(unittest.TestCase):
    def budget_entry(self,text,version=VERSION):
        value=entry(text)
        value['input'].update(model_budget=dict(remaining=3))
        value['input']['context']['measured_conclusion']=dict(version=version)
        value['reviewed_cost_refs']=['j:policy']
        return value

    def test_native_budget_is_not_model_budget_but_joint_exhaustion_is_checked(self):
        for text in (
            '3次原生仿真额度已用尽（remaining=0），按find_one收尾复核。',
            '原生场景预算已用尽（同组3/3）；模型请求额度尚有剩余。',
            '仿真预算已用尽，模型预算未用尽。'):
            with self.subTest(text=text):
                self.assertNotIn('model_budget_not_exhausted',{i['code'] for i in assess_entry(self.budget_entry(text))['issues']})
        for text in ('模型预算已用尽','原生场景预算已用尽；模型额度已用尽','模型预算与仿真次数已用尽'):
            self.assertIn('model_budget_not_exhausted',{i['code'] for i in assess_entry(self.budget_entry(text))['issues']})
        old=self.budget_entry('原生场景预算已用尽','measured-conclusion-v14')
        self.assertIn('model_budget_not_exhausted',{i['code'] for i in assess_entry(old)['issues']})

    def test_retrieval_handles_negation_transition_and_metric_synonyms(self):
        for text in ('只做停车治理，不做单向和禁转','停车治理，不安排单向也不安排禁转'):
            bundle=retrieve(text,'initial')
            self.assertFalse(any(h['id'].startswith('oneway.') for h in bundle['hits']))
            self.assertIn('parking.categories',[h['id'] for h in bundle['hits']])
        for text in ('不做停车，改为单向组织','不是不做单向，而是先检查公交保护'):
            self.assertTrue(any(h['id'].startswith('oneway.') for h in retrieve(text,'initial')['hits']))
        self.assertIn('metrics.boundary',[h['id'] for h in retrieve('停车治理后平均车速能用算术平均吗','initial')['hits']])

    def test_old_retrieval_ranking_replays_under_its_frozen_version(self):
        text='只做停车治理，不做单向和禁转'
        old=retrieve(text,'initial',version='curated-knowledge-rag-v1')
        self.assertTrue(any(h['id'].startswith('oneway.') for h in old['hits']))
        self.assertEqual(verify(old,text,'initial'),'verified')
        new=retrieve(text,'initial')
        self.assertNotEqual(old['version'],new['version'])

    def payload(self):
        change=lambda a,b,p:dict(from_node=a,to_node=b,parking=p)
        return dict(phase='parking',context=dict(research_depth=dict(intent='find_one'),
            completion_guard=dict(strength_comparison_required=False)),observations=[
                dict(ref='j:failed',changes=[change(1,2,2),change(3,4,2)],delta={},eligible=False,violations=['邻区过饱和'])],
            allowed_actions=dict(subset=dict(changes=[change(1,2,1)]),
                strength=dict(changes=[change(1,2,1),change(3,4,1)]),other=dict(changes=[change(5,6,2)])))

    def test_feasibility_feedback_offers_unmeasured_subsets_without_certifying_them(self):
        from traffic_agent.feasibility_search import build
        p=self.payload();before=copy.deepcopy(p);value=build(p)
        self.assertEqual(value['reduced_scope_actions'],['subset'])
        self.assertEqual(value['different_scope_actions'],['other'])
        self.assertEqual(value['same_scope_actions'],['strength'])
        self.assertTrue(value['unmeasured']);self.assertEqual(value['failed_ref'],'j:failed')
        self.assertEqual(p,before)

    def test_feasibility_feedback_does_not_override_required_comparison_or_success(self):
        from traffic_agent.feasibility_search import build
        for mode in ('compare_candidates','demand_robustness'):
            p=self.payload();p['context']['research_depth']['intent']=mode
            self.assertIsNone(build(p))
        p=self.payload();p['context']['completion_guard']['strength_comparison_required']=True
        self.assertIsNone(build(p))
        p=self.payload();p['observations'][0]['eligible']=True
        self.assertIsNone(build(p))

    def test_guidance_is_sent_to_model_and_tampered_menu_evidence_is_rejected(self):
        import json
        from unittest.mock import Mock
        from traffic_agent.feedback_planner import PlanningSession,verify_review_permissions
        from traffic_agent.knowledge_retrieval import VERSION as RAG_VERSION
        from test_feedback_planner import reply
        p=self.payload();seen=[]
        def choose(instructions,inputs,tools,choice):
            payload=json.loads(inputs[0]['content']);seen.append(payload)
            self.assertIn('feasibility_search',payload['context'])
            self.assertIn('不默认同范围更强治理',instructions)
            return reply('subset',['j:failed'],payload)
        session=PlanningSession(Mock(request=Mock(side_effect=choose)),'找到停车可用方案')
        session.programme_plan=dict(knowledge_retrieval_protocol=RAG_VERSION)
        result=session.decide('parking','j',p['observations'],p['allowed_actions'],p['context'])
        self.assertEqual(result['status'],'accepted')
        verify_review_permissions(result)
        damaged=copy.deepcopy(result)
        damaged['input']['context']['feasibility_search']['reduced_scope_actions']=['invented']
        with self.assertRaisesRegex(ValueError,'失败反馈搜索依据'):verify_review_permissions(damaged)
