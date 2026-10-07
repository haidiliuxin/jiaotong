"""Observation-only permissions and independent studies; offline model boundary."""
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from traffic_agent.natural_plan import compose,limits
from traffic_agent.plan_compiler import diagnosis_constraints
from test_natural_plan import CATALOG,experiment,model_reply


class DiagnosisIntentTests(unittest.TestCase):
    def compose(self,text,studies,catalog=CATALOG):
        client=Mock(request=Mock(return_value=model_reply(studies)))
        result=compose(text,Path('OFFLINE_ONLY'),client,context=catalog)
        client.request.assert_called_once()
        return result,json.loads(client.request.call_args.args[1][0]['content'])

    def test_observation_then_explicit_intervention_research_is_not_diagnosis_only(self):
        for text in ('先做诊断，再研究停车治理','先了解现状，然后比较单向组织',
                     '先做个体检，接着评估停车治理','先诊断再研究停车治理',
                     '先做诊断，再判断是否需要治理','先诊断，如果需要再研究停车治理'):
            with self.subTest(text=text):
                request='长江村路'+text+'；最多4次仿真，不复跑'
                strategies=['parking','oneway'] if '单向' in text else ['parking']
                result,payload=self.compose(request,[experiment(strategies)])
                self.assertEqual(result['status'],'ready')
                self.assertFalse(payload['diagnosis_only'])
                self.assertEqual(result['experiments'][0]['minimum_runs'],4 if '单向' in text else 2)

    def test_observation_only_or_explicit_confirmation_keeps_permission_closed(self):
        for text in ('只做诊断','先了解现状','先做诊断，后续治理再说',
                     '先做体检，等我确认后再研究停车','先诊断，不再治理',
                     '只做诊断，以后再研究停车治理','暂不进行治理'):
            with self.subTest(text=text):
                request='长江村路'+text+'；最多3次仿真，不复跑'
                with self.assertRaisesRegex(ValueError,'只要求诊断'):
                    self.compose(request,[experiment(['parking'])])
                result,_=self.compose(request,[experiment([],study='diagnosis')])
                self.assertEqual(result['status'],'ready')
                self.assertEqual(result['experiments'][0]['minimum_runs'],1)

    def test_negated_only_diagnosis_does_not_turn_into_a_governance_ban(self):
        for text in ('不要只做诊断，要比较停车治理','不仅做诊断，还要研究停车治理','不只是诊断，还要停车治理'):
            with self.subTest(text=text):
                self.assertFalse(limits('长江村路'+text)['diagnosis_only'])

    def test_each_named_road_keeps_its_own_observation_only_permission(self):
        catalog=dict(CATALOG,matched_roads=['长江村路','红景路'])
        studies=[experiment([],study='diagnosis',road='长江村路'),experiment(['parking'],road='红景路')]
        for description in ('长江村路只做体检；红景路比较停车治理',
                            '长江村路最近拥堵，只做体检；红景路比较停车治理',
                            '长江村路先做诊断；红景路再研究停车治理'):
            request=description+'；最多3次仿真，不复跑'
            with self.subTest(request=request):
                result,payload=self.compose(request,studies,catalog)
                self.assertEqual([e['task']['scenario'] for e in result['experiments']],['diagnosis','parking'])
                self.assertEqual(result['allocated_runs'],3)
                self.assertFalse(payload['diagnosis_only'])
                self.assertEqual(payload['diagnosis_constraints']['roads'],['长江村路'])
                for quote in payload['diagnosis_constraints']['source_quotes']:
                    self.assertIn(quote['source_quote'],request)
                with self.assertRaisesRegex(ValueError,'长江村路诊断'):
                    self.compose(request,[experiment(['parking'],road='长江村路'),studies[1]],catalog)

    def test_global_only_and_both_road_restrictions_still_apply_to_the_whole_plan(self):
        for text in ('长江村路和红景路只做诊断','长江村路停车问题；红景路单向问题；本次只做体检',
                     '长江村路只做诊断；红景路暂不治理'):
            with self.subTest(text=text):
                self.assertTrue(diagnosis_constraints(text,['长江村路','红景路'])['only'])

    def test_requested_independent_diagnosis_cannot_be_compiled_away_to_fit_budget(self):
        studies=[experiment([],study='diagnosis'),experiment(['parking'])]
        request='长江村路需要独立体检，并另做停车治理，两组单独跑新基准；最多3次仿真，不复跑'
        result,_=self.compose(request,studies)
        self.assertEqual([e['task']['scenario'] for e in result['experiments']],['diagnosis','parking'])
        self.assertEqual(result['compilation']['version'],'plan-compilation-v3')
        self.assertEqual(result['compilation']['actions'],[])
        self.assertEqual(result['model_budget']['minimum_required'],4)
        blocked,_=self.compose(request.replace('最多3次','最多2次'),studies)
        self.assertEqual(blocked['status'],'needs_input')
        self.assertEqual(blocked['budget_check']['native']['required'],3)
        self.assertFalse(blocked['budget_check']['correction_feasible'])
        self.assertEqual([g['may_omit'] for g in blocked['budget_check']['groups']],[False,False])


if __name__=='__main__':unittest.main()
