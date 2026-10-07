"""Failure, recovery and evidence boundaries; no live model or native calls."""
import copy
import json
import unittest

from traffic_agent.knowledge_retrieval import retrieve, requested_topics, VERSION, validate_citations
from traffic_agent.initial_planner import evidence, verify_initial
from traffic_agent.feedback_planner import digest
from traffic_agent.runner import now
from traffic_agent.llm_client import ModelError
import test_initial_planner as initial_fixture
from test_natural_plan import model_reply, experiment


class ShapeRecoveryTests(unittest.TestCase):
    setup_preview=initial_fixture.InitialPlannerTests.setup_preview
    preview=initial_fixture.InitialPlannerTests.preview
    records=initial_fixture.InitialPlannerTests.records
    def test_complete_tool_history_invalid_json_gets_only_one_format_correction(self):
        invalid=model_reply([experiment(['parking'])]);invalid['calls'][0]['arguments']='{"goal":'
        self.setup_preview([invalid,model_reply([experiment(['parking'])])],tokens=[80,120])
        result=self.preview()
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['model_budget']['used'],2)
        self.assertEqual(result['model_budget']['known_tokens'],200)
        self.assertEqual(result['initial_planning_attempts'][0]['validation_code'],'invalid_plan_shape')
        bodies=[json.loads(c.args[0].data) for c in self.opener.open.call_args_list]
        feedback=json.loads(bodies[1]['messages'][-1]['content'])
        self.assertEqual(feedback['code'],'invalid_plan_shape')
        self.assertEqual(bodies[0]['tools'],bodies[1]['tools'])
        self.assertEqual(bodies[0]['messages'][1],bodies[1]['messages'][1])
        self.assertEqual(verify_initial(result,dict(created=now(),initial_planning_digest=digest(evidence(result)))),2)
        self.native.assert_not_called()
        bad=copy.deepcopy(result);bad['initial_review_protocol']='initial-plan-review-v6'
        with self.assertRaises(ValueError):verify_initial(bad,dict(created=now(),initial_planning_digest=digest(evidence(bad))))

    def test_repeated_format_error_stops_at_two_with_no_execution(self):
        invalid=model_reply([experiment(['parking'])]);invalid['calls'][0]['arguments']='{'
        self.setup_preview([invalid,invalid,model_reply([experiment(['parking'])])])
        with self.assertRaises(ModelError):self.preview()
        self.assertEqual(self.opener.open.call_count,2)
        self.assertEqual([r['status'] for r in self.records()],['validation_failed']*2)
        self.native.assert_not_called()

    def test_mixed_decision_fields_from_real_failure_are_recoverable(self):
        invalid=model_reply([experiment(['parking'])])
        value=json.loads(invalid['calls'][0]['arguments'])
        value['decision']['questions']=[]
        invalid['calls'][0]['arguments']=json.dumps(value)
        self.setup_preview([invalid,model_reply([experiment(['parking'])])])
        result=self.preview()
        self.assertEqual(result['model_budget']['used'],2)
        self.assertEqual(result['initial_planning_attempts'][0]['validation_code'],'invalid_plan_shape')
        self.native.assert_not_called()

    def test_quality_statistics_count_sessions_and_unknown_usage_without_prose(self):
        from traffic_agent.planning_attempt import quality
        invalid=model_reply([experiment(['parking'])]);invalid['calls'][0]['arguments']='{'
        self.setup_preview([invalid,model_reply([experiment(['parking'])])],tokens=[None,120])
        self.preview()
        summary=quality(self.root)
        self.assertEqual(summary['sessions'],1)
        self.assertEqual(summary['first_ready'],0)
        self.assertEqual(summary['recovered_ready'],1)
        self.assertEqual(summary['requests'],2)
        self.assertEqual(summary['known_tokens'],120)
        self.assertEqual(summary['unknown_usage'],1)
        self.assertNotIn('UNIT_TEST_ONLY',json.dumps(summary))


class ProfessionalEvidenceTests(unittest.TestCase):
    def test_negation_lists_suffix_double_negation_and_positive_reset(self):
        cases=[('不考虑单向，只研究停车治理',{'parking'}),
               ('暂不实施禁转，先做停车治理',{'parking'}),
               ('不需要施工方案，只考虑信号配时',{'signal'}),
               ('单向暂不考虑，停车需要研究',{'parking'}),
               ('无需单向但需要停车',{'parking'}),
               ('不要单向和禁转，要研究停车',{'parking'}),
               ('不是不考虑单向，但不需要施工',{'oneway'}),
               ('不考虑单向和禁转，改为公交优先',{'transit'}),
               ('停车不需要，信号配时需要',{'signal'})]
        for text,expected in cases:
            with self.subTest(text=text):self.assertEqual(requested_topics(text,VERSION),expected)
        self.assertEqual(requested_topics('不考虑单向，只研究停车治理','curated-knowledge-rag-v2'),{'oneway','parking'})

    def test_method_citation_cannot_be_used_as_measured_effect_or_reverse_metric(self):
        bundle=retrieve('停车影响类别速度汇总','parking','parking')
        validate_citations('依据【K:parking.categories】提出停车1级试验，收益待本次实测。',bundle)
        for text in ('根据【K:parking.categories】证明本次方案耗时下降20%。',
                     '可以用算术平均汇总速度【K:metrics.boundary】。'):
            with self.subTest(text=text),self.assertRaisesRegex(ValueError,'知识引用'):validate_citations(text,bundle)
        validate_citations('不能用算术平均汇总速度【K:metrics.boundary】。',bundle)

    def payload(self):
        c=lambda a,b,p:dict(from_node=a,to_node=b,parking=p)
        return dict(phase='parking',context=dict(task=dict(demand_factors=[.9,1.,1.1]),
            research_depth=dict(intent='demand_robustness'),knowledge_retrieval_protocol=VERSION),
            observations=[dict(ref='j:base_low',changes=[],demand_factor=.9),
                          dict(ref='j:base_mid',changes=[],demand_factor=1.),
                          dict(ref='j:base_high',changes=[],demand_factor=1.1),
                          dict(ref='j:p1_low',changes=[c(1,2,1),c(3,4,1)],demand_factor=.9,delta={'core':{}},eligible=True),
                          dict(ref='j:p1_high',changes=[c(1,2,1),c(3,4,1)],demand_factor=1.1,delta={'core':{}},eligible=False,violations=['核心耗时上升'])],
            allowed_actions=dict(subset=dict(changes=[c(1,2,1)]),weaker=dict(changes=[c(1,2,2),c(3,4,2)]),other=dict(changes=[c(5,6,2)])))

    def test_robust_feedback_keeps_failed_demand_despite_low_demand_pass(self):
        from traffic_agent.feasibility_search import build
        payload=self.payload();value=build(payload)
        self.assertIsNotNone(value)
        self.assertEqual(value['failed_demand_factors'],[1.1])
        self.assertEqual(value['required_demand_factors'],[.9,1.,1.1])
        self.assertEqual(value['reduced_scope_actions'],['subset'])
        self.assertFalse(value['robust_eligible'])
        self.assertTrue(value['unmeasured'])
        self.assertEqual(value['policy_grid'][0]['missing_demand_factors'],[1.])
        payload['context']['knowledge_retrieval_protocol']='curated-knowledge-rag-v2'
        self.assertIsNone(build(payload))

    def test_support_ledger_does_not_certify_partial_or_failed_grids(self):
        from traffic_agent.evidence_support import ledger, issues
        p=self.payload();facts=ledger(p['observations'],p['context'])
        self.assertEqual(facts['eligible_policy_count'],0)
        self.assertEqual(facts['policies'][0]['missing_demand_factors'],[1.])
        for text,code in [('该方案在全部需求下均有效','unsupported_robust_claim'),
                          ('这是全网最优方案','unsupported_optimality_claim'),
                          ('这是唯一可行的方案','unqualified_unique_claim')]:
            found=issues(dict(reason=text),facts)
            self.assertIn(code,{i['code'] for i in found})
        self.assertFalse(issues(dict(reason='不能证明全网最优；在已测候选中未找到跨需求合格方案。'),facts))
        p['observations'].append(dict(p['observations'][-2],ref='j:p1_mid',demand_factor=1.))
        p['observations'][-2]['eligible']=True
        passed=ledger(p['observations'],p['context'])
        self.assertEqual(passed['eligible_policy_count'],1)
        self.assertFalse(issues(dict(reason='在本次已测全部需求下均有效'),passed))

    def test_baselines_and_multiple_demands_are_required_to_support_robustness(self):
        from traffic_agent.evidence_support import ledger, issues
        p=self.payload()
        p['observations']=[p['observations'][-2]]
        p['context']['task']['demand_factors']=[.9]
        facts=ledger(p['observations'],p['context'])
        self.assertEqual(facts['eligible_policy_count'],0)
        self.assertEqual(facts['policies'][0]['missing_baseline_factors'],[.9])
        p['observations'].append(dict(ref='j:base',changes=[],demand_factor=.9))
        facts=ledger(p['observations'],p['context'])
        self.assertEqual(facts['eligible_policy_count'],1)
        self.assertTrue(issues(dict(reason='跨需求仍有效'),facts))

    def test_frozen_acceptance_fixture_passes_without_rank_tuning(self):
        from scripts.validate_professional_acceptance import evaluate
        result=evaluate(VERSION)
        self.assertEqual(result['passed'],24)
        self.assertTrue(all(c['replay']=='verified' for c in result['cases']))

    def test_support_claims_keep_negation_and_do_not_invent_a_unique_winner(self):
        from traffic_agent.evidence_support import ledger, issues
        facts=ledger(self.payload()['observations'],self.payload()['context'])
        for text in ('并非全网最优方案','没有跨需求合格方案','所有需求未通过',
                     '三个需求均eligible=false：×0.9违反area_2耗时门槛，×1.0另加邻区毛新增2.594km，×1.1违反四项含核心路段未改善，全部需求无一通过全部约束'):
            with self.subTest(text=text):self.assertFalse(issues(dict(reason=text),facts))
        self.assertTrue(issues(dict(reason='已测候选中唯一可行的方案'),facts))
        from traffic_agent.evidence_support import LEGACY_VERSION
        old=ledger(self.payload()['observations'],self.payload()['context'],version=LEGACY_VERSION)
        self.assertTrue(issues(dict(reason='全部需求无一通过全部约束'),old))

    def test_universal_adjectives_and_robustness_nouns_are_not_affirmative_claims(self):
        from traffic_agent.evidence_support import ledger, issues
        facts=ledger(self.payload()['observations'],self.payload()['context'])
        for text in ('全部需求均不合格，稳健搜索无合格候选','跨需求稳健性未获支持',
                     '跨需求稳健性是否成立仍待验证','所有需求下的效果尚未验证'):
            with self.subTest(text=text):self.assertFalse(issues(dict(reason=text),facts))
        for text in ('全部需求均合格','全部需求下都通过','跨需求仍有效'):
            with self.subTest(text=text):self.assertTrue(issues(dict(reason=text),facts))

    def test_absent_frozen_grid_is_unknown_not_a_false_robust_contradiction(self):
        from traffic_agent.evidence_support import ledger, issues
        p=self.payload();p['context'].pop('task')
        facts=ledger(p['observations'],p['context'])
        self.assertFalse(facts['coverage_known'])
        self.assertEqual(facts['eligible_policy_count'],0)
        self.assertFalse(issues(dict(reason='本组已测各需求均有效'),facts))

    def test_independent_groups_cannot_complete_each_others_demand_grid(self):
        from traffic_agent.evidence_support import ledger
        p=self.payload()
        p['observations'][-1]['ref']='other:p1_high'
        p['observations'][-1]['eligible']=True
        facts=ledger(p['observations'],p['context'])
        self.assertEqual(len(facts['policies']),2)
        self.assertEqual(facts['eligible_policy_count'],0)
        self.assertTrue(all(not policy['complete'] for policy in facts['policies']))

    def test_new_evidence_contract_does_not_change_v15_frozen_contract(self):
        from traffic_agent.conclusion_review import contract, VERSION as CONCLUSION_VERSION
        p=self.payload();context=p['context']
        context['measured_conclusion']=dict(version='measured-conclusion-v15')
        self.assertNotIn('evidence_support',contract(p['observations'],context))
        context['measured_conclusion']['version']=CONCLUSION_VERSION
        self.assertEqual(contract(p['observations'],context)['evidence_support']['eligible_policy_count'],0)


if __name__=='__main__':unittest.main()
