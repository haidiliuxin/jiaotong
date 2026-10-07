"""Offline checks, including the RC12 scope-mixing regression (no API calls)."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from traffic_agent.conclusion_review import assess_entry, contract, POLICY_VERSION, POLICY_SCOPE
from traffic_agent.executed_facts import level_issues
from traffic_agent.feedback_planner import PlanningSession
from traffic_agent.measured_numbers import issues, paired_facts
from test_feedback_planner import reply


def observations():
    # These four values reproduce the independently measured RC12 mismatch.
    return [dict(ref='a:baseline', run='baseline', demand_factor=1, changes=[],
                 metrics=dict(core=dict(running_time_pcu_h=345.4695328775985))),
            dict(ref='a:parking_1', run='parking_1', demand_factor=1,
                 changes=[dict(from_node=3245, to_node=3244, parking=1)],
                 core_roads=[dict(name='七东线', from_node=3245, to_node=3244)],
                 metrics=dict(core=dict(running_time_pcu_h=296.93266871975317)),
                 regional_checks=[dict(region='area_1', before=dict(running_time_pcu_h=337.846423180212),
                                       after=dict(running_time_pcu_h=286.6741932129964))])]


def proposal(text):
    return dict(summary=text, evidence_refs=['a:parking_1'])


class MeasuredNumberTests(unittest.TestCase):
    def test_actual_rc12_scope_mismatch_is_not_certified_or_rewritten(self):
        value=proposal('七东线停车1级核心耗时降14.05%（337.8→286.7 pcu·h），但area_2耗时增34.6%。')
        entry=dict(status='accepted', action='study_2', **value, input=dict(observations=observations(), context={}))
        before=copy.deepcopy(entry)
        result=assess_entry(entry)
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['issues'][0]['code'], 'measured_time_scope')
        self.assertIn('345.470→296.933', result['issues'][0]['message'])
        self.assertEqual(entry,before)

    def test_same_scope_absolute_and_percent_values_allow_rounding(self):
        for text in ('七东线核心耗时降14.05%（345.5→296.9 pcu·h）。',
                     '核心范围耗时从345降至297 pcu·h。',
                     'area_1耗时降15.15%（337.85→286.67 pcu·h）。'):
            self.assertEqual(issues(proposal(text),observations(),{}),[],text)

    def test_correct_pair_with_wrong_percentage_is_rejected(self):
        result=issues(proposal('核心耗时降15.15%（345.47→296.93 pcu·h）。'),observations(),{})
        self.assertEqual([r['code'] for r in result],['measured_time_arithmetic'])

    def test_missing_or_other_demand_base_is_unknown_not_zero(self):
        obs=observations();obs[0]['demand_factor']=1.1
        facts=paired_facts(obs)
        self.assertEqual([r['scope'] for r in facts],['area_1'])
        self.assertEqual(issues(proposal('核心耗时337.8→286.7 pcu·h。'),obs,{}),[])
        obs[0]['demand_factor']=1;obs[0]['ref']='other:baseline'
        self.assertEqual([r['scope'] for r in paired_facts(obs)],['area_1'])

    def test_future_conditional_and_quoted_correction_are_not_executed_facts(self):
        for text in ('预计核心耗时从337.8降至286.7 pcu·h。',
                     '如果核心耗时337.8→286.7 pcu·h，尚需验证。',
                     '原解释错写核心耗时337.8→286.7 pcu·h。'):
            self.assertEqual(issues(proposal(text),observations(),{}),[],text)

    def test_other_group_cannot_inherit_current_pair(self):
        context=dict(programme_cost_facts=[dict(ref='b:parking_1',run='parking_1',roads=['长江村路'])])
        value=proposal('长江村路核心耗时100→80 pcu·h。')
        self.assertEqual(issues(value,observations(),context),[])

    def test_direct_policy_contrast_does_not_inherit_baseline(self):
        obs=observations();second=copy.deepcopy(obs[-1]);second.update(ref='a:parking_2',run='parking_2')
        second['metrics']['core']['running_time_pcu_h']=280;obs.append(second)
        value=proposal('parking_1与parking_2相比，核心耗时降5.70%（296.93→280.00 pcu·h）。')
        value['evidence_refs']=['a:parking_1','a:parking_2']
        self.assertEqual(issues(value,obs,{}),[])

    def test_thousands_separators_and_scope_do_not_split_numbers(self):
        obs=observations()
        obs[0]['metrics']['neighbor']=dict(running_time_pcu_h=2278.934365826983)
        obs[1]['metrics']['neighbor']=dict(running_time_pcu_h=2266.8780772505334)
        value=proposal('邻区耗时降0.529%（2,278.934→2,266.878 pcu·h）。')
        self.assertEqual(issues(value,obs,{}),[])

    def test_v2_frozen_contract_keeps_original_scope_and_policy_facts(self):
        context=dict(measured_conclusion=dict(version=POLICY_VERSION))
        result=contract(observations(),context)
        self.assertEqual(result['version'],POLICY_VERSION)
        self.assertEqual(result['scope'],POLICY_SCOPE)
        self.assertEqual(result['executed_policies'][0]['parking_levels'],[1])

    def test_local_unmeasured_group_does_not_inherit_previous_action_level(self):
        rows=[dict(ref='a:parking_1',run='parking_1',roads=['七东线'],parking_levels=[1])]
        self.assertEqual(level_issues('本组停车2级。','summary',rows,local_job='b'),[])
        self.assertTrue(level_issues('七东线已执行停车2级。','summary',rows,local_job='b'))
        self.assertEqual(level_issues('七东线已执行停车1级，长江村路停车2级。','summary',rows,
                                     local_job='b',known_roads=['七东线','长江村路']),[])
        self.assertEqual(level_issues('测停车2级、单向与组合。','next_check',rows),[])
        self.assertTrue(level_issues('已测停车2级，结果有效。','summary',rows))

    def test_real_controller_accepts_future_target_without_relabeling_previous_result(self):
        from test_conclusion_review import observation
        prior=observation(ref='a:parking_1',road='七东线')
        from traffic_agent.conclusion_review import cost_facts
        from traffic_agent.executed_facts import policy_facts
        context=dict(programme_current_job='b',programme_cost_facts=cost_facts([prior]),
                     programme_policy_facts=policy_facts([prior]))
        current=dict(ref='b:baseline',run='baseline',changes=[],demand_factor=1,
                     core_roads=[dict(name='长江村路',from_node=3237,to_node=3236)],metrics={})
        def choose(instructions,inputs,tools,choice):
            payload=json.loads(inputs[0]['content']);response=reply('oneway_3237_3236',['b:baseline'],payload)
            args=json.loads(response['calls'][0]['arguments'])
            args['next_check']='选择长江村路3237→3236开启单向对照，测停车2级、单向、组合四项同需求结果。'
            response['calls'][0]['arguments']=json.dumps(args);return response
        client=Mock(request=Mock(side_effect=choose))
        session=PlanningSession(client,'两条道路独立研究')
        result=session.decide('oneway_target','b',[current],{'oneway_3237_3236':dict(title='长江村路单向')},context=context)
        self.assertEqual(result['status'],'accepted')
        self.assertEqual(result['action'],'oneway_3237_3236')
        self.assertEqual(client.request.call_count,1)
        self.assertFalse(session.halted)

    def test_read_view_recomputes_completion_without_rewriting_original_review(self):
        from test_planning_completion import CompletionTests
        from traffic_agent.dispatch import Dispatcher, evidence_review
        helper=CompletionTests();self.addCleanup(helper.doCleanups)
        agent,session,payloads,diagnosis=helper.exercise(hypotheses=True)
        original_review=copy.deepcopy(evidence_review(agent.state))
        self.assertTrue(original_review['goal_review']['complete'])
        state=copy.deepcopy(agent.state)
        state['planner_trace'][-1]['summary']='已执行停车0级，实测通过。'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state_path=root/'jobs'/state['id']/'state.json';state_path.parent.mkdir(parents=True)
            dispatch_path=root/'dispatches'/f"{state['id']}.json";dispatch_path.parent.mkdir()
            state_path.write_text(json.dumps(state),encoding='utf-8')
            dispatch_path.write_text(json.dumps(dict(job_id=state['id'],status='completed',planning_trace=state['planner_trace'],
                progress=[dict(job_id=state['id'],status='completed',review=original_review)])),encoding='utf-8')
            before={p:p.read_bytes() for p in (state_path,dispatch_path)}
            view=Dispatcher(root,root,Mock()).listing()[0]
            review=view['progress'][0]['review']
            self.assertEqual(view['status'],'incomplete')
            self.assertFalse(review['goal_review']['complete'])
            self.assertFalse(next(r for r in review['goal_review']['requirements'] if r['id']=='conclusion')['fulfilled'])
            self.assertTrue(review['recorded_goal_review']['complete'])
            self.assertFalse(review['research_review']['reviewed'])
            self.assertTrue(review['research_review']['recorded_reviewed'])
            self.assertTrue(all(p.read_bytes()==data for p,data in before.items()))


if __name__ == '__main__':
    unittest.main()
