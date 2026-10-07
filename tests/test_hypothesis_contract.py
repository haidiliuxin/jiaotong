"""Synthetic measurements for contract semantics; no model or native calls."""
import copy
import unittest

from traffic_agent import hypothesis_contract as hc
from traffic_agent.research_evidence import validate_updates, hypothesis_update_schema, findings
from traffic_agent.planning_response import ResponseValidationError
from traffic_agent.task import Task
from traffic_agent.llm_client import ModelError
import test_natural_plan as natural_tests


def observation(name, changes=(), *, factor=1, core=100, neighbor=100, eligible=True, gross=0):
    return dict(name=name, changes=list(changes), demand_factor=factor, execution=dict(status='completed'),
        metrics={s:dict(running_time_pcu_h=t) for s,t in [('core',core),('neighbor',neighbor),('network',core+neighbor)]},
        comparison=dict(eligible=eligible, violations=[] if eligible else ['实际约束未通过'],
            new_neighbor_oversaturated_km=gross,
            delta={s:dict(running_time_change_pct=t) for s,t in [('core',core-100),('neighbor',neighbor-100),('network',(core+neighbor)/2-100)]}))


PARK=[dict(from_node=1,to_node=2,old_parking=3,parking=1)]
CONTROL=[dict(kind='oneway',from_node=2,to_node=1)]


def state(scenario='parking', factors=(1,)):
    task=Task(scenario=scenario,road_name='离线测试路',request='比较停车治理',repeat_baseline=False,
              demand_factors=list(factors),max_runs=8,recommendation_version='tolerance-v2').as_dict()
    result=dict(id='synthetic',status='running',task=task,planning_protocol='observed-planning-v3',
                runs=[observation('baseline',factor=f) for f in factors],run_count=len(factors))
    if scenario!='parking':
        result['factorial_design']=dict(policies=dict(S1_parking=PARK,S2_oneway=CONTROL,S3_combined=PARK+CONTROL))
    return result


def add(s, name='parking', changes=PARK, **kwargs):
    run=observation(name,changes,**kwargs)
    if s['task']['scenario']!='parking':
        run['policy_label']=name
        if any(c.get('kind')=='oneway' for c in changes):run['oneway_response']=dict(passed=True)
    s['runs'].append(run);s['run_count']+=1
    return run


def hypothesis(states, test='core_time_decrease', policies='parking', quantifier='any', id='H1'):
    entries=[dict(task=s['task'],source_studies=[i+1]) for i,s in enumerate(states)]
    assessment=[dict(hypothesis='离线问题，检验指定研究范围',evidence_needed='同需求新基准与实际执行结果',
                     criterion=dict(test=test,studies=list(range(1,len(states)+1)),policies=policies,quantifier=quantifier))]
    return dict(hc.hypotheses(hc.bind(assessment,entries,{}))[0],id=id)


def evaluate(h,*states):
    return hc.finding(h,{f'study_{i+1}':s for i,s in enumerate(states)})


class ContractTests(unittest.TestCase):
    def test_region_non_worsening_is_strict_and_separate_from_eligibility(self):
        s=state();s['regions']=[dict(id='area_1'),dict(id='area_2')]
        r=add(s,core=80,eligible=True)
        r['comparison']['regional_checks']=[
            dict(region='area_1',before=dict(running_time_pcu_h=100),after=dict(running_time_pcu_h=90),time_change_pct=-10,passed=True),
            dict(region='area_2',before=dict(running_time_pcu_h=100),after=dict(running_time_pcu_h=100.2),time_change_pct=.2,passed=True)]
        h=hypothesis([s],test='region_no_worsening')
        f=evaluate(h,s)
        self.assertEqual(f['expected_judgment'],'contradicted')
        self.assertEqual(f['measurements'][0]['policies'][0]['cases'][0]['values']['increased_regions'],['area_2'])
        self.assertEqual(evaluate(hypothesis([s],test='admissible'),s)['expected_judgment'],'supported')
        r['comparison']['regional_checks'].pop()
        self.assertEqual(evaluate(h,s)['expected_judgment'],'unresolved')
        r['comparison']['regional_checks'].append(
            dict(region='area_2',before=dict(running_time_pcu_h=100),after=dict(running_time_pcu_h=100),time_change_pct=0,passed=True))
        self.assertEqual(evaluate(h,s)['expected_judgment'],'supported')
        r['comparison']['regional_checks'].append(dict(r['comparison']['regional_checks'][-1]))
        self.assertEqual(evaluate(h,s)['expected_judgment'],'unresolved')
        r['comparison']['regional_checks'].pop()
        r['comparison']['regional_checks'][-1]['time_change_pct']=.1
        self.assertEqual(evaluate(h,s)['expected_judgment'],'unresolved')

    def test_policy_catalog_members_are_the_measured_members(self):
        catalog=hc.policy_catalog()
        self.assertEqual(catalog['version'],'hypothesis-policy-family-v1')
        self.assertEqual(catalog['families']['traffic_control']['members'],['traffic_control'])
        self.assertEqual(catalog['families']['control_and_combined']['members'],['traffic_control','combined'])
        self.assertEqual(hc.effective_policies('all','admissible'),('parking','traffic_control','combined'))
        self.assertEqual(hc.effective_policies('all','core_advantage_over_parking'),('traffic_control','combined'))
        self.assertEqual(hc.effective_policies('control_and_combined','core_advantage_over_parking'),('traffic_control','combined'))

    def test_control_and_combined_excludes_parking_without_excluding_either_control_policy(self):
        s=state('oneway')
        add(s,'S1_parking',PARK,core=110,eligible=False)
        add(s,'S2_oneway',CONTROL,core=80)
        add(s,'S3_combined',PARK+CONTROL,core=70)
        narrow=evaluate(hypothesis([s],test='admissible',policies='control_and_combined',quantifier='all'),s)
        self.assertEqual(narrow['expected_judgment'],'supported')
        self.assertEqual([p['policy'] for p in narrow['measurements'][0]['policies']],['traffic_control','combined'])
        self.assertEqual(evaluate(hypothesis([s],test='admissible',policies='all',quantifier='all'),s)['expected_judgment'],'contradicted')
        s['runs'][-1]['comparison']['eligible']=False
        self.assertEqual(evaluate(hypothesis([s],test='admissible',policies='control_and_combined',quantifier='all'),s)['expected_judgment'],'contradicted')

    def test_core_gain_is_separate_from_admissibility(self):
        s=state();add(s,core=46,neighbor=103,eligible=False)
        self.assertEqual(evaluate(hypothesis([s]),s)['expected_judgment'],'supported')
        rejected=evaluate(hypothesis([s],test='admissible'),s)
        self.assertEqual(rejected['expected_judgment'],'contradicted')
        self.assertEqual(rejected['measurements'][0]['policies'][0]['cases'][0]['values']['constraint_passed'],False)

    def test_measured_counterexample_refutes_all_even_with_missing_other_group(self):
        a,b=state(),state();add(a,core=50,neighbor=300,eligible=False)
        h=hypothesis([a,b],test='neighbor_limits',policies='all',quantifier='all')
        f=evaluate(h,a,b)
        self.assertEqual(f['expected_judgment'],'contradicted')
        self.assertIsNone(f['measurements'][1]['passed'])
        with self.assertRaisesRegex(ResponseValidationError,'原|判据'):
            validate_updates([dict(hypothesis_id='H1',judgment='unresolved',finding_ids=[f['id']],explanation='将原全称命题弱化为部分措施仍可能有效')],[h],dict(findings=[f]),final=True)

    def test_any_requires_same_policy_across_every_demand(self):
        s=state(factors=(.9,1.1))
        p2=[dict(PARK[0],parking=2)]
        for factor,core in [(0.9,70),(1.1,120)]:add(s,f'first_{factor}',factor=factor,core=core)
        for factor,core in [(0.9,120),(1.1,70)]:add(s,f'second_{factor}',changes=p2,factor=factor,core=core)
        self.assertEqual(evaluate(hypothesis([s]),s)['expected_judgment'],'contradicted')
        s['runs'][-1]['metrics']['core']['running_time_pcu_h']=60
        s['runs'][-2]['metrics']['core']['running_time_pcu_h']=60
        self.assertEqual(evaluate(hypothesis([s]),s)['expected_judgment'],'supported')

    def test_no_candidate_and_partial_demands_are_unknown(self):
        s=state(factors=(1,1.2));h=hypothesis([s])
        self.assertEqual(evaluate(h,s)['expected_judgment'],'unresolved')
        add(s,core=50)
        self.assertEqual(evaluate(h,s)['expected_judgment'],'unresolved')

    def test_all_does_not_pass_before_required_comparison_is_complete(self):
        s=state();s['task']['request']='比较1级与2级强度';add(s,core=50)
        self.assertEqual(evaluate(hypothesis([s],quantifier='all'),s)['expected_judgment'],'unresolved')
        self.assertEqual(evaluate(hypothesis([s]),s)['expected_judgment'],'supported')

    def test_neighborhood_gross_and_net_cannot_cancel(self):
        s=state();r=add(s,core=50,neighbor=90,gross=.2)
        r['comparison']['delta']['neighbor']['oversaturated_km_change']=-10
        f=evaluate(hypothesis([s],test='neighbor_limits'),s)
        self.assertEqual(f['expected_judgment'],'contradicted')
        r['comparison']['new_neighbor_oversaturated_km']=.05
        self.assertEqual(evaluate(hypothesis([s],test='neighbor_limits'),s)['expected_judgment'],'supported')

    def test_known_loss_settles_neighbor_limits_even_if_other_metric_is_missing(self):
        s=state();r=add(s,neighbor=110)
        r['comparison'].pop('new_neighbor_oversaturated_km')
        self.assertEqual(evaluate(hypothesis([s],test='neighbor_limits'),s)['expected_judgment'],'contradicted')
        r['metrics']['neighbor']['running_time_pcu_h']=90
        self.assertEqual(evaluate(hypothesis([s],test='neighbor_limits'),s)['expected_judgment'],'unresolved')

    def test_combination_can_have_more_gain_and_still_be_rejected(self):
        s=state('oneway');add(s,'S1_parking',core=70)
        add(s,'S2_oneway',CONTROL,core=20,neighbor=200,eligible=False)
        combo=add(s,'S3_combined',PARK+CONTROL,core=10,neighbor=200,eligible=False)
        h=hypothesis([s],test='combination_core_advantage',policies='combined')
        f=evaluate(h,s)
        self.assertEqual(f['expected_judgment'],'supported')
        self.assertEqual(f['measurements'][0]['policies'][0]['cases'][0]['values']['combination_minus_singles_pcu_h'],[-60,-10])
        self.assertEqual(evaluate(hypothesis([s],test='admissible',policies='combined'),s)['expected_judgment'],'contradicted')
        combo['metrics']['core']['running_time_pcu_h']=20
        self.assertEqual(evaluate(h,s)['expected_judgment'],'contradicted')

    def test_control_response_is_not_implied_by_parameters_or_cost_change(self):
        s=state('oneway');r=add(s,'S2_oneway',CONTROL,core=20,eligible=True)
        r['oneway_response']['passed']=False
        self.assertEqual(evaluate(hypothesis([s],policies='traffic_control'),s)['expected_judgment'],'unresolved')
        self.assertEqual(evaluate(hypothesis([s],test='admissible',policies='traffic_control',quantifier='all'),s)['expected_judgment'],'contradicted')

    def test_extra_benefit_over_parking_is_distinct_from_combination_over_both_singles(self):
        s=state('oneway');add(s,'S1_parking',core=70)
        add(s,'S2_oneway',CONTROL,core=20);add(s,'S3_combined',PARK+CONTROL,core=30)
        h=hypothesis([s],test='core_advantage_over_parking',policies='all',quantifier='all')
        f=evaluate(h,s)
        self.assertEqual(f['expected_judgment'],'supported')
        self.assertEqual(len(f['measurements'][0]['policies']),2)
        self.assertEqual(evaluate(hypothesis([s],test='combination_core_advantage',policies='combined'),s)['expected_judgment'],'contradicted')

    def test_different_engines_are_independently_paired_never_pooled(self):
        a,b=state(),state('oneway');add(a,core=1)
        add(b,'S1_parking',core=110)
        h=hypothesis([a,b],quantifier='all')
        self.assertEqual(evaluate(h,a,b)['expected_judgment'],'contradicted')

    def test_duplicate_metrics_nonfinite_and_zero_base_are_not_cherry_picked(self):
        for modification in ('repeat','nan','zero'):
            with self.subTest(modification=modification):
                s=state();add(s,core=50)
                if modification=='repeat':s['runs'].append(observation('baseline_repeat',core=10))
                elif modification=='nan':s['runs'][1]['metrics']['core']['running_time_pcu_h']=float('nan')
                else:s['runs'][0]['metrics']['core']['running_time_pcu_h']=0
                self.assertEqual(evaluate(hypothesis([s]),s)['expected_judgment'],'unresolved')

    def test_identical_repeat_is_allowed_despite_baseline_metadata(self):
        s=state();add(s,core=50);r=copy.deepcopy(s['runs'][0]);r.update(name='baseline_repeat',comparison={})
        s['runs'].append(r)
        self.assertEqual(evaluate(hypothesis([s]),s)['expected_judgment'],'supported')

    def test_failed_run_or_wrong_declared_scope_cannot_supply_evidence(self):
        s=state();r=add(s,core=50);h=hypothesis([s]);r['execution']['status']='failed'
        self.assertEqual(evaluate(h,s)['expected_judgment'],'unresolved')
        r['execution']['status']='completed';s['task']['road_name']='另一条路'
        self.assertEqual(evaluate(h,s)['expected_judgment'],'unresolved')

    def test_unavailable_keeps_question_unknown_without_proxy_substitution(self):
        s=state();add(s,core=20)
        h=hypothesis([s],test='unavailable');h['hypothesis']='治理是否消除排队成因'
        f=evaluate(h,s)
        self.assertEqual(f['expected_judgment'],'unresolved');self.assertEqual(f['evidence_refs'],[])
        self.assertEqual(f['question'],h['hypothesis'])

    def test_review_must_use_own_criterion_and_frozen_judgment(self):
        s=state();add(s,core=46,eligible=False);h=hypothesis([s]);f=evaluate(h,s)
        other=dict(f,id='benefit');other['status']='not_supported'
        evidence=dict(findings=[f,other])
        valid=dict(hypothesis_id='H1',judgment='supported',finding_ids=[f['id']],explanation='核心耗时下降，但未通过推荐约束，两个判断分别保留。')
        validate_updates([valid],[h],evidence,final=True)
        for patch in (dict(judgment='contradicted'),dict(judgment='unresolved'),dict(finding_ids=['benefit'])):
            with self.assertRaises(ResponseValidationError):validate_updates([dict(valid,**patch)],[h],evidence,final=True)
        schema=hypothesis_update_schema([h],evidence,True)
        self.assertEqual(schema['items']['anyOf'][0]['properties']['judgment']['enum'],['supported'])

    def test_legacy_findings_and_schema_are_not_retrospectively_changed(self):
        h=dict(id='H1',hypothesis='历史原问题',evidence_needed='原证据')
        e=dict(findings=[dict(id='benefit',status='supported',evidence_refs=['old:x'])])
        u=dict(hypothesis_id='H1',judgment='unresolved',finding_ids=['benefit'],explanation='旧记录保持原协议，不自动补充事前标准。')
        validate_updates([u],[h],e,final=True)
        self.assertNotIn('anyOf',hypothesis_update_schema([h],e)['items'])
        self.assertEqual(hc.findings([h],{}),[])

    def test_safe_compilation_keeps_criterion_linked_to_original_group(self):
        s=state();h=hypothesis([s]);raw={k:v for k,v in h.items() if k in ('hypothesis','evidence_needed','criterion')}
        entries=[dict(task=s['task'],source_studies=[2,3])]
        bound=hc.bind([raw],entries,dict(actions=[dict(source_study=1,retained_source_study=2)]))
        self.assertEqual(bound[0]['criterion']['studies'],[1])
        self.assertEqual(bound[0]['evaluation']['scope'][0]['study_id'],'study_1')

    def test_impossible_group_and_wrong_control_type_fail_before_execution(self):
        s=state();h=hypothesis([s]);entries=[dict(task=s['task'],source_studies=[1])]
        for criterion in (dict(h['criterion'],studies=[2]),dict(h['criterion'],policies='combined'),
                          dict(h['criterion'],studies=[True]),dict(h['criterion'],test=[])):
            with self.assertRaises(ModelError):hc.bind([dict(h,criterion=criterion)],entries,{})

    def test_local_evidence_carries_declared_criterion_only_for_single_study(self):
        s=state();add(s,core=50);s['research_hypotheses']=[hypothesis([s])]
        self.assertIn('hypothesis.H1',[f['id'] for f in findings(s)['findings']])
        s['review_scope']=dict(mode='programme_final_only')
        self.assertNotIn('hypothesis.H1',[f['id'] for f in findings(s)['findings']])


class InitialContractTests(unittest.TestCase):
    def test_current_wire_requires_a_criterion(self):
        helper=natural_tests.NaturalPlanTests()
        with self.assertRaises(ModelError):helper.compose([natural_tests.experiment(['parking'])],assessment=[
            dict(hypothesis='原问题',evidence_needed='新基准与治理结果')])

    def test_initial_plan_freezes_resolved_scope_and_exposes_tests(self):
        helper=natural_tests.NaturalPlanTests();result=helper.compose([natural_tests.experiment(['parking'])],assessment=[
            dict(hypothesis='本次已试停车措施中至少一项使核心耗时下降',evidence_needed='同需求新基准与治理耗时',
                 criterion=dict(test='core_time_decrease',studies=[1],policies='parking',quantifier='any'))])
        self.assertEqual(result['assessment'][0]['evaluation']['scope'][0]['road'],'长江村路')
        self.assertEqual(result['hypothesis_contract_protocol'],hc.VERSION)
        self.assertIn('hypothesis_tests',helper.client.request.call_args.args[1][0]['content'])


if __name__=='__main__':unittest.main()
