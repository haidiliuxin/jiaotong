"""Offline action-label and bounded prose checks; no provider/native calls."""
import copy
import json
import unittest
from unittest.mock import Mock

from traffic_agent.conclusion_review import (VERSION, V11_VERSION, V10_VERSION, LEGACY_VERSION, LEGACY_SCOPE,
    contract, assess_entry, expected_acknowledgments, prose_issues)
from traffic_agent.executed_facts import policy_facts, named_rows
from traffic_agent.feedback_planner import PlanningSession, verify_review_permissions
from test_conclusion_review import observation, entry
from test_feedback_planner import reply


def measured():
    o=observation('j:S1_parking', new=0, net=0)
    o['changes'][0]['parking']=2
    o['delta']['neighbor']['running_time_change_pct']=.0000149734122656
    o['delta']['network']['oversaturated_km_change']=0
    return o


class ExecutedFactsTests(unittest.TestCase):
    def test_negated_action_predicates_do_not_assert_an_executed_level(self):
        observations=[observation('j:parking_1',road='乙路')]
        context=dict(measured_conclusion=dict(version=VERSION))
        facts=contract(observations,context)
        for text in ('先前决策未选停车2级对照。','本次没有选择停车2级。',
                     '本轮尚未采用停车2级。','暂未执行停车2级。',
                     '未曾运行停车2级。','还没比较停车2级。'):
            with self.subTest(text=text):
                self.assertEqual(prose_issues(dict(reason=text),facts,context,observations),[])
        for text in ('已选择停车2级。','实际执行停车2级。','并非未选停车2级。',
                     '不是没有执行停车2级。','未测停车1级但已执行停车2级。',
                     '没有选择停车1级，而采用停车2级。'):
            with self.subTest(text=text):
                issues=prose_issues(dict(reason=text),facts,context,observations)
                self.assertEqual([i['code'] for i in issues],['executed_parking_level'])
        old=dict(measured_conclusion=dict(version=V11_VERSION))
        legacy=prose_issues(dict(reason='先前决策未选停车2级对照。'),contract(observations,old),old,observations)
        self.assertEqual([i['code'] for i in legacy],['executed_parking_level'])

    def test_region_level_claim_uses_the_actually_changed_region(self):
        measured_area=observation('j:parking_1',road='乙路')
        measured_area['spatial']=dict(regions=[
            dict(id='area_1',directions=[[1,2]]),
            dict(id='area_2',directions=[[3,4]])])
        text='已测area_1停车1级；area_2仍停车3级。'
        new=dict(measured_conclusion=dict(version=VERSION))
        facts=contract([measured_area],new)
        self.assertEqual(prose_issues(dict(summary=text),facts,new,[measured_area]),[])
        wrong=prose_issues(dict(summary='已测area_1停车3级。'),facts,new,[measured_area])
        self.assertEqual([issue['code'] for issue in wrong],['executed_parking_level'])
        old=dict(measured_conclusion=dict(version=V10_VERSION))
        legacy=prose_issues(dict(summary=text),contract([measured_area],old),old,[measured_area])
        self.assertIn('executed_parking_level',{issue['code'] for issue in legacy})
        second=copy.deepcopy(measured_area)
        second.update(ref='j:parking_2',run='parking_2',changes=[dict(from_node=3,to_node=4,parking=2)],
                      core_roads=[dict(name='乙路',from_node=3,to_node=4)])
        both=contract([measured_area,second],new)
        self.assertEqual(prose_issues(dict(summary='area_1停车1级，area_2停车2级。'),both,new,[measured_area,second]),[])
        wrong=prose_issues(dict(summary='area_2停车3级。'),both,new,[measured_area,second])
        self.assertEqual(wrong[0]['refs'],['j:parking_2'])

    def test_policy_label_comes_from_actual_changes_not_scene_number(self):
        o=measured();before=copy.deepcopy(o)
        facts=contract([o],{})
        self.assertEqual(facts['version'],VERSION)
        self.assertEqual(facts['executed_policies'][0]['parking_levels'],[2])
        self.assertEqual(facts['executed_policies'][0]['label'],'停车2级')
        self.assertEqual(o,before)

    def test_full_reference_then_scene_alias_precedes_shared_road(self):
        o=measured();other=copy.deepcopy(o);other.update(ref='k:S1_parking')
        other['changes'][0]['parking']=1
        rows=policy_facts([o,other])
        self.assertEqual(named_rows('j:S1_parking是停车2级',rows),rows[:1])
        self.assertEqual(len(named_rows('S1停车2级',rows)),2)
        self.assertEqual(named_rows('S10停车1级',rows),[])
        # Ambiguous same-named scenes in different groups do not prove a conflict.
        value=entry('S1停车1级。',[o,other])
        self.assertEqual(assess_entry(value)['issues'],[])

    def test_explicit_wrong_level_is_rejected_and_exact_or_unmeasured_language_is_not(self):
        for text in ('乙路停车1级(S1)可推荐。','S1为1级停车。','j:S1_parking，停车一级通过。'):
            with self.subTest(text=text):
                result=assess_entry(entry(text,[measured()]))
                self.assertEqual({x['code'] for x in result['issues']},{'executed_parking_level'})
        for text in ('S1停车2级可推荐。','S1实际停车二级；停车1级尚未测试。',
                     'S1未测停车1级。','S1不是停车1级，而是停车2级。',
                     'S1的停车1级与2级尚未比较。','不能把S1写成停车1级。'):
            with self.subTest(text=text):
                self.assertEqual(assess_entry(entry(text,[measured()]))['issues'],[])

    def test_each_group_keeps_its_own_level_and_all_public_prose_fields_are_checked(self):
        a=observation('a:parking_1',road='甲路')
        b=measured()
        good='甲路停车1级不合格，乙路停车2级通过。'
        self.assertEqual(assess_entry(entry(good,[a,b]))['issues'],[])
        value=entry('只依据实测。',[a,b]);value['next_check']='S1停车1级可推荐。'
        value['programme_updates']=[dict(hypothesis_id='H1',judgment='supported',explanation='S1停车1级有效')]
        result=assess_entry(value)
        self.assertEqual({x['field'] for x in result['issues']},{'next_check','programme_updates:H1'})

    def test_numeric_zero_cannot_erase_tiny_positive_cost(self):
        for text in ('S1邻区与全网代价0。','S1邻区代价为0.000。','S1是零代价。'):
            with self.subTest(text=text):
                self.assertIn('zero_measured_cost',{x['code'] for x in assess_entry(entry(text,[measured()]))['issues']})
        for text in ('S1邻区代价近似为零，仍有0.000015%的正增加。',
                     'S1邻区毛新增过饱和为0，但耗时有微小增加。',
                     'S1不能说代价0。','S1代价为0.000015%。'):
            with self.subTest(text=text):
                self.assertEqual(assess_entry(entry(text,[measured()]))['issues'],[])
        self.assertEqual(assess_entry(entry('S1全网代价0；邻区有微小正增加。',[measured()]))['issues'],[])

    def test_v1_contract_and_original_bytes_are_preserved_but_gap_is_flagged(self):
        value=entry('S1停车1级，邻区与全网代价0。',[measured()])
        ctx=value['input']['context'];ctx['measured_conclusion']=dict(version=LEGACY_VERSION)
        old=contract(value['input']['observations'],ctx)
        self.assertNotIn('executed_policies',old);self.assertEqual(old['scope'],LEGACY_SCOPE)
        ctx['measured_conclusion']=old
        value['cost_acknowledgments']=expected_acknowledgments(old)
        before=copy.deepcopy(value)
        checked=assess_entry(value)
        self.assertEqual(checked['version'],LEGACY_VERSION)
        self.assertEqual({i['code'] for i in checked['issues']},{'executed_parking_level','zero_measured_cost'})
        self.assertEqual(value,before)

    def test_v1_other_group_with_unknown_level_does_not_inherit_current_groups_level(self):
        from traffic_agent.conclusion_review import cost_facts
        value=entry('甲路停车1级；S1停车1级。',[measured()])
        ctx=value['input']['context']
        ctx.update(programme_cost_facts=cost_facts([observation('a:parking_1',road='甲路')]),
                   measured_conclusion=dict(version=LEGACY_VERSION))
        ctx['measured_conclusion']=contract(value['input']['observations'],ctx)
        value['cost_acknowledgments']=expected_acknowledgments(ctx['measured_conclusion'])
        issues=assess_entry(value)['issues']
        self.assertEqual(len(issues),1)
        self.assertEqual(issues[0]['refs'],['j:S1_parking'])

    def test_shared_pass_fail_claim_does_not_hide_a_passing_single_measure(self):
        from traffic_agent.executed_facts import status_issues
        rows=[dict(ref='j:S1_parking',run='S1_parking',roads=['甲路'],constraint_passed=True),
              dict(ref='j:S3_combined',run='S3_combined',roads=['甲路'],constraint_passed=False)]
        for text in ('组合相对停车无协同增益（同为失败）','S1与S3均通过','甲路停车和组合均未通过'):
            with self.subTest(text=text):
                issue=status_issues(text,'summary',rows,'j')
                self.assertEqual(len(issue),1)
                self.assertEqual(issue[0]['code'],'measured_constraint_status')
        for text in ('组合和停车并非同为失败','如果组合和停车均失败，则保留基准','甲路停车通过，组合未通过'):
            with self.subTest(text=text):self.assertEqual(status_issues(text,'summary',rows,'j'),[])
        other=[dict(r,ref=r['ref'].replace('j:','other:'),roads=['乙路'],constraint_passed=False) for r in rows]
        self.assertEqual(status_issues('乙路停车和组合同为失败','programme_summary',rows+other),[])
        self.assertEqual(status_issues('停车和组合同为失败','programme_summary',rows+other),[])

    def test_shared_status_assertion_does_not_absorb_another_clause(self):
        from traffic_agent.executed_facts import status_issues
        rows=[dict(ref='j:'+name,run=name,roads=['甲路'],constraint_passed=name=='S1_parking')
              for name in ('S1_parking','S2_oneway','S3_combined')]
        for text in ('停车单项通过，单向与组合均未通过约束。',
                     'S1通过,S2与S3均未通过。', '甲路停车通过，单向及组合均失败。'):
            with self.subTest(text=text):self.assertEqual(status_issues(text,'summary',rows,'j'),[])
        self.assertEqual(len(status_issues('停车单项通过，停车与组合同为失败。','summary',rows,'j')),1)

    def test_wrong_level_enters_the_same_bounded_final_correction_without_native_work(self):
        calls=[]
        def choose(instructions,inputs,tools,choice):
            payload=json.loads(inputs[0]['content']);calls.append(payload)
            response=reply('stop',['j:S1_parking'],payload)
            value=json.loads(response['calls'][0]['arguments'])
            value['summary']='S1停车1级通过。' if len(calls)==1 else 'S1停车2级通过；仍有微小的邻区耗时正增加。'
            response['calls'][0]['arguments']=json.dumps(value)
            return response
        session=PlanningSession(Mock(request=Mock(side_effect=choose)),'离线结论核对')
        result=session.decide('route','j',[measured()],{},dict(programme_final_group=True,
            reserved_model_requests=0,programme_evidence=dict(groups=[dict(execution_complete=True)],findings=[])))
        self.assertEqual(result['status'],'accepted',result.get('validation'))
        self.assertEqual(len(calls),2)
        self.assertEqual(result['review_correction']['first_attempt']['validation']['code'],'measured_conclusion')
        self.assertEqual(calls[0]['observations'],calls[1]['observations'])
        self.assertEqual(calls[0]['context'],calls[1]['context'])
        verify_review_permissions(result)


if __name__=='__main__':unittest.main()
