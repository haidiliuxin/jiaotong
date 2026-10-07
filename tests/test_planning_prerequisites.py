"""Only unresolved location conditions expose a clarification decision.

All replies are offline fixtures. No successful live planning is asserted.
"""
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from traffic_agent.initial_planner import evidence,verify_initial
from traffic_agent.feedback_planner import digest
from traffic_agent.llm_client import ChatCompletionsClient,ResponsesClient,ModelError
from traffic_agent.llm_settings import DEFAULTS
from traffic_agent.natural_plan import compose,limits,EXPERIMENT,ASSESSMENT,ALTERNATIVE,UNSUPPORTED
from traffic_agent.planning_contract import tool_for
from traffic_agent.planning_scope import decision_permissions,validate_questions,location_anchors
from traffic_agent.runner import now
from scripts.validate_observed_planning import REGIONAL_REQUEST,REQUEST,PROGRAMME_REQUEST
from test_natural_plan import CATALOG,experiment,model_reply
from test_planning_contract import response
import test_initial_planner as initial_fixture

CAT=dict(CATALOG,matched_roads=[],targets={})


def branches(tool):
    return {b['properties']['intent']['enum'][0]:b for b in tool['parameters']['properties']['decision']['anyOf']}


class PlanningPrerequisiteTests(unittest.TestCase):
    def test_actual_request_only_opens_applicable_decisions_without_fixed_research(self):
        for text in [REGIONAL_REQUEST,REQUEST,'比较案例道路停车强度与单向措施，检查周边影响',
                     '长江村路和红景路停车干扰，检查周边耗时和周边代价']:
            catalog=dict(CAT,matched_roads=[n for n in CATALOG['road_names'] if n in text])
            gate=decision_permissions(text,catalog,limits(text)['explicit'])
            self.assertEqual(gate['clarification_needs'],[])
            tool=tool_for(EXPERIMENT,ASSESSMENT,ALTERNATIVE,UNSUPPORTED,gate['clarification_needs'],
                          gate['clarification_quotes'],gate['unsupported_capabilities'])
            allowed=branches(tool)
            self.assertEqual(list(allowed),['execute'])
            self.assertEqual(allowed['execute']['properties']['experiments']['items'],EXPERIMENT)
            self.assertEqual(allowed['execute']['properties']['assessment']['items'],ASSESSMENT)
            self.assertEqual(allowed['execute']['properties']['alternatives']['items'],ALTERNATIVE)
        signal=decision_permissions('请优化长江村路信号配时',CATALOG,{})
        self.assertEqual(signal['unsupported_capabilities'],['signal'])
        tool=tool_for(EXPERIMENT,ASSESSMENT,ALTERNATIVE,UNSUPPORTED,signal['clarification_needs'],
                      signal['clarification_quotes'],signal['unsupported_capabilities'])
        self.assertEqual(branches(tool)['unsupported']['properties']['capabilities']['items']['enum'],['signal'])

    def test_real_mixed_failure_request_does_not_confuse_two_roads_with_unknown_place(self):
        cat=dict(CAT,matched_roads=['七东线','长江村路'])
        self.assertEqual(location_anchors(PROGRAMME_REQUEST,cat,limits(PROGRAMME_REQUEST)['explicit']),[])
        gate=decision_permissions(PROGRAMME_REQUEST,cat,limits(PROGRAMME_REQUEST)['explicit'])
        self.assertEqual(gate['clarification_quotes'],{})
        self.assertNotIn('needs_input',gate['intents'])
        for reference in ['两条道路','2条路段','各条道路','每条道路','所有道路','全部路段','上述道路','这些路段']:
            text=f'研究七东线与长江村路的停车干扰，分别比较{reference}，读取道路指标与路段状态，检查路侧停车与路权'
            with self.subTest(reference=reference):
                self.assertEqual(decision_permissions(text,cat,{})['clarification_needs'],[])

    def test_questions_are_bound_to_actual_unknown_fragment_even_when_known_roads_exist(self):
        text='先研究长江村路，再比较新春路与已匹配道路，最多治理两条道路'
        cat=dict(CAT,matched_roads=['长江村路'])
        gate=decision_permissions(text,cat,{})
        self.assertEqual(gate['clarification_quotes'],{'unresolved_location':['再比较新春路与已匹配道路']})
        validate_questions([dict(need='unresolved_location',source_quote='再比较新春路与已匹配道路',question='新春路在案例路网中对应哪条道路？')],text,cat,{})
        for quote in ['长江村路','先研究长江村路','最多治理两条道路','新春路']:
            with self.subTest(quote=quote),self.assertRaises(ModelError):
                validate_questions([dict(need='unresolved_location',source_quote=quote,question='请指定长江村路的节点')],text,cat,{})
        # Budget/generic words elsewhere in a genuine location clause are not blockers.
        mixed='在全网中比较长江村路与新春路但预算有限'
        self.assertIn(mixed,decision_permissions(mixed,cat,{})['clarification_quotes']['unresolved_location'])
        validate_questions([dict(need='unresolved_location',source_quote=mixed,question='请定位新春路')],mixed,cat,{})

    def test_bounded_fragments_keep_the_actual_marker_in_long_clauses(self):
        text='研究'*70+'新春路附近禁转'+'并比较结果'*30
        gate=decision_permissions(text,CAT,{})
        for need,quotes in gate['clarification_quotes'].items():
            for quote in quotes:
                self.assertTrue(2<=len(quote)<=100)
                self.assertIn(quote,text)
                if need=='turn_target':self.assertIn('禁转',quote)
                validate_questions([dict(need=need,source_quote=quote,question='请补充具体位置')],text,CAT,{})
        self.assertEqual(decision_permissions('路',CAT,{})['clarification_needs'],[])

    def test_real_unknown_road_relative_position_and_turn_still_open_questions(self):
        cases=[('请研究学校门口拥堵',CAT,['unresolved_location']),
               ('请比较不同区域，重点是新春路',CAT,['unresolved_location']),
               ('请研究长江村路东向停车',CATALOG,['unresolved_location']),
               ('请研究禁转方案',CAT,['turn_target']),
               ('请研究学校门口禁转',CAT,['unresolved_location','turn_target'])]
        for text,cat,needs in cases:
            with self.subTest(text=text):
                gate=decision_permissions(text,cat,limits(text)['explicit'])
                self.assertEqual(gate['clarification_needs'],needs)
                tool=tool_for(EXPERIMENT,ASSESSMENT,ALTERNATIVE,UNSUPPORTED,needs,gate['clarification_quotes'])
                question=branches(tool)['needs_input']['properties']['questions']['items']
                self.assertEqual([q['properties']['need']['enum'][0] for q in question['anyOf']],needs)
                for q in question['anyOf']:
                    need=q['properties']['need']['enum'][0]
                    self.assertEqual(q['properties']['source_quote']['enum'],gate['clarification_quotes'][need])
        self.assertEqual(decision_permissions('不安排单向或禁转',CAT,{})['clarification_needs'],[])
        self.assertEqual(decision_permissions('想禁转',CATALOG,{})['clarification_needs'],[])

    def test_known_names_cannot_be_reasked_by_quoting_only_their_suffix(self):
        text='比较长江村路与红景路，检查周边影响'
        cat=dict(CAT,matched_roads=['长江村路','红景路'])
        for quote in ['江村路','红景路','周边']:
            with self.subTest(quote=quote),self.assertRaises(ModelError):
                validate_questions([dict(need='unresolved_location',source_quote=quote,question='请给出道路')],text,cat,{})
        self.assertEqual(decision_permissions('长江村路东向',cat,dict(links=[[1,2]]))['clarification_needs'],[])

    def test_actual_serialized_tools_match_request_conditions_on_both_wires(self):
        for wire,cls in [('chat_completions',ChatCompletionsClient),('responses',ResponsesClient)]:
            for text,needs in [(REGIONAL_REQUEST,[]),('学校门口停车干扰',['unresolved_location'])]:
                with self.subTest(wire=wire,text=text):
                    value=model_reply([experiment(['parking'])]) if not needs else model_reply([],missing_information=[dict(
                        need='unresolved_location',source_quote=text,question='请明确学校位置')])
                    opener=Mock();opener.open.return_value=response(json.loads(value['calls'][0]['arguments']),wire)
                    result=compose(text,Path('source'),cls(dict(DEFAULTS,wire_api=wire),'UNIT_TEST_ONLY',opener),CAT)
                    body=json.loads(opener.open.call_args.args[0].data)
                    actual=body['tools'][0]['function'] if wire=='chat_completions' else body['tools'][0]
                    self.assertEqual('needs_input' in branches(actual),bool(needs))
                    payload=json.loads((body['messages'][1] if wire=='chat_completions' else body['input'][0])['content'])
                    self.assertEqual(payload['decision_permissions'],result['decision_permissions'])
                    self.assertEqual(payload['decision_permissions']['clarification_needs'],needs)
                    self.assertEqual(result['status'],'ready' if not needs else 'needs_input')
                    self.assertEqual(opener.open.call_count,1)

    def test_schema_ignoring_clarification_is_rejected_without_creating_a_fallback_plan(self):
        helper=initial_fixture.InitialPlannerTests()
        try:
            helper.setup_preview([initial_fixture.wrong_prerequisite(),initial_fixture.wrong_prerequisite()])
            with self.assertRaises(ModelError):helper.preview()
            helper.native.assert_not_called()
            self.assertEqual(helper.opener.open.call_count,2)
            for c in helper.opener.open.call_args_list:
                tool=json.loads(c.args[0].data)['tools'][0]['function']
                self.assertNotIn('needs_input',branches(tool))
            saved=helper.records()
            self.assertTrue(all(r['status']=='validation_failed' for r in saved))
            self.assertTrue(all(r['decision_permissions']['clarification_needs']==[] for r in saved))
        finally:helper.doCleanups()

    def test_permissions_are_frozen_with_the_initial_attempts(self):
        helper=initial_fixture.InitialPlannerTests()
        try:
            helper.setup_preview([model_reply([experiment(['parking'])])])
            result=helper.preview();plan=dict(created=now(),initial_planning_digest=digest(evidence(result)))
            self.assertEqual(verify_initial(result,plan),1)
            changed=json.loads(json.dumps(result))
            changed['initial_planning_attempts'][0]['decision_permissions']['clarification_needs']=['turn_target']
            with self.assertRaises(ValueError):verify_initial(changed,plan)
            with self.assertRaisesRegex(ValueError,'工具权限'):
                verify_initial(changed,dict(plan,initial_planning_digest=digest(evidence(changed))))
        finally:helper.doCleanups()


if __name__=='__main__':unittest.main()
