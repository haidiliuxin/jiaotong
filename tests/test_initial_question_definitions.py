"""Initial question references and frozen limits; synthetic, no paid calls."""
import copy
import json
import unittest

from traffic_agent import hypothesis_contract as hc,question_wording as qw,natural_plan as np
from traffic_agent.llm_client import ModelError
from traffic_agent.initial_hypothesis_review import inspect,design
from test_natural_plan import experiment
import test_natural_plan as natural_helpers


def groups():
    return [dict(source_studies=[1],task=dict(scenario='oneway',road_name='甲路',demand_factors=[1],
        max_neighbor_time_increase_pct=.5,max_new_oversaturated_km=.1))]


def question(evidence,test='core_time_decrease',text='至少一个已试措施降低冻结核心总耗时'):
    return dict(hypothesis=text,evidence_needed=evidence,
        criterion=dict(test=test,studies=[1],policies='combined',quantifier='any'))


def bind(row,version=qw.VERSION):return hc.bind([row],groups(),{},wording_protocol=version)[0]


class InitialQuestionDefinitionTests(unittest.TestCase):
    def test_initial_and_frozen_questions_use_the_same_definition(self):
        row=question('本组同需求无治理基准与措施配对')
        result=bind(row);definition=result['evaluation']['definition']
        self.assertEqual({k:v for k,v in definition.items() if k!='version'},hc.QUESTION_DEFINITIONS[row['criterion']['test']])
        self.assertEqual(hc.question_catalog()[row['criterion']['test']]['predicate'],definition['predicate'])
        self.assertIn(definition['predicate'],result['evaluation']['interpretation']['question'])
        self.assertEqual(result['hypothesis'],row['hypothesis'])
        self.assertEqual(result['evidence_needed'],row['evidence_needed'])

    def test_evidence_comparator_cannot_silently_replace_the_baseline(self):
        row=question('在同需求新基准下，观察是否存在任一组合方案使核心总耗时低于仅停车对照。')
        # v5 retains its old blind spot; the new version rejects, not repairs it.
        old=bind(row,qw.V5_VERSION)
        self.assertNotIn('definition',old['evaluation'])
        with self.assertRaisesRegex(ModelError,'参照对象'):bind(row)
        evaluation=dict(old['evaluation'])
        issues=qw.definition_conflicts(row,evaluation,qw.VERSION)
        self.assertEqual([(i['field'],i['claimed_comparator']) for i in issues],[('evidence_needed','parking_only')])

    def test_contextual_baseline_is_not_the_comparison_operand(self):
        row=question('在同需求新基准下，组合核心总耗时低于仅停车对照。',
                     test='core_advantage_over_parking',text='至少一个组合方案相比仅停车核心耗时更低')
        self.assertEqual(bind(row)['evaluation']['definition']['comparator'],'parking_only')
        row['evidence_needed']='比较核心总耗时低于无治理基准'
        with self.assertRaisesRegex(ModelError,'参照对象'):bind(row)

    def test_comparison_with_both_singles_is_not_comparison_with_parking_only(self):
        row=question('组合核心总耗时低于两个单项措施',test='core_advantage_over_parking',
                     text='至少一个组合方案相比仅停车核心耗时更低')
        with self.assertRaisesRegex(ModelError,'参照对象'):bind(row)
        row.update(hypothesis='组合比两个单项核心耗时都低');row['criterion']['test']='combination_core_advantage'
        self.assertEqual(bind(row)['evaluation']['definition']['comparator'],'both_single_measures')
        for pair in ('停车单项和单向单项','单向单项与停车单项','停车单项及禁转单项'):
            self.assertEqual(qw.comparison_references('组合核心总耗时分别低于'+pair),{'both_single_measures'})

    def test_frozen_neighbor_values_reach_binding_and_both_public_fields(self):
        row=question('邻区耗时增幅不超过0.5%，邻区毛新增过饱和不超过0.1km',test='neighbor_limits',
                     text='至少一个组合满足邻区原定门槛')
        bound=bind(row);scope=bound['evaluation']['scope'][0]
        self.assertEqual((scope['max_neighbor_time_increase_pct'],scope['max_new_oversaturated_km']),(.5,.1))
        self.assertIn('≤0.5%',bound['evaluation']['interpretation']['question'])
        self.assertNotIn('max_new_oversaturated_km',bind(row,qw.V5_VERSION)['evaluation']['scope'][0])
        for field in ('hypothesis','evidence_needed'):
            for text in ('邻区耗时增幅不超过0%','邻区毛新增过饱和不超过0km'):
                wrong=copy.deepcopy(row);wrong[field]=text
                with self.subTest(field=field,text=text),self.assertRaisesRegex(ModelError,'门槛'):bind(wrong)

    def test_causal_conjunction_remains_rejected_and_is_not_automatically_weakened(self):
        row=question('实测至少一个候选使核心总耗时低于无治理基准',
                     text='至少存在一个停车候选降低核心总耗时。当前停车干扰是拥堵直接成因之一。')
        before=copy.deepcopy(row)
        with self.assertRaisesRegex(ModelError,'成因'):bind(row)
        scope=design(groups(),{},qw.VERSION)
        _,check=inspect([row],scope,dict(fits=True,model=dict(used=1,required=6,limit=7)))
        self.assertFalse(check['correction_feasible'])
        self.assertEqual(check['issues'][0]['allowed_policies'],[])
        self.assertEqual(row,before)

    def test_catalog_does_not_preselect_policy_quantifier_or_governance_answer(self):
        catalog=hc.question_catalog()
        self.assertEqual(set(catalog),set(hc.TESTS))
        for test,definition in catalog.items():
            self.assertEqual(definition['allowed_policies'],hc.POLICY_OPTIONS[test])
            self.assertNotIn('quantifier',definition)
            self.assertNotIn('selected',definition)
        catalog['core_time_decrease']['allowed_policies'].clear()
        self.assertTrue(hc.POLICY_OPTIONS['core_time_decrease'])

    def test_current_wire_design_precedes_questions_and_criterion_precedes_wording(self):
        self.assertEqual(list(np.ASSESSMENT['properties']),['criterion','hypothesis','evidence_needed'])
        decision=np.TOOL['parameters']['properties']['decision']['anyOf'][0]
        self.assertEqual(list(decision['properties'])[:3],['intent','experiments','assessment'])
        helper=natural_helpers.NaturalPlanTests();helper.compose([experiment(['parking'])])
        payload=json.loads(helper.client.request.call_args.args[1][0]['content'])
        self.assertEqual(payload['initial_question_protocol'],hc.QUESTION_PROTOCOL)
        self.assertEqual(payload['hypothesis_tests'],hc.question_catalog())
        self.assertNotIn('hypothesis_policy_options',payload)
        self.assertEqual({test:value['allowed_policies'] for test,value in payload['hypothesis_tests'].items()},hc.POLICY_OPTIONS)


if __name__=='__main__':unittest.main()
