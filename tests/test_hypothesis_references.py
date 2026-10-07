import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from traffic_agent.feedback_planner import PlanningSession, verify_review_permissions
from traffic_agent.research_evidence import (REFERENCE_SCHEMA, LEGACY_REFERENCE_SCHEMA, hypothesis_update_schema,
                                            validate_updates, verify_reference_schema)
from traffic_agent.planning_response import ResponseValidationError
import test_hypothesis_contract as hc


class HypothesisReferenceTests(unittest.TestCase):
    def setUp(self):
        self.hypotheses=[dict(id='H1',hypothesis='仅检验本地样例的实测收益',evidence_needed='本组配对')]
        self.facts=dict(findings=[dict(id='benefit',status='not_supported',evidence_refs=['j:policy']),
            dict(id='strength',status='unmeasured',evidence_refs=[]),
            dict(id='missing_refs',status='supported',evidence_refs=[])])

    def test_tool_only_offers_measured_bases_and_keeps_gaps_in_input(self):
        before=copy.deepcopy(self.facts)
        schema=hypothesis_update_schema(self.hypotheses,self.facts,True)
        self.assertEqual(schema['minItems'],1)
        self.assertEqual(schema['items']['properties']['finding_ids']['items']['enum'],['benefit'])
        self.assertEqual(self.facts,before)
        self.assertEqual(schema['items']['properties']['judgment']['enum'],['supported','contradicted','unresolved'])

    def test_no_measurements_require_empty_references_without_invalid_empty_enum(self):
        schema=hypothesis_update_schema(self.hypotheses,dict(findings=self.facts['findings'][1:]))
        references=schema['items']['properties']['finding_ids']
        self.assertEqual(references['maxItems'],0)
        self.assertNotIn('enum',references['items'])
        update=dict(hypothesis_id='H1',judgment='unresolved',finding_ids=[],explanation='本地样例尚无已测依据，保留未知。')
        validate_updates([update],self.hypotheses,self.facts,True,measured_only=True)

    def test_legacy_unresolved_gap_remains_valid_but_new_tools_reject_it(self):
        update=dict(hypothesis_id='H1',judgment='unresolved',finding_ids=['strength'],explanation='本地样例强度未测，仍待验证。')
        validate_updates([update],self.hypotheses,self.facts,True)
        with self.assertRaisesRegex(ValueError,'判断依据只能引用已测'):
            validate_updates([update],self.hypotheses,self.facts,True,measured_only=True)
        update.update(judgment='contradicted',finding_ids=['benefit','strength'])
        with self.assertRaisesRegex(ValueError,'未测'):
            validate_updates([update],self.hypotheses,self.facts,True)

    def test_local_and_programme_tools_use_separate_measured_ids_and_audit_tampering(self):
        programme=dict(groups=[{},{}],findings=[dict(f,id='study_1.'+f['id']) for f in self.facts['findings']])
        def respond(instructions,inputs,tools,choice):
            payload=json.loads(inputs[0]['content']);properties=tools[0]['parameters']['properties']
            for field,ids in [('hypothesis_updates',['benefit']),('programme_updates',['study_1.benefit'])]:
                self.assertEqual(properties[field]['items']['properties']['finding_ids']['items']['enum'],ids)
            self.assertEqual(payload['context']['research_evidence'],self.facts)
            self.assertEqual(payload['context']['programme_evidence'],programme)
            value=dict(action='stop',summary='离线样例仅验证引用权限，不是交通结果。',reason='本地结构检查保留未测缺口。',
                risk='没有调用模型或原生程序。',next_check='真实整体结论仍须另行验证。',evidence_refs=['j:baseline'],
                hypothesis_updates=[dict(hypothesis_id='H1',judgment='unresolved',finding_ids=[],explanation='本地样例未形成结论。')],
                programme_updates=[dict(hypothesis_id='H1',judgment='contradicted',finding_ids=['study_1.benefit'],explanation='本地样例的实测依据否定假设，强度仍未测。')],
                programme_summary='本地结构样例，不认证真实模型或交通效果。')
            return dict(calls=[dict(name='choose_next_experiment',arguments=json.dumps(value))],metadata=dict(tokens=None))
        client=Mock(request=Mock(side_effect=respond))
        entry=PlanningSession(client,'离线引用检查',risk_review_protocol=None).decide('route','j',[dict(ref='j:baseline')],{},dict(
            initial_hypotheses=self.hypotheses,research_evidence=self.facts,
            programme_hypotheses=self.hypotheses,programme_evidence=programme,programme_final_group=True))
        self.assertEqual(entry['status'],'accepted',entry.get('validation'))
        self.assertEqual(entry['finding_reference_schema'],REFERENCE_SCHEMA)
        verify_review_permissions(entry)
        client.request.assert_called_once()
        broken=copy.deepcopy(entry)
        broken['programme_updates'][0]['finding_ids'].append('study_1.strength')
        with self.assertRaisesRegex(ValueError,'未测'):verify_reference_schema(broken)
        broken['finding_reference_schema']='unknown'
        with self.assertRaisesRegex(ValueError,'未知'):verify_reference_schema(broken)

    def typed(self,unavailable=False):
        a,b=hc.state(),hc.state('oneway')
        hc.add(a,core=70);hc.add(b,'S1_parking',core=80)
        hc.add(b,'S2_oneway',hc.CONTROL,core=30,neighbor=200,eligible=False)
        hypotheses=[hc.hypothesis([a,b],test='unavailable' if unavailable else 'core_time_decrease',id='H1'),
                    hc.hypothesis([a,b],test='admissible',id='H4')]
        facts=dict(findings=[hc.evaluate(h,a,b) for h in hypotheses])
        first=facts['findings'][0]
        update=dict(hypothesis_id='H1',judgment=first['expected_judgment'],
                    finding_ids=[] if unavailable else [first['id']],explanation='离线判据复核，只依据本问题的配对记录。')
        return hypotheses,facts,update

    def test_scoped_typed_references_reject_other_question_or_duplicate_preserve_legacy(self):
        hs,facts,update=self.typed()
        validate_updates([update],hs,facts,reference_schema=REFERENCE_SCHEMA)
        schema=hypothesis_update_schema(hs,facts,reference_schema=REFERENCE_SCHEMA)
        refs=schema['items']['anyOf'][0]['properties']['finding_ids']
        self.assertEqual(refs['items']['enum'],['hypothesis.H1'])
        self.assertEqual((refs['minItems'],refs['maxItems'],refs['uniqueItems']),(1,1,True))
        for extra in ('hypothesis.H4','hypothesis.H1'):
            bad=dict(update,finding_ids=update['finding_ids']+[extra])
            validate_updates([bad],hs,facts,reference_schema=LEGACY_REFERENCE_SCHEMA)
            with self.subTest(extra=extra),self.assertRaises(ResponseValidationError) as err:
                validate_updates([bad],hs,facts,reference_schema=REFERENCE_SCHEMA)
            self.assertEqual(err.exception.code,'finding_scope')
        self.assertEqual(len(facts['findings']),2)  # Other results remain model context.

    def test_unavailable_typed_question_cannot_borrow_measured_proof(self):
        hs,facts,update=self.typed(unavailable=True)
        validate_updates([update],hs,facts,reference_schema=REFERENCE_SCHEMA)
        refs=hypothesis_update_schema(hs,facts,reference_schema=REFERENCE_SCHEMA)['items']['anyOf'][0]['properties']['finding_ids']
        self.assertEqual(refs['maxItems'],0)
        self.assertNotIn('enum',refs['items'])
        with self.assertRaises(ResponseValidationError) as err:
            validate_updates([dict(update,finding_ids=['hypothesis.H4'])],hs,facts,reference_schema=REFERENCE_SCHEMA)
        self.assertEqual(err.exception.code,'finding_scope')

    def test_reference_protocol_cannot_drop_or_downgrade_inside_new_context(self):
        hs,facts,update=self.typed()
        entry=dict(status='accepted',finding_reference_schema=REFERENCE_SCHEMA,
                   input=dict(context=dict(finding_reference_schema=REFERENCE_SCHEMA,initial_hypotheses=hs,research_evidence=facts)),
                   hypothesis_updates=[update])
        verify_reference_schema(entry)
        for version in (None,LEGACY_REFERENCE_SCHEMA,'unknown'):
            with self.subTest(version=version),self.assertRaises(ValueError):
                verify_reference_schema(dict(entry,finding_reference_schema=version))
        session=PlanningSession(Mock(),'离线版本检查')
        session.bind_programme({},[])
        self.assertEqual(session.finding_reference_schema,LEGACY_REFERENCE_SCHEMA)
        with self.assertRaisesRegex(ValueError,'未知'):session.bind_programme(dict(finding_reference_schema='unknown'),[])

    def test_new_and_legacy_binding_freeze_before_runs_and_cannot_change_after_runs(self):
        session=PlanningSession(Mock(request=Mock(return_value=dict(calls=[]))),'离线协议冻结')
        agent=SimpleNamespace(job_id='j',state=dict(runs=[],finding_reference_schema=REFERENCE_SCHEMA))
        session.bind_programme({},[agent])
        self.assertNotIn('finding_reference_schema',agent.state)
        session.bind_programme(dict(finding_reference_schema=LEGACY_REFERENCE_SCHEMA),[agent])
        self.assertEqual(agent.state['finding_reference_schema'],LEGACY_REFERENCE_SCHEMA)
        entry=session.decide('route','j',[dict(ref='j:baseline')],{})
        self.assertEqual(entry['input']['context']['finding_reference_schema'],LEGACY_REFERENCE_SCHEMA)
        agent.state['runs']=[dict(name='baseline')]
        with self.assertRaisesRegex(ValueError,'执行开始后'):
            session.bind_programme(dict(finding_reference_schema=REFERENCE_SCHEMA),[agent])
        self.assertEqual(session.finding_reference_schema,LEGACY_REFERENCE_SCHEMA)


if __name__=='__main__':unittest.main()
