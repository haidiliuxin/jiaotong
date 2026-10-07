"""Offline checks for verified prerequisites; no model/native calls."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from traffic_agent.llm_client import ModelError
from traffic_agent.natural_plan import compose,limits
from traffic_agent.plan_compiler import requirements
from traffic_agent.planning_scope import decision_permissions,execution_scope,validate_questions
from traffic_agent.planning_attempt import PlanningAttempt,listing
from scripts.validate_observed_planning import REGIONAL_REQUEST
from test_natural_plan import CATALOG,model_reply,experiment

CAT=dict(CATALOG,matched_roads=[],targets={})


def question(quote,need='unresolved_location'):
    return dict(need=need,source_quote=quote,question='请提供要研究的具体道路或路口。')


class PlanningScopeTests(unittest.TestCase):
    def test_empty_topology_references_do_not_mean_no_automatic_targets(self):
        scope=execution_scope(limits(REGIONAL_REQUEST),CAT,requirements(REGIONAL_REQUEST,[],False))
        self.assertEqual(scope['search_scope'],'whole_dataset')
        for strategy in ('parking','oneway','diagnosis'):
            self.assertFalse(scope['target_resolution'][strategy]['road_name_required'])
            self.assertEqual(scope['target_resolution'][strategy]['target_ref'],'auto')
        self.assertEqual(scope['target_resolution']['turn']['available_refs'],[])
        self.assertEqual(scope['candidate_availability'],'unknown_until_new_baseline')
        self.assertEqual(scope['candidate_limit']['max_changed_directed_links'],1)
        self.assertEqual(scope['candidate_limit']['applies_to'],'each_independent_candidate')
        self.assertEqual(scope['regional_design']['regions_per_parking_group'],2)
        self.assertEqual(scope['parking_cost']['minimum_total_runs'],3)

    def test_scope_facts_sent_to_model_match_the_compiled_parking_design(self):
        before=copy.deepcopy(CAT)
        client=Mock(request=Mock(return_value=model_reply([experiment(['parking'])])))
        plan=compose(REGIONAL_REQUEST,Path('source'),client,CAT)
        sent=json.loads(client.request.call_args.args[1][0]['content'])
        self.assertEqual(sent['execution_scope'],plan['execution_scope'])
        self.assertEqual(sent['execution_scope']['parking_cost']['minimum_total_runs'],plan['allocated_runs'])
        self.assertEqual(plan['task']['comparison_regions'],2)
        self.assertEqual(plan['task']['max_links'],1)
        self.assertEqual(CAT,before)
        client.request.assert_called_once()

    def test_scope_counts_remain_conditional_for_named_roads_and_demands(self):
        text='长江村路与红景路比较停车强度；需求系数：0.9,1,1.1；最多20次仿真'
        cat=dict(CATALOG,matched_roads=['长江村路','红景路'])
        scope=execution_scope(limits(text),cat,requirements(text,cat['matched_roads'],False))
        self.assertEqual(scope['search_scope'],'named_roads')
        self.assertEqual(scope['regional_design']['regions_per_parking_group'],1)
        self.assertEqual(scope['parking_cost']['minimum_runs_per_group'],10)
        self.assertEqual(scope['parking_cost']['minimum_total_runs'],20)
        self.assertEqual(scope['target_resolution']['turn']['available_refs'],['T_1_2_3'])

    def test_unnecessary_road_questions_are_validation_failures_not_user_blockers(self):
        # Typed adaptations of the real erroneous premise, not a replay of an
        # unsaved raw provider response. Do not claim the v2 wire was live tested.
        for quote in ['最多治理1条','两个不同区域','新基准']:
            with self.subTest(quote=quote),tempfile.TemporaryDirectory() as tmp:
                response=model_reply([],missing_information=[question(quote)])
                client=Mock(request=Mock(return_value=response))
                attempt=PlanningAttempt(client,Path(tmp),REGIONAL_REQUEST)
                with self.assertRaisesRegex(ModelError,'定位缺口') as error:
                    compose(REGIONAL_REQUEST,Path('source'),attempt,CAT)
                attempt.finish('validation_failed',error.exception)
                saved=listing(Path(tmp))[0]
                self.assertEqual(saved['status'],'validation_failed')
                self.assertEqual(saved['proposal']['question_citations'][0]['source_quote'],quote)
                self.assertEqual(saved['usage']['tokens'],100)
                self.assertFalse(attempt.record['native_started'])
                client.request.assert_called_once()

    def test_concrete_unresolved_locations_remain_questions(self):
        for quote in ['学校门口','新春路','长江村路东向']:
            text=f'请研究{quote}的停车干扰'
            client=Mock(request=Mock(return_value=model_reply([],missing_information=[question(text)])))
            result=compose(text,Path('source'),client,CAT if quote!='长江村路东向' else CATALOG)
            self.assertEqual(result['status'],'needs_input')
            self.assertEqual(result['experiments'],[])

    def test_singular_reference_to_one_matched_road_is_not_a_missing_location(self):
        request='请研究长江村路停车拥堵。先在该道路找最值得治理的方向；需求系数0.9和1.0。'
        permission=decision_permissions(request,CATALOG,{})
        self.assertEqual(permission['clarification_needs'],[])
        self.assertEqual(permission['intents'],['execute'])
        with self.assertRaisesRegex(ModelError,'定位缺口'):
            validate_questions([question('先在该道路找最值得治理的方向')],request,CATALOG,{})
        for catalog in (CAT,dict(CATALOG,matched_roads=['长江村路','红景路'])):
            self.assertIn('unresolved_location',decision_permissions(request,catalog,{})['clarification_needs'])

    def test_known_roads_missing_quotes_and_resolved_nodes_cannot_be_reasked(self):
        for q,text,catalog,explicit in [
                (question('长江村路'),'研究长江村路停车',CATALOG,{}),
                (question('不存在的引用'),'研究案例道路停车',CAT,{}),
                (question('学校门口'),'研究学校门口停车',CAT,dict(links=[[1,2]])),
                (question('全网'),'研究全网拥堵',CAT,{})]:
            with self.subTest(q=q),self.assertRaises(ModelError):validate_questions([q],text,catalog,explicit)

    def test_turn_gap_requires_requested_turn_and_unavailable_topology(self):
        q=question('想在合适路口禁转','turn_target')
        validate_questions([q],'想在合适路口禁转',CAT,{})
        for quote,text,catalog in [('禁转','请禁转',CATALOG),('停车','研究停车',CAT),
                                   ('不安排单向或禁转','不安排单向或禁转',CAT),
                                   ('禁转','不安排单向或禁转',CAT)]:
            with self.subTest(text=text),self.assertRaises(ModelError):
                validate_questions([question(quote,'turn_target')],text,catalog,{})

    def test_budget_blocker_is_calculated_from_the_proposal_not_a_model_question(self):
        text=REGIONAL_REQUEST.replace('最多3次仿真','最多2次仿真')
        client=Mock(request=Mock(return_value=model_reply([experiment(['parking'])])))
        result=compose(text,Path('source'),client,CAT)
        self.assertEqual(result['status'],'needs_input')
        self.assertIn('至少需要3次仿真',result['missing'][0])
        self.assertEqual(result['experiments'],[])
        client.request.assert_called_once()


if __name__=='__main__':unittest.main()
