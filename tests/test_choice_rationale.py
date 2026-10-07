"""Keep observation and experimental hypothesis separate in the actual tool."""
import copy
import json
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

from traffic_agent import choice_rationale as cr
from traffic_agent.choice_evidence import VERSION as CHOICE_VERSION
from traffic_agent.feedback_planner import PlanningSession,decision_tool,verify_review_permissions
from traffic_agent.planning_response import ResponseValidationError
from traffic_agent.planning_review import display_trace
from test_choice_evidence import payload
from test_feedback_planner import reply


class ChoiceRationaleTests(unittest.TestCase):
    def test_protocols_freeze_before_baseline_and_legacy_binding_clears_them(self):
        from traffic_agent.planning_payload import VERSION as INPUT_VERSION
        s=PlanningSession(Mock(),'原始问题',choice_basis_protocol=CHOICE_VERSION)
        agent=SimpleNamespace(job_id='j',state=dict(runs=[]))
        plan=dict(input_protocol=INPUT_VERSION,choice_rationale_protocol=cr.VERSION,choice_basis_protocol=CHOICE_VERSION)
        s.bind_programme(plan,[agent])
        self.assertEqual(agent.state['input_protocol'],INPUT_VERSION)
        self.assertEqual(agent.state['choice_rationale_protocol'],cr.VERSION)
        agent.state['runs']=[dict(name='baseline')]
        for key in ('input_protocol','choice_rationale_protocol'):
            broken=dict(plan);broken.pop(key)
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'执行开始后'):s.bind_programme(broken,[agent])
        agent.state['runs']=[];s.bind_programme({},[agent])
        for key in ('input_protocol','choice_rationale_protocol'):
            self.assertNotIn(key,agent.state);self.assertIsNone(getattr(s,key))
        with self.assertRaisesRegex(ValueError,'需要结构化'):s.bind_programme(dict(choice_rationale_protocol=cr.VERSION),[])

    def test_schema_and_runtime_require_one_structured_rationale_for_experiments(self):
        p=payload(version=CHOICE_VERSION);p['context']['choice_rationale_protocol']=cr.VERSION
        self.assertEqual(decision_tool(p)['parameters']['properties']['reason']['enum'],[''])
        v=dict(action='candidate_0',reason='已证明这里是拥堵主因')
        with self.assertRaises(ResponseValidationError) as err:cr.validate(v,p)
        self.assertEqual(err.exception.code,'choice_rationale')
        v['reason']='';cr.validate(v,p)
        p['allowed_actions']['stop']={}
        self.assertIn('allOf',decision_tool(p)['parameters'])
        cr.validate(dict(action='stop',reason='已完成约定比较，模型决定停止新增试验。'),p)
        legacy=copy.deepcopy(p);legacy['context'].pop('choice_rationale_protocol')
        self.assertNotIn('allOf',decision_tool(legacy)['parameters'])
        cr.validate(dict(action='candidate_0',reason='保留原历史理由'),legacy)

    def test_valid_choice_retains_model_hypothesis_falsifier_and_alternative(self):
        p=payload(version=CHOICE_VERSION)
        def decide(instructions,inputs,tools,choice):
            actual=json.loads(inputs[0]['content'])
            return reply('candidate_0',actual['evidence_refs'],actual)
        s=PlanningSession(Mock(request=Mock(side_effect=decide)),'原始问题',choice_basis_protocol=CHOICE_VERSION)
        e=s.decide(p['phase'],'job',p['observations'],p['allowed_actions'],p['context'],allow_stop=False)
        self.assertEqual(e['status'],'accepted',e.get('validation'))
        self.assertEqual(e['reason'],'')
        verify_review_permissions(e)
        shown=display_trace([e])[0]
        self.assertEqual(shown['selection_reason'],'待检验假设（尚未证实）：'+e['selection_basis']['working_hypothesis'])
        self.assertEqual(shown['selection_basis'],e['selection_basis'])
        self.assertNotIn('selection_reason',e)
        bad=copy.deepcopy(e);bad['reason']='事后补成已经证实的因果'
        with self.assertRaises(ResponseValidationError):verify_review_permissions(bad)

    def test_duplicate_causal_reason_is_rejected_without_rewriting_or_another_request(self):
        p=payload(version=CHOICE_VERSION)
        def decide(instructions,inputs,tools,choice):
            actual=json.loads(inputs[0]['content']);r=reply('candidate_0',actual['evidence_refs'],actual)
            value=json.loads(r['calls'][0]['arguments']);value['reason']='该方向为拥堵主因。'
            r['calls'][0]['arguments']=json.dumps(value);return r
        client=Mock(request=Mock(side_effect=decide))
        e=PlanningSession(client,'原始问题',choice_basis_protocol=CHOICE_VERSION).decide(
            p['phase'],'job',p['observations'],p['allowed_actions'],p['context'],allow_stop=False)
        self.assertEqual(e['status'],'rejected')
        self.assertEqual(e['validation']['code'],'choice_rationale')
        self.assertEqual(e['proposal_excerpt']['reason'],'该方向为拥堵主因。')
        self.assertIsNone(cr.text(e));client.request.assert_called_once()


if __name__=='__main__':unittest.main()
