import unittest
from unittest.mock import Mock

from traffic_agent.feedback_planner import PlanningSession
from traffic_agent.planning_budget import (VERSION,LEGACY_VERSION,starting_with,minimum_remaining,
                                           reserved_after_current,route_guard)
from traffic_agent.study_handoff import contract,closes_study
from test_planning_response import inputs,decision,response


class HandoffTests(unittest.TestCase):
    def test_old_budgets_remain_reproducible_while_new_groups_share_review_and_route(self):
        parking=dict(scenario='parking',request='只比较一个措施')
        oneway=dict(scenario='oneway')
        self.assertEqual(starting_with([parking,oneway],version=LEGACY_VERSION),5)
        self.assertEqual(starting_with([oneway,parking],version=LEGACY_VERSION),4)
        self.assertEqual(starting_with([parking,oneway]),4)
        self.assertEqual(starting_with([oneway,parking]),4)
        self.assertEqual(minimum_remaining([parking,parking],LEGACY_VERSION),5)
        self.assertEqual(minimum_remaining([parking,parking]),4)
        self.assertEqual(reserved_after_current(parking,[oneway],LEGACY_VERSION),3)
        self.assertEqual(reserved_after_current(parking,[oneway]),2)
        guard=route_guard([dict(id='study_2',title='单向研究',task=oneway)],2)
        self.assertFalse(guard['choices']['study_2']['feasible'])
        self.assertEqual(guard['version'],VERSION)

    def exercise(self,action,optional=False,omit_review=False):
        observations,context=inputs()
        options={'study_2':dict(title='另一组的新基准与对照')}
        context['study_handoff']=contract(options)
        if optional:options['parking_more']=dict(title='继续当前组试验',changes=[dict(parking=2)])
        value=decision();value['action']=action
        if omit_review:value.update(hypothesis_updates=[],cost_acknowledgments=[])
        client=Mock(request=Mock(return_value=response(value)))
        entry=PlanningSession(client,'组间衔接检查',risk_review_protocol=None).decide('parking','j',observations,options,context,allow_stop=False)
        return entry,client

    def test_only_handoff_menu_requires_full_review_and_does_not_open_stop(self):
        entry,client=self.exercise('study_2')
        self.assertEqual(entry['status'],'accepted')
        self.assertTrue(closes_study(entry['action'],entry['input']['context']))
        props=client.request.call_args.args[2][0]['parameters']['properties']
        self.assertEqual(props['action']['enum'],['study_2'])
        self.assertEqual(props['hypothesis_updates']['minItems'],2)
        self.assertEqual(props['cost_acknowledgments']['minItems'],1)
        self.assertNotIn('changes',entry['selected'])
        client.request.assert_called_once()

    def test_optional_local_experiment_remains_a_model_choice_but_handoff_must_review(self):
        entry,_=self.exercise('parking_more',optional=True,omit_review=True)
        self.assertEqual(entry['status'],'accepted')
        self.assertFalse(closes_study(entry['action'],entry['input']['context']))
        entry,_=self.exercise('study_2',optional=True,omit_review=True)
        self.assertEqual(entry['status'],'rejected')
        self.assertEqual(entry['validation']['code'],'hypothesis_coverage')
        self.assertNotIn('selected',entry)

    def test_stop_cannot_skip_the_remaining_required_study(self):
        entry,client=self.exercise('stop')
        self.assertEqual(entry['validation']['code'],'action_not_allowed')
        self.assertNotIn('selected',entry)
        client.request.assert_called_once()


if __name__=='__main__':unittest.main()
