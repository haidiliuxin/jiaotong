"""Named study bindings, immutable rendered questions and real transport adapters.

All provider responses are synthetic; no credentials, API or native run.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from traffic_agent import question_authoring as qa,hypothesis_contract as hc,natural_plan as np
from traffic_agent.question_wording import VERSION as WORDING_VERSION
from traffic_agent.llm_client import ChatCompletionsClient,ResponsesClient,ModelError
from traffic_agent.llm_settings import DEFAULTS
from traffic_agent.planning_contract import canonical_proposal
from traffic_agent.planning_attempt import safe_proposal,listing
from traffic_agent.initial_planner import reviewed_compose,verify_initial,evidence
from traffic_agent.feedback_planner import digest
from traffic_agent.runner import now
from test_natural_plan import experiment,model_reply,CATALOG
from test_planning_contract import response

REQUEST='长江村路停车拥堵。请至少比较两个不同停车备选方案，并检验单向组织及其组合的独立效果。最多7次仿真，不复跑基准；保护邻区。'


def question(test='core_time_decrease',keys=None,policies='parking',quantifier='any'):
    return dict(definition=dict(test=test,study_keys=keys or ['parking_options'],policies=policies,quantifier=quantifier))


def wire_value():
    studies=[dict(experiment(['parking']),key='parking_options'),dict(experiment(['parking','oneway']),key='direction_comparison')]
    questions=[question(),question('core_advantage_over_parking',['direction_comparison'],'control_and_combined'),
               question('neighbor_limits',['parking_options','direction_comparison'],'all','all')]
    return json.loads(model_reply(studies,assessment=questions)['calls'][0]['arguments'])


class QuestionAuthoringTests(unittest.TestCase):
    def assert_policy_constraints(self,schema):
        expected=dict(core_advantage_over_parking=['all','traffic_control','combined','control_and_combined'],
                      combination_core_advantage=['combined'],
                      control_response=['all','traffic_control','combined','control_and_combined'])
        self.assertEqual({c['if']['properties']['test']['const']:c['then']['properties']['policies']['enum']
                          for c in schema.get('allOf',[])},expected)
        return expected

    def test_wire_and_local_checks_agree_on_every_metric_policy_pair(self):
        expected=self.assert_policy_constraints(qa.DEFINITION_SCHEMA)
        self.assert_policy_constraints(hc.SCHEMA)
        for test in hc.TESTS:
            if test=='unavailable':continue
            for family in hc.POLICIES:
                with self.subTest(test=test,policy=family):
                    definition=question(test,policies=family)['definition'];before=copy.deepcopy(definition)
                    if family in expected.get(test,list(hc.POLICIES)):
                        qa.validate_definition(definition)
                    else:
                        with self.assertRaises(ModelError):qa.validate_definition(definition)
                    self.assertEqual(definition,before)

    def test_names_keep_the_same_study_when_model_reorders_its_design(self):
        value=wire_value();before=copy.deepcopy(value)
        result=canonical_proposal(value)
        self.assertEqual(result['assessment'][1]['criterion']['studies'],[2])
        value['decision']['experiments'].reverse()
        reordered=canonical_proposal(value)
        self.assertEqual(reordered['assessment'][1]['criterion']['studies'],[1])
        self.assertEqual(reordered['experiments'][0]['strategies'],['parking','oneway'])
        self.assertEqual(before['decision']['assessment'],value['decision']['assessment'])
        self.assertNotIn('key',result['experiments'][0])

    def test_projection_never_changes_raw_definition_and_detects_tampering(self):
        definition=question()['definition'];before=copy.deepcopy(definition)
        row=qa.render(definition,dict(parking_options=1))
        self.assertEqual(definition,before);qa.verify_row(row)
        for field,value in (('hypothesis','所有措施已有效'),('evidence_needed','只看其他基准'),
                            ('criterion',dict(row['criterion'],quantifier='all'))):
            changed=copy.deepcopy(row);changed[field]=value
            with self.subTest(field=field),self.assertRaises(ModelError):qa.verify_row(changed)
        changed=copy.deepcopy(row);changed['authoring']['study_keys']['parking_options']=2
        with self.assertRaises(ModelError):qa.verify_row(changed)

    def test_every_supported_metric_policy_quantifier_renders_a_consistent_question(self):
        groups=[dict(source_studies=[1],task=dict(scenario='oneway',road_name='甲路',demand_factors=[.9,1.0],
            max_neighbor_time_increase_pct=.5,max_new_oversaturated_km=.1))]
        for test in hc.TESTS:
            if test=='unavailable':continue
            for family in hc.POLICY_OPTIONS[test]:
                for quantifier in ('any','all'):
                    with self.subTest(test=test,family=family,quantifier=quantifier):
                        row=qa.render(question(test,['mixed'],family,quantifier)['definition'],dict(mixed=1))
                        bound=hc.bind([row],groups,{},wording_protocol=WORDING_VERSION)[0]
                        self.assertEqual(bound['criterion']['quantifier'],quantifier)
                        self.assertEqual(bound['evaluation']['interpretation']['origin'],'model_structured_definition')
                        if test=='neighbor_limits':
                            self.assertEqual(bound['evaluation']['scope'][0]['max_new_oversaturated_km'],.1)

    def test_unknown_duplicate_or_missing_names_are_rejected_without_guessing(self):
        for kind in ('unknown','duplicate','missing'):
            value=wire_value()
            if kind=='unknown':value['decision']['assessment'][0]['definition']['study_keys']=['almost_parking_options']
            elif kind=='duplicate':value['decision']['experiments'][1]['key']='parking_options'
            else:value['decision']['experiments'][0].pop('key')
            before=copy.deepcopy(value)
            with self.subTest(kind=kind),self.assertRaises(ModelError):canonical_proposal(value)
            self.assertEqual(value,before)

    def test_incompatible_study_is_rejected_not_moved_to_a_convenient_group(self):
        row=qa.render(question('core_advantage_over_parking',policies='control_and_combined')['definition'],dict(parking_options=1))
        groups=[dict(source_studies=[1],task=dict(scenario='parking',road_name='甲路',demand_factors=[1]))]
        with self.assertRaisesRegex(ModelError,'不在该研究组'):hc.bind([row],groups,{},wording_protocol=WORDING_VERSION)
        self.assertEqual(row['criterion']['studies'],[1])

    def test_free_question_is_preserved_and_not_replaced_by_a_measurement(self):
        value=wire_value()
        original=dict(hypothesis='当前停车干扰是拥堵直接成因之一。',evidence_needed='核心耗时对照',
            criterion=dict(test='core_time_decrease',studies=[1],policies='parking',quantifier='any'))
        value['decision']['assessment']=[original]
        result=canonical_proposal(value)
        self.assertEqual(result['assessment'],[original]);self.assertNotIn('authoring',result['assessment'][0])
        groups=[dict(source_studies=[1],task=dict(scenario='parking',road_name='甲路',demand_factors=[1]))]
        with self.assertRaisesRegex(ModelError,'成因'):hc.bind(result['assessment'],groups,{},wording_protocol=WORDING_VERSION)
        original['criterion']['test']='unavailable'
        self.assertEqual(hc.bind([original],groups,{},wording_protocol=WORDING_VERSION)[0]['hypothesis'],original['hypothesis'])

    def test_mixed_definition_and_prose_does_not_discard_the_prose_to_pass(self):
        value=wire_value();value['decision']['assessment'][0]['hypothesis']='所有措施一定有效'
        with self.assertRaises(ModelError):canonical_proposal(value)
        saved=safe_proposal(value)['assessment'][0]
        self.assertEqual(saved['hypothesis'],'所有措施一定有效')
        self.assertEqual(saved['criterion_status'],'invalid')
        self.assertEqual(saved['definition'],value['decision']['assessment'][0]['definition'])

    def test_unsupported_question_cannot_be_generated_without_its_original_words(self):
        value=wire_value();value['decision']['assessment'][0]['definition']['test']='unavailable'
        with self.assertRaisesRegex(ModelError,'不可测'):canonical_proposal(value)
        for test,policies in (('core_advantage_over_parking','parking'),('control_response','parking'),('combination_core_advantage','all')):
            bad=wire_value();bad['decision']['assessment'][0]=question(test,policies=policies)
            with self.subTest(test=test),self.assertRaises(ModelError):canonical_proposal(bad)

    def test_saved_named_sources_rebind_and_reject_reordered_source_records(self):
        saved=safe_proposal(wire_value());qa.verify_study_links(saved)
        self.assertEqual(safe_proposal(saved),saved)
        changed=copy.deepcopy(saved);changed['studies'].reverse()
        with self.assertRaises(ModelError):qa.verify_study_links(changed)
        self.assertEqual(saved['assessment'][1]['authoring']['definition']['study_keys'],['direction_comparison'])

    def test_both_real_transport_adapters_preserve_sources_and_full_initial_verification(self):
        for wire,cls in (('chat_completions',ChatCompletionsClient),('responses',ResponsesClient)):
            with self.subTest(wire=wire),tempfile.TemporaryDirectory() as temp:
                opener=Mock();opener.open.return_value=response(wire_value(),wire)
                client=cls(dict(DEFAULTS,wire_api=wire),'UNIT_TEST_ONLY',opener)
                with patch('traffic_agent.natural_plan.context_for',return_value=CATALOG):
                    result=reviewed_compose(REQUEST,Path('source'),client,Path(temp),np.compose)
                self.assertEqual(result['status'],'ready');self.assertEqual(result['allocated_runs'],7)
                self.assertEqual(result['request'],REQUEST)
                self.assertEqual([e['runs'] for e in result['experiments']],[3,4])
                self.assertEqual(result['question_authoring_protocol'],qa.VERSION)
                plan=dict(created=now(),initial_planning_digest=digest(evidence(result)))
                self.assertEqual(verify_initial(result,plan),1)
                saved=result['initial_planning_attempts'][0]['proposal']
                self.assertEqual(saved,listing(Path(temp))[0]['proposal'])
                self.assertEqual(saved['assessment'][1]['authoring']['definition'],wire_value()['decision']['assessment'][1]['definition'])
                opener.open.assert_called_once()
                body=json.loads(opener.open.call_args.args[0].data)
                sent=body['tools'][0]
                tool=sent['function'] if wire=='chat_completions' else sent
                execute=next(d for d in tool['parameters']['properties']['decision']['anyOf']
                             if d['properties']['intent']['enum']==['execute'])
                variants=execute['properties']['assessment']['items']['anyOf']
                self.assert_policy_constraints(variants[0]['properties']['definition'])
                self.assert_policy_constraints(variants[1]['properties']['criterion'])
                damaged=copy.deepcopy(result);damaged['initial_planning_attempts'][0]['proposal']['studies'].reverse()
                with self.assertRaisesRegex(ValueError,'具名'):
                    verify_initial(damaged,dict(plan,initial_planning_digest=digest(evidence(damaged))))
                for p in (Path(temp)/'planning_attempts').glob('*.json'):
                    self.assertNotIn('UNIT_TEST_ONLY',p.read_text('utf-8'))
                    self.assertNotIn('PRIVATE_HIDDEN_REASONING',p.read_text('utf-8'))

    def test_invalid_combination_definition_is_preserved_without_retry_or_launch(self):
        value=wire_value()
        bad=question('combination_core_advantage',['direction_comparison'],'control_and_combined')
        value['decision']['assessment'].append(bad);before=copy.deepcopy(value)
        for wire,cls in (('chat_completions',ChatCompletionsClient),('responses',ResponsesClient)):
            with self.subTest(wire=wire),tempfile.TemporaryDirectory() as temp:
                opener=Mock();opener.open.return_value=response(value,wire)
                client=cls(dict(DEFAULTS,wire_api=wire),'UNIT_TEST_ONLY',opener)
                with patch('traffic_agent.natural_plan.context_for',return_value=CATALOG):
                    with self.assertRaisesRegex(ModelError,'组合增益判据只能指向组合措施'):
                        reviewed_compose(REQUEST,Path('source'),client,Path(temp),np.compose)
                opener.open.assert_called_once()
                attempts=listing(Path(temp));self.assertEqual(len(attempts),1)
                record=json.loads((Path(temp)/'planning_attempts'/(attempts[0]['id']+'.json')).read_text('utf-8'))
                self.assertEqual(record['status'],'validation_failed')
                self.assertFalse(record['native_started']);self.assertEqual(record['request'],REQUEST)
                saved=record['proposal']['assessment'][-1]
                self.assertEqual(saved,dict(bad,criterion_status='invalid'))
                self.assertEqual(attempts[0]['proposal']['assessment'][-1],saved)
                self.assertEqual(value,before)


if __name__=='__main__':unittest.main()
