"""No provider I/O: preserve actionable failures without retaining raw responses."""
import copy
import json
import unittest
from unittest.mock import Mock

from traffic_agent.conclusion_review import assess_traces
from traffic_agent.feedback_planner import PlanningSession, verify_review_permissions
from traffic_agent.planning_response import VERSION, capture_proposal
from functools import partial

# These v1-v14 response fixtures deliberately retain the legacy free-form risk.
# New risk contracts are exercised by test_risk_review and mixed controllers.
PlanningSession=partial(PlanningSession,risk_review_protocol=None)


def response(value):
    return dict(calls=[dict(name='choose_next_experiment',arguments=json.dumps(value))],metadata=dict(tokens=41))


def decision():
    return dict(action='stop',summary='停车候选已有配对实测，但未通过全部约束。',reason='保留本组基准和实际代价。',
                risk='下一组尚未实测，不能推断其效果。',next_check='后续独立研究仍需自己的新基准。',
                evidence_refs=['j:policy'],cost_acknowledgments=[dict(ref='j:policy',constraint_passed=False,
                    has_measured_cost=True,neighbor_new_oversaturated_km=.056)],
                hypothesis_updates=[dict(hypothesis_id='H1',judgment='contradicted',finding_ids=['benefit'],explanation='本组已测候选未通过区域约束。'),
                                    dict(hypothesis_id='H2',judgment='unresolved',finding_ids=[],explanation='另一组尚未执行，不能作出结论。')])


def inputs():
    from traffic_agent.conclusion_review import V8_VERSION
    observed=[dict(ref='j:policy',run='policy',changes=[dict(from_node=1,to_node=2,parking=1)],eligible=False,
                   delta=dict(core=dict(running_time_change_pct=-10),neighbor=dict(running_time_change_pct=.2)),
                   new_neighbor_oversaturated_km=.056)]
    context=dict(measured_conclusion=dict(version=V8_VERSION),
                 initial_hypotheses=[dict(id='H1',hypothesis='停车可以满足区域约束',evidence_needed='配对实测'),
                                     dict(id='H2',hypothesis='另一组单向有效',evidence_needed='另一组实测')],
                 research_evidence=dict(findings=[dict(id='benefit',status='not_supported',evidence_refs=['j:policy']),
                                                 dict(id='strength',status='unmeasured',evidence_refs=[])]))
    return observed,context


class ResponseChecks(unittest.TestCase):
    def programme_context(self):
        observed,context=inputs()
        findings=[dict(f,id='study_1.'+f['id']) for f in context['research_evidence']['findings']]
        context.update(programme_final_group=True,programme_hypotheses=copy.deepcopy(context['initial_hypotheses']),
                       programme_evidence=dict(groups=[dict(id='study_1',execution_complete=True),
                                                       dict(id='study_2',execution_complete=False)],findings=findings))
        return observed,context

    def test_final_group_target_and_mandatory_trial_do_not_offer_programme_conclusions(self):
        for phase in ('oneway_target','parking'):
            for add_final_fields in (False,True):
                with self.subTest(phase=phase,extra=add_final_fields):
                    observed,context=self.programme_context()
                    value=decision();value.update(action='candidate',hypothesis_updates=[],cost_acknowledgments=[])
                    if add_final_fields:value.update(programme_updates=[],programme_summary='过早的总任务总结。')
                    client=Mock(request=Mock(return_value=response(value)))
                    result=PlanningSession(client,'先选点后实测').decide(phase,'j',observed,
                        {'candidate':dict(title='待测候选')},context,allow_stop=False)
                    tool=client.request.call_args.args[2][0]
                    self.assertNotIn('programme_updates',tool['parameters']['properties'])
                    self.assertNotIn('programme_summary',tool['parameters']['required'])
                    self.assertEqual(result['input']['context']['review_permissions']['programme_review_actions'],[])
                    self.assertEqual(result['input']['context']['programme_evidence'],context['programme_evidence'])
                    self.assertEqual(result['status'],'rejected' if add_final_fields else 'accepted')
                    if add_final_fields:
                        self.assertEqual(result['validation']['code'],'argument_fields')
                        self.assertNotIn('selected',result)
                        self.assertEqual(result['proposal_capture']['omitted_fields'],2)
                    client.request.assert_called_once()

    def test_final_stop_still_requires_every_programme_hypothesis_and_summary(self):
        for flaw,expected in ((None,'passed'),('omitted','argument_fields'),('empty','hypothesis_coverage'),('summary','programme_summary')):
            with self.subTest(flaw=flaw):
                observed,context=self.programme_context()
                context['programme_evidence']['groups'][1]['execution_complete']=True
                value=decision()
                if flaw!='omitted':
                    value.update(programme_updates=[] if flaw=='empty' else
                        [dict(u,finding_ids=['study_1.'+f for f in u['finding_ids']]) for u in value['hypothesis_updates']],
                        programme_summary='' if flaw=='summary' else '两组已各自实测；保留约束失败和未解决的假设，不合并收益。')
                client=Mock(request=Mock(return_value=response(value)))
                result=PlanningSession(client,'最终复核').decide('route','j',observed,{},context)
                props=client.request.call_args.args[2][0]['parameters']['properties']
                self.assertEqual(props['programme_updates']['minItems'],2)
                self.assertEqual(result['input']['context']['review_permissions']['programme_review_actions'],['stop'])
                self.assertEqual(result['validation'].get('code','passed'),expected)
                self.assertEqual(result['status'],'accepted' if flaw is None else 'rejected')

    def test_review_permissions_are_audited_and_cannot_be_forged_as_final(self):
        observed,context=self.programme_context()
        value=decision();value.update(action='candidate',hypothesis_updates=[],cost_acknowledgments=[])
        client=Mock(request=Mock(return_value=response(value)))
        result=PlanningSession(client,'权限检查').decide('oneway_target','j',observed,{'candidate':{}},context,allow_stop=False)
        self.assertEqual(result['status'],'accepted')
        verify_review_permissions(result)
        altered=copy.deepcopy(result);altered['input']['context']['review_permissions']['programme_review_actions']=['stop']
        with self.assertRaisesRegex(ValueError,'权限'):verify_review_permissions(altered)
        altered=copy.deepcopy(result);altered['programme_summary']='未经授权的最终结论'
        with self.assertRaisesRegex(ValueError,'最终结论'):verify_review_permissions(altered)

    def run_decision(self,value):
        client=Mock(request=Mock(return_value=response(value)))
        planner=PlanningSession(client,'独立分组复核')
        observed,context=inputs()
        return planner.decide('parking','j',observed,{},context),planner,client

    def test_final_schema_requires_all_hypotheses_and_all_costs(self):
        result,_,client=self.run_decision(decision())
        self.assertEqual(result['status'],'accepted')
        props=client.request.call_args.args[2][0]['parameters']['properties']
        self.assertEqual(props['hypothesis_updates']['minItems'],2)
        self.assertEqual(props['hypothesis_updates']['maxItems'],2)
        self.assertEqual(props['cost_acknowledgments']['minItems'],1)
        self.assertEqual(result['validation'],dict(version=VERSION,status='passed'))
        self.assertNotIn('proposal_excerpt',result)

    def test_missing_hypothesis_is_named_retained_and_does_not_retry(self):
        value=decision();value['hypothesis_updates'].pop()
        result,planner,client=self.run_decision(value)
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(result['validation']['code'],'hypothesis_coverage')
        self.assertIn('遗漏初始假设',result['reason'])
        self.assertEqual(result['proposal_excerpt'],value)
        self.assertFalse(result['proposal_capture']['excerpted'])
        self.assertNotIn('selected',result)
        self.assertNotIn('proposed_decision',result)  # Invalid arguments are not a validated conclusion.
        self.assertIsInstance(assess_traces([result]),dict)
        observed,context=inputs();planner.decide('parking','j',observed,{},context)
        client.request.assert_called_once()
        self.assertEqual(planner.used,2)  # One initial request plus one actual decision.

    def test_unmeasured_reference_cannot_support_a_hypothesis(self):
        value=decision();value['hypothesis_updates'][0]['finding_ids']=['benefit','strength']
        result,_,client=self.run_decision(value)
        self.assertEqual(result['validation']['code'],'unmeasured_finding')
        self.assertIn('未测结果',result['reason'])
        self.assertEqual(result['proposal_excerpt'],value)
        client.request.assert_called_once()

    def test_unknown_fields_and_credential_strings_never_enter_excerpt(self):
        secret='sk_'+'synthetic-not-a-real-credential'
        value=decision();value['reason']='公开依据 '+secret
        value['private_reasoning']='hidden material '+secret
        result,_,_=self.run_decision(value)
        self.assertEqual(result['validation']['code'],'argument_fields')
        self.assertNotIn(secret,json.dumps(result))
        self.assertNotIn('hidden material',json.dumps(result))
        self.assertNotIn('private_reasoning',result['proposal_excerpt'])
        self.assertEqual(result['proposal_capture']['omitted_fields'],1)
        self.assertEqual(result['proposal_capture']['redacted_strings'],1)

    def test_nested_invalid_fields_are_safe_and_listing_remains_readable(self):
        value=decision();value['hypothesis_updates']=[dict(hypothesis_id='H1',private_content='do not save',explanation='x'*3000)]
        result,_,_=self.run_decision(value)
        self.assertEqual(result['validation']['code'],'hypothesis_fields')
        self.assertNotIn('do not save',json.dumps(result))
        self.assertEqual(len(result['proposal_excerpt']['hypothesis_updates'][0]['explanation']),2048)
        self.assertEqual(result['proposal_capture']['shortened_strings'],1)
        self.assertIsInstance(assess_traces([result]),dict)

    def test_invalid_json_or_scalar_keeps_only_safe_diagnostics(self):
        for raw,code,kind in [('not JSON','arguments_json','json_syntax'),('{"action":NaN}','arguments_json','non_finite'),('[]','arguments_object',None)]:
            with self.subTest(raw=raw):
                client=Mock(request=Mock(return_value=dict(calls=[dict(name='choose_next_experiment',arguments=raw)],metadata=dict(tokens=41))))
                result=PlanningSession(client,'格式检查').decide('route','j',[dict(ref='j:baseline')],{})
                self.assertEqual(result['status'],'rejected')
                self.assertEqual(result['validation']['code'],code)
                self.assertNotIn('proposal_excerpt',result)
                if kind:
                    self.assertEqual(result['arguments_diagnostic']['version'],'tool-arguments-diagnostic-v1')
                    self.assertEqual(result['arguments_diagnostic']['kind'],kind)
                    self.assertEqual(result['arguments_diagnostic']['length'],len(raw))
                    self.assertNotIn(raw,json.dumps(result,ensure_ascii=False))
                else:self.assertNotIn('arguments_diagnostic',result)
                self.assertEqual(result['usage']['tokens'],41)
                client.request.assert_called_once()

    def test_untrusted_exception_text_is_not_saved(self):
        client=Mock(request=Mock(side_effect=RuntimeError('private arbitrary exception content')))
        result=PlanningSession(client,'错误隔离').decide('route','j',[dict(ref='j:baseline')],{})
        self.assertEqual(result['validation']['code'],'local_validation')
        self.assertNotIn('private arbitrary',json.dumps(result))

    def test_capture_bounds_large_array_and_ignores_unknown_names(self):
        value=dict(items=[dict(text='a'*3000,unknown='private') for _ in range(50)])
        row_schema=dict(properties=dict(text=dict(type='string')))
        schema=dict(properties=dict(items=dict(type='array',maxItems=3,items=row_schema)))
        before=copy.deepcopy(value);excerpt,record=capture_proposal(value,schema)
        self.assertEqual(value,before)
        self.assertEqual(len(excerpt['items']),3)
        self.assertEqual(record['omitted_items'],47)
        self.assertEqual(record['omitted_fields'],3)
        self.assertEqual(record['shortened_strings'],3)
        self.assertNotIn('private',json.dumps(excerpt))


if __name__=='__main__':unittest.main()
