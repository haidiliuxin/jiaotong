"""Source/region boundaries from RC14; synthetic roads, no provider or native calls."""
import copy
import unittest

from traffic_agent.conclusion_review import VERSION, V13_VERSION, assess_entry, contract
from traffic_agent.executed_facts import parking_source_contract
from test_conclusion_review import observation


def scenes():
    baseline = observation('j:baseline')
    baseline.update(changes=[], delta=None, spatial=dict(regions=[
        dict(id='area_1', directions=[[1,2]], roads=[dict(from_node=1,to_node=2,parking=3)]),
        dict(id='area_2', directions=[[3,4]], roads=[dict(from_node=3,to_node=4,parking=3)])]))
    measured = observation('j:parking_1')
    measured['spatial'] = copy.deepcopy(baseline['spatial'])
    measured['spatial']['regions'][0]['roads'][0]['parking'] = 1
    return [baseline, measured]


def checked(text, version=VERSION, observations=None, proposed=()):
    observations = scenes() if observations is None else observations
    context = dict(programme_current_job='j', measured_conclusion=dict(version=version))
    context['measured_conclusion'] = contract(observations, context)
    turn = dict(status='accepted', action='next', summary=text, reviewed_cost_refs=[],
                input=dict(observations=observations, context=context,
                           allowed_actions=dict(next=dict(changes=list(proposed)))))
    return assess_entry(turn)


class ParkingClaimSourcesTests(unittest.TestCase):
    def test_rc14_untreated_region_state_never_inherits_other_region_action(self):
        text='面积2两条方向为停车3级且VC达2.04-2.33，停车1级可能无法突破既有瓶颈。'
        self.assertEqual(checked(text)['issues'], [])
        old=checked(text,V13_VERSION)['issues']
        self.assertEqual([(i['code'],i['quote']) for i in old],[('executed_parking_level','停车3级')])

    def test_original_baseline_and_executed_level_can_coexist(self):
        for text in ('area_1基准停车3级，已执行停车1级。',
                     '面积1从停车3级降至停车1级。',
                     'j:baseline，area_1停车3级；j:parking_1，area_1停车1级。'):
            self.assertEqual(checked(text)['issues'], [], text)
        self.assertEqual(checked('area_1基准停车2级。')['issues'][0]['fact_source'],'baseline')

    def test_unmodified_roads_in_a_measured_scene_are_still_three(self):
        self.assertEqual(checked('j:parking_1，area_2停车3级。')['issues'], [])
        wrong=checked('j:parking_1，area_2停车1级。')['issues'][0]
        self.assertEqual(wrong['code'],'observed_parking_level')
        self.assertEqual(wrong['refs'],['j:parking_1'])

    def test_explicit_baseline_without_region_uses_observed_state(self):
        for text in ('基准停车3级。','j:baseline，停车3级。'):
            self.assertEqual(checked(text)['issues'],[],text)
        self.assertEqual(checked('基准停车1级。')['issues'][0]['fact_source'],'baseline')

    def test_wrong_executed_level_is_still_rejected_for_each_region_alias(self):
        for region in ('area_1','面积1','区域1','候选区1'):
            wrong=checked(region+'已测停车3级。')['issues'][0]
            self.assertEqual(wrong['code'],'executed_parking_level')
            self.assertEqual(wrong['region_ids'],['area_1'])
            self.assertEqual(wrong['refs'],['j:parking_1'])

    def test_future_selected_action_is_bound_to_menu_and_never_marked_measured(self):
        proposal=[dict(from_node=3,to_node=4,parking=1)]
        text='本轮转面积2两条方向停车1级，构成第二不同区域备选。'
        self.assertEqual(checked(text,proposed=proposal)['issues'], [])
        wrong=checked('面积2已执行停车1级。',proposed=proposal)['issues'][0]
        self.assertEqual(wrong['code'],'unmeasured_parking_action')
        self.assertEqual(checked('面积2为停车1级。',proposed=proposal)['issues'][0]['code'],'observed_parking_level')

    def test_multiple_trials_are_independent_not_a_cumulative_latest_city(self):
        values=scenes()
        second=copy.deepcopy(values[0]);second.update(ref='j:parking_2',run='parking_2',
            changes=[dict(from_node=3,to_node=4,parking=2)])
        second['spatial']['regions'][1]['roads'][0]['parking']=2
        values.append(second)
        self.assertEqual(checked('area_1已测停车1级，area_2已测停车2级。',observations=values)['issues'],[])
        wrong=checked('area_2已测停车1级。',observations=values)['issues'][0]
        self.assertEqual(wrong['refs'],['j:parking_2'])

    def test_unknown_and_conflicting_observed_levels_do_not_inherit_action(self):
        self.assertEqual(checked('面积9为停车3级。')['issues'],[])
        values=scenes();values[-1]['spatial']['regions'][1]['roads'][0]['parking']=2
        self.assertEqual(checked('面积2仍为停车3级。',observations=values)['issues'],[])
        self.assertEqual(checked('j:parking_1，面积2为停车3级。',observations=values)['issues'][0]['code'],'observed_parking_level')

    def test_partial_region_edits_do_not_replace_mixed_observed_state(self):
        values=scenes()
        for o in values:
            o['spatial']['regions'][0]['roads'].append(dict(from_node=2,to_node=1,parking=3))
            o['spatial']['regions'][0]['directions'].append([2,1])
        # The region contains both levels; a generic state claim is ambiguous.
        self.assertEqual(checked('面积1现状为停车3级。',observations=values)['issues'],[])
        self.assertEqual(checked('面积1已执行停车3级。',observations=values)['issues'][0]['code'],'executed_parking_level')

    def test_source_contract_and_observations_are_not_mutated(self):
        values=scenes();before=copy.deepcopy(values)
        facts=contract(values,{})
        self.assertEqual(facts['parking_claim_sources'],parking_source_contract())
        self.assertNotIn('parking_claim_sources',contract(values,dict(measured_conclusion=dict(version=V13_VERSION))))
        checked('面积2为停车3级。',observations=values)
        self.assertEqual(values,before)


if __name__ == '__main__': unittest.main()
