"""Read-only regressions for the two sealed failures and typed action semantics."""
import copy
import json
import unittest
from sealed_planning_fixtures import load_sealed
from pathlib import Path

from traffic_agent import question_wording as wording
from traffic_agent.feedback_planner import direction_claims_match, direction_claims_match_v2, direction_claims_match_v3, wire_payload
from traffic_agent.hypothesis_contract import cell
from traffic_agent.conclusion_review import VERSION as CONCLUSION_VERSION, prose_issues, _affirmed
from traffic_agent import study_objectives
from traffic_agent.planning_scope import PrerequisiteError


ROOT = Path(__file__).resolve().parents[1]


class SemanticContractV3Tests(unittest.TestCase):
    def test_new_separate_region_question_is_not_replaced_by_admissibility(self):
        request='分别检查核心耗时是否下降、各区域是否恶化以及邻区是否满足原定门槛。'
        required=study_objectives.required(request)
        self.assertEqual(required['tests'],['core_time_decrease','region_no_worsening','neighbor_limits'])
        actual=[dict(criterion=dict(test=x,policies='all')) for x in ('core_time_decrease','admissible','neighbor_limits')]
        with self.assertRaisesRegex(PrerequisiteError,'各区域'):
            study_objectives.verify(required,actual)
        actual.append(dict(criterion=dict(test='region_no_worsening',policies='parking')))
        study_objectives.verify(required,actual)
        self.assertEqual(study_objectives.required(request,version=study_objectives.LEGACY_VERSION)['tests'],[])

    def test_explicit_separate_tests_are_frozen_before_native_run(self):
        request=('独立研究长江村路的停车治理、单向组织及两者组合。分别检验原生禁行控制是否生效、'
                 '冻结核心耗时是否下降、邻区代价是否在原定门槛内。依据本次新基准选择单向关闭方向。')
        required=study_objectives.required(request)
        self.assertEqual(required['tests'],['control_response','core_time_decrease','neighbor_limits'])
        self.assertEqual(required['policy_scopes']['control_response'],'control_and_combined')
        actual=[dict(criterion=dict(test=x,policies='all')) for x in ('core_time_decrease','admissible','neighbor_limits',
                                                       'combination_core_advantage')]
        with self.assertRaisesRegex(PrerequisiteError,'原生控制响应'):
            study_objectives.verify(required,actual)
        actual.append(dict(criterion=dict(test='control_response',policies='traffic_control')))
        with self.assertRaisesRegex(PrerequisiteError,'缩小'):
            study_objectives.verify(required,actual)
        actual[-1]['criterion']['policies']='control_and_combined'
        study_objectives.verify(required,actual)
        self.assertEqual(study_objectives.required('找一个可用方案就停')['tests'],[])

    def test_sealed_mixed_question_keeps_parking_in_scope(self):
        record, = load_sealed('quantifier_live_raw_evidence')
        original=record['assessment'][1]
        self.assertEqual(original['criterion']['policies'],'control_and_combined')
        self.assertEqual(original['hypothesis'],
                         '长江村路停车、单向及组合的每种已试措施核心总耗时均低于同需求新基准')
        self.assertEqual(wording.policy_scope(original['hypothesis']),'all')
        self.assertIn('policy_scope_changed',[x['code'] for x in wording.conflicts(original,
            dict(test='core_time_decrease',policies='control_and_combined',quantifier='all',
                 scope=[dict(study_id='study_2',road='长江村路',demand_factors=[1])]),version=wording.VERSION)])
        # Historical records remain bound to their own, narrower v2 grammar.
        self.assertEqual(wording.conflicts(original,record['assessment'][1]['evaluation'],
                                           version=wording.PREVIOUS_VERSION),[])

    def test_latest_public_quantifier_and_threshold_are_not_silently_redefined(self):
        h=dict(hypothesis='停车组织降低核心总耗时，至少在某一个已试停车方案上成立。',evidence_needed='配对耗时')
        e=dict(test='core_time_decrease',policies='parking',quantifier='all',
               scope=[dict(study_id='study_1',road=None,demand_factors=[1])])
        self.assertIn('quantifier_changed',[x['code'] for x in wording.conflicts(h,e)])
        h['hypothesis']='所有已试停车方案均需使邻区毛新增过饱和不超过0km门槛。'
        e.update(test='neighbor_limits',policies='all',
                 scope=[dict(study_id='study_1',road=None,demand_factors=[1],
                             max_neighbor_time_increase_pct=.5,max_new_oversaturated_km=.1)])
        self.assertIn('neighbor_threshold_changed',[x['code'] for x in wording.conflicts(h,e)])

    def test_control_response_is_independent_of_recommendation(self):
        base=dict(name='baseline')
        run=dict(name='S2_oneway',changes=[dict(kind='oneway',closed_direction=[3237,3236])],
                 comparison=dict(eligible=False),oneway_response=dict(passed=True))
        state=dict(id='case',task=dict())
        self.assertIs(cell('control_response',state,base,run)['passed'],True)
        self.assertIs(cell('admissible',state,base,run)['passed'],False)
        h=dict(hypothesis='单向禁行原生控制是否生效。',evidence_needed='原生方向流量')
        e=dict(test='admissible',policies='traffic_control',quantifier='any',
               scope=[dict(study_id='study_1',road=None,demand_factors=[1])])
        self.assertIn('control_substitutes_recommendation',[x['code'] for x in wording.conflicts(h,e)])

    def test_closed_and_open_directions_share_selected_nodes(self):
        selected=dict(closed_direction=[3237,3236])
        self.assertFalse(direction_claims_match(dict(reason='3237→3236保留通行，反向禁行'),selected))
        self.assertFalse(direction_claims_match(dict(reason='3236→3237禁行'),selected))
        self.assertTrue(direction_claims_match(dict(reason='关闭3237→3236，保留3236→3237'),selected))
        self.assertTrue(direction_claims_match(dict(reason='关闭3237→3236方向以实施单向通行'),selected))
        review=json.loads((ROOT/'docs/validation/20260928_quantifier_live_manual_review.json').read_text('utf-8'))
        original=next(x for x in review['remaining_issues'] if x['id']=='oneway_prose_reverses_actual_closure')
        self.assertFalse(direction_claims_match(dict(reason=original['original_reason'],
            risk=original['original_risk'],selection_basis=original['original_selection_basis']),selected))

    def test_direction_v2_resolves_reverse_referent_without_changing_v1(self):
        selected=dict(closed_direction=[3237,3236])
        correct=dict(reason='禁行拥堵方向的3237→3236单向，保留反向通行。',
                     selection_basis=dict(working_hypothesis='禁行拥堵方向3237→3236、保留相对畅通反向后再测。'))
        self.assertFalse(direction_claims_match(correct,selected))
        self.assertTrue(direction_claims_match_v2(correct,selected))
        self.assertTrue(direction_claims_match_v2(dict(reason='禁行3237→3236保留相对畅通反向通行'),selected))
        for claim in ('3237→3236保留通行，反向禁行', '3236→3237禁行',
                      '3237→3236单向通行', '保留3237→3236方向通行',
                      '3237→3236保留通行但反向另议'):
            with self.subTest(claim=claim):
                self.assertFalse(direction_claims_match_v2(dict(reason=claim),selected))
        self.assertTrue(direction_claims_match_v2(dict(reason='关闭3237→3236，保留3236→3237'),selected))

    def test_direction_v3_resolves_diversion_to_retained_direction(self):
        selected=dict(closed_direction=[3237,3236])
        good='禁行高拥堵方向3237→3236可将车流导向保留方向，随后核对绕行代价。'
        self.assertFalse(direction_claims_match_v2(dict(selection_basis=dict(working_hypothesis=good)),selected))
        self.assertTrue(direction_claims_match_v3(dict(selection_basis=dict(working_hypothesis=good)),selected))
        for bad in ('3237→3236保留通行', '保留3237→3236方向通行',
                    '3236→3237禁行', '3237→3236可将车流导向保留方向且3237→3236保留通行'):
            with self.subTest(bad=bad):
                self.assertFalse(direction_claims_match_v3(dict(reason=bad),selected))

    def test_public_threshold_claim_uses_frozen_number(self):
        facts=dict(version=CONCLUSION_VERSION,costs=[],executed_policies=[],hypothesis_gaps=[])
        context=dict(task=dict(max_new_oversaturated_km=.1))
        bad=prose_issues(dict(next_check='核对邻区毛新增过饱和是否低于0km门槛'),facts,context)
        self.assertIn('threshold_changed',[x['code'] for x in bad])
        good=prose_issues(dict(next_check='核对邻区毛新增过饱和是否低于0.1km门槛'),facts,context)
        self.assertNotIn('threshold_changed',[x['code'] for x in good])

    def test_negated_zero_risk_is_not_an_affirmative_claim(self):
        phrase='批次分配无法证明动态排队回堵或零风险'
        pattern=r'(?:无|没有|零)(?:任何|明显)?(?:周边|邻区)?(?:溢出风险|风险)'
        self.assertEqual(list(_affirmed(pattern,phrase,True)),[])
        self.assertEqual(list(_affirmed(pattern,phrase,False)),['零风险'])
        self.assertEqual(list(_affirmed(pattern,'本次治理零风险',True)),['零风险'])
        cost=dict(ref='S2',run='S2_oneway',roads=[],constraint_passed=False,has_measured_cost=True,
                  neighbor_new_oversaturated_km=1.0,neighbor_time_change_pct=1.0,
                  neighbor_net_oversaturated_km=0,network_time_change_pct=0,
                  network_net_oversaturated_km=0,network_new_oversaturated_km=0)
        facts=dict(version='measured-conclusion-v4',costs=[cost],executed_policies=[],hypothesis_gaps=[])
        answer=dict(risk='单需求不能证明跨需求稳健性；'+phrase+'，需保留实测邻区代价。')
        self.assertIn('zero_risk',[x['code'] for x in prose_issues(answer,facts,{})])
        facts['version']=CONCLUSION_VERSION
        self.assertNotIn('zero_risk',[x['code'] for x in prose_issues(answer,facts,{})])

    def test_wire_compaction_preserves_audit_input_and_negative_evidence(self):
        original=dict(request='先治理停车，不转移拥堵',context=dict(
            programme_hypotheses=[dict(id='H1',hypothesis='原问题',evidence_needed='配对结果',
                                      evaluation=dict(interpretation=dict(question='完整定义')))],
            programme_evidence=dict(findings=[dict(id='hypothesis.H1',status='not_supported')]),
            programme_cost_facts=[dict(new_oversaturated_km=.749)],
            task=dict(max_new_oversaturated_km=.1)))
        saved=copy.deepcopy(original)
        wire=wire_payload(original)
        self.assertEqual(original,saved)
        self.assertNotIn('evaluation',wire['context']['programme_hypotheses'][0])
        self.assertEqual(wire['context']['programme_evidence'],saved['context']['programme_evidence'])
        self.assertEqual(wire['context']['programme_cost_facts'],saved['context']['programme_cost_facts'])
        self.assertEqual(wire['context']['task'],saved['context']['task'])
        self.assertLess(len(json.dumps(wire)),len(json.dumps(original)))

    def test_single_study_wire_omits_identical_programme_hypotheses(self):
        frozen=[dict(id='H1',hypothesis='原问题',evidence_needed='实际配对',
                     evaluation=dict(test='neighbor_limits',max_new_oversaturated_km=.1))]
        original=dict(request='比较两个方案',context=dict(
            review_scope=dict(mode='single_study'),initial_hypotheses=copy.deepcopy(frozen),
            programme_hypotheses=copy.deepcopy(frozen),
            programme_evidence=dict(findings=[dict(id='study_1.tradeoff',status='not_supported')]),
            measured_conclusion=dict(costs=[dict(new_neighbor_oversaturated_km=2.797)]),
            task=dict(max_new_oversaturated_km=.1)))
        saved=copy.deepcopy(original)
        wire=wire_payload(original)
        self.assertEqual(original,saved)
        self.assertNotIn('programme_hypotheses',wire['context'])
        self.assertEqual(wire['context']['initial_hypotheses'],[
            dict(id='H1',hypothesis='原问题',evidence_needed='实际配对')])
        for key in ('programme_evidence','measured_conclusion','task'):
            self.assertEqual(wire['context'][key],saved['context'][key])

    def test_wire_omits_only_exact_duplicate_measured_facts(self):
        cost=dict(ref='trial',constraint_passed=False,neighbor_new_oversaturated_km=2.797)
        policy=dict(ref='trial',parking_levels=[1],closed_directions=[])
        original=dict(request='比较实测后停下',context=dict(
            measured_conclusion=dict(costs=[copy.deepcopy(cost)],executed_policies=[copy.deepcopy(policy)]),
            programme_cost_facts=[copy.deepcopy(cost)],programme_policy_facts=[copy.deepcopy(policy)]))
        saved=copy.deepcopy(original)
        wire=wire_payload(original)
        self.assertEqual(original,saved)
        self.assertNotIn('programme_cost_facts',wire['context'])
        self.assertNotIn('programme_policy_facts',wire['context'])
        self.assertEqual(wire['context']['measured_conclusion'],saved['context']['measured_conclusion'])
        changed=copy.deepcopy(original)
        changed['context']['programme_cost_facts'][0]['neighbor_new_oversaturated_km']=2.8
        changed['context']['programme_policy_facts'][0]['parking_levels']=[2]
        different=wire_payload(changed)
        self.assertEqual(different['context']['programme_cost_facts'],changed['context']['programme_cost_facts'])
        self.assertEqual(different['context']['programme_policy_facts'],changed['context']['programme_policy_facts'])

    def test_single_study_wire_only_omits_proven_duplicate_findings(self):
        local=[dict(id='benefit',question='核心是否改善',status='not_supported',
                    answer='耗时下降但约束失败',evidence_refs=['baseline','trial']),
               dict(id='hypothesis.H1',question='全部候选是否合格',status='not_supported',
                    answer='有实测反例',evidence_refs=['baseline','trial'])]
        programme=[dict(local[0],id='study_1.benefit',question='停车研究：核心是否改善'),copy.deepcopy(local[1])]
        original=dict(request='比较两个方案',context=dict(review_scope=dict(mode='single_study'),
            research_evidence=dict(findings=local),
            programme_evidence=dict(groups=[dict(id='study_1',title='停车研究')],findings=programme),
            measured_conclusion=dict(costs=[dict(ref='trial',neighbor_new_oversaturated_km=2.797)]),
            completion_guard=dict(required_candidate_comparisons=2),
            choice_observations=dict(recent_trial='trial:trial')))
        saved=copy.deepcopy(original)
        wire=wire_payload(original)
        self.assertEqual(original,saved)
        self.assertNotIn('programme_evidence',wire['context'])
        for key in ('research_evidence','measured_conclusion','completion_guard','choice_observations'):
            self.assertEqual(wire['context'][key],saved['context'][key])
        changed=copy.deepcopy(original)
        changed['context']['programme_evidence']['findings'][0]['answer']='代价消失'
        self.assertIn('programme_evidence',wire_payload(changed)['context'])
        changed=copy.deepcopy(original)
        changed['context']['programme_evidence']['groups'].append(dict(id='study_2',title='另一研究'))
        self.assertIn('programme_evidence',wire_payload(changed)['context'])

    def test_wire_omits_only_exact_duplicate_hypothesis_wording_and_contract(self):
        scope=[dict(study_id='study_1',demand_factors=[.9,1.0],max_new_oversaturated_km=.1)]
        contract=dict(test='neighbor_limits',policies='parking',quantifier='all',scope=scope)
        finding=dict(id='hypothesis.H1',hypothesis_id='H1',question='所有需求均过邻区门槛吗？',
                     model_question='原始模型提问',status='not_supported',
                     answer='需求1.0失败、0.9邻区通过',evidence_refs=['baseline','trial'],
                     evaluation=dict(**contract,interpretation=dict(question='所有需求均过邻区门槛吗？',
                         contract=copy.deepcopy(contract),boundary='仅限离散需求点')))
        original=dict(context=dict(initial_hypotheses=[dict(id='H1',hypothesis='原始模型提问',
            evidence_needed='配对实测')],research_evidence=dict(findings=[finding]),
            task=dict(max_new_oversaturated_km=.1)))
        saved=copy.deepcopy(original)
        wire=wire_payload(original)
        self.assertEqual(original,saved)
        compact=wire['context']['research_evidence']['findings'][0]
        self.assertNotIn('model_question',compact)
        self.assertNotIn('question',compact['evaluation']['interpretation'])
        self.assertNotIn('contract',compact['evaluation']['interpretation'])
        self.assertEqual(compact['answer'],finding['answer'])
        self.assertEqual(compact['evaluation']['scope'],scope)
        self.assertEqual(compact['evaluation']['interpretation']['boundary'],'仅限离散需求点')
        changed=copy.deepcopy(original)
        changed_finding=changed['context']['research_evidence']['findings'][0]
        changed_finding['model_question']='另一个问题'
        changed_finding['evaluation']['interpretation']['question']='另一判据问题'
        changed_finding['evaluation']['interpretation']['contract']['scope'][0]['max_new_oversaturated_km']=.2
        different=wire_payload(changed)['context']['research_evidence']['findings'][0]
        self.assertEqual(different['model_question'],'另一个问题')
        self.assertEqual(different['evaluation']['interpretation']['question'],'另一判据问题')
        self.assertEqual(different['evaluation']['interpretation']['contract']['scope'][0]['max_new_oversaturated_km'],.2)

    def test_wire_hoists_only_identical_interpretations_with_finding_ids(self):
        shared=dict(version='bounded-question-wording-v4',boundary='仅核对冻结范围',limits=['保留负面结果'])
        findings=[dict(id=f'hypothesis.H{i}',status='not_supported' if i==1 else 'unmeasured',
                       evidence_refs=['trial'] if i==1 else [],
                       evaluation=dict(test='neighbor_limits',scope=[dict(study_id='study_1')],
                                       interpretation=copy.deepcopy(shared))) for i in (1,2)]
        original=dict(context=dict(programme_evidence=dict(findings=findings),
                                   measured_conclusion=dict(costs=[dict(ref='trial',constraint_passed=False)])))
        saved=copy.deepcopy(original)
        wire=wire_payload(original)
        self.assertEqual(original,saved)
        self.assertEqual(wire['context']['shared_evaluation_interpretations'],dict(
            programme_evidence=dict(finding_ids=['hypothesis.H1','hypothesis.H2'],interpretation=shared)))
        compact=wire['context']['programme_evidence']['findings']
        self.assertEqual([f['status'] for f in compact],['not_supported','unmeasured'])
        self.assertEqual(compact[0]['evidence_refs'],['trial'])
        self.assertTrue(all('interpretation' not in f['evaluation'] for f in compact))
        self.assertEqual(wire['context']['measured_conclusion'],saved['context']['measured_conclusion'])
        different=copy.deepcopy(original)
        different['context']['programme_evidence']['findings'][1]['evaluation']['interpretation']['limits']=['另一限制']
        kept=wire_payload(different)['context']
        self.assertNotIn('shared_evaluation_interpretations',kept)
        self.assertEqual(kept['programme_evidence']['findings'][1]['evaluation']['interpretation']['limits'],['另一限制'])


if __name__=='__main__':unittest.main()
