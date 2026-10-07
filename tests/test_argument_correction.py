"""A malformed public tool response may be reissued once over frozen facts."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from traffic_agent.feedback_planner import PlanningSession, digest, verify_review_permissions, verify_input_wire
from traffic_agent.llm_client import ModelError
from traffic_agent.planning_response import ARGUMENT_CORRECTION_VERSION, request_usages
from test_planning_response import inputs, decision, response


def malformed(raw='{"action":"parking_a"'):
    result=response({})
    result['calls'][0]['arguments']=raw
    result['metadata']=dict(tokens=30,response_id='offline-first')
    return result


def candidate():
    value=decision()
    value.update(action='parking_a',summary='按负反馈选择另一个停车候选，效果待验证。',
                 next_check='执行另一个候选并读取配对结果。',cost_acknowledgments=[])
    return value


class ArgumentCorrectionTests(unittest.TestCase):
    def exercise(self,replies=None,limit=6,required=2,reserved=0,protocol=ARGUMENT_CORRECTION_VERSION,phase='parking',
                 menu=None,runtime_reserve=0):
        observed,context=inputs()
        context.update(completion_guard=dict(required_model_requests=required),reserved_model_requests=reserved)
        client=Mock(request=Mock(side_effect=replies or [malformed(),response(candidate())]))
        session=PlanningSession(client,'保留原始用户问题',initial_usage=dict(tokens=10),limit=limit,
            risk_review_protocol=None,argument_correction_protocol=protocol)
        session.reserved_requests=runtime_reserve
        changes=[]
        session.on_update=lambda:changes.append(copy.deepcopy(dict(trace=session.trace,budget=session.budget())))
        entry=session.decide(phase,'j',observed,menu or {'parking_a':dict(title='另一个停车候选')},context,allow_stop=False)
        return session,entry,client,changes

    def test_intermediate_response_reissued_once_without_guessing_an_action(self):
        session,entry,client,changes=self.exercise()
        self.assertEqual(entry['status'],'accepted',entry.get('validation'))
        correction=entry['review_correction'];first=correction['first_attempt']
        self.assertEqual(correction['version'],ARGUMENT_CORRECTION_VERSION)
        self.assertEqual(first['action'],'stop')
        self.assertNotIn('selected',first)
        self.assertNotIn('proposal_excerpt',first)
        self.assertEqual(entry['action'],'parking_a')
        self.assertEqual(client.request.call_count,2)
        self.assertEqual(session.budget(),dict(limit=6,used=3,remaining=3,known_tokens=81,unknown_usage=0,halted=False))
        self.assertEqual([u['tokens'] for u in request_usages(entry)],[30,41])
        second=json.loads(client.request.call_args.args[1][0]['content'])
        self.assertEqual(second,correction['input'])
        for key in ('request','phase','observations','context','evidence_refs','allowed_actions'):
            self.assertEqual(correction['input'][key],entry['input'][key])
        self.assertEqual(second['review_feedback']['original_failure_evidence'],'parser_diagnostic_only')
        self.assertNotIn('previous_proposal',second['review_feedback'])
        self.assertEqual(second['review_feedback']['reserved_after_correction'],1)
        pending=next(c for c in changes if c['trace'][-1].get('review_correction') and c['trace'][-1]['status']=='awaiting_model')
        self.assertEqual(pending['budget']['unknown_usage'],1)
        self.assertNotIn('selected',pending['trace'][-1])
        verify_review_permissions(entry);verify_input_wire(entry)

    def test_repair_can_choose_any_current_action_because_first_was_not_decodable(self):
        changed=candidate();changed['action']='parking_b'
        _,entry,_,_=self.exercise(replies=[malformed(),response(changed)],
            menu={'parking_a':dict(title='A'),'parking_b':dict(title='B')})
        self.assertEqual((entry['status'],entry['action']),('accepted','parking_b'))
        verify_review_permissions(entry)

    def test_full_original_checks_still_reject_an_invalid_second_response(self):
        changed=candidate();changed['evidence_refs']=['other:unmeasured']
        session,entry,client,_=self.exercise(replies=[malformed(),response(changed)])
        self.assertEqual(entry['status'],'rejected')
        self.assertEqual(entry['validation']['code'],'evidence_refs')
        self.assertTrue(session.halted)
        self.assertNotIn('selected',entry)
        self.assertEqual(client.request.call_count,2)
        verify_review_permissions(entry)

    def test_no_second_repair_for_syntax_or_transport_or_later_decision(self):
        for failure in (malformed(),ModelError('模型网络连接失败或超时')):
            session,entry,client,_=self.exercise(replies=[malformed(),failure])
            self.assertEqual(entry['status'],'rejected')
            self.assertTrue(session.halted)
            self.assertEqual(client.request.call_count,2)
            self.assertEqual(session.budget()['unknown_usage'],int(isinstance(failure,ModelError)))
            verify_review_permissions(entry)
        session,_,client,_=self.exercise(replies=[malformed(),response(candidate()),malformed()])
        observed,ctx=inputs();ctx.update(completion_guard=dict(required_model_requests=2),reserved_model_requests=0)
        entry=session.decide('parking','j',observed,{'parking_a':dict(title='A')},ctx,allow_stop=False)
        self.assertEqual(entry['status'],'rejected')
        self.assertNotIn('review_correction',entry)
        self.assertEqual(client.request.call_count,3)

    def test_budget_keeps_all_later_decisions_and_reserved_studies(self):
        for kwargs in (dict(limit=3),dict(limit=5,reserved=2),dict(limit=6,runtime_reserve=4),
                       dict(required=None),dict(reserved=True)):
            with self.subTest(kwargs=kwargs):
                _,entry,client,_=self.exercise(**kwargs)
                self.assertNotIn('review_correction',entry)
                client.request.assert_called_once()
        session,entry,client,_=self.exercise(limit=4)
        self.assertEqual(entry['status'],'accepted')
        self.assertEqual(session.budget()['remaining'],1)
        self.assertEqual(client.request.call_count,2)

    def test_legacy_nonnumeric_json_wrong_tool_and_nonparking_never_reissued(self):
        wrong=malformed();wrong['calls'][0]['name']='other_tool'
        failures=[malformed('{"action":NaN}'),malformed('[]'),wrong,ModelError('连接失败')]
        for first in failures:
            _,entry,client,_=self.exercise(replies=[first])
            self.assertNotIn('review_correction',entry)
            client.request.assert_called_once()
        for kwargs in (dict(protocol=None),dict(phase='route'),dict(menu={'study_2':dict(title='下一研究组')})):
            _,entry,client,_=self.exercise(**kwargs)
            self.assertNotIn('review_correction',entry)
            client.request.assert_called_once()

    def test_undecodable_private_material_is_not_retained_or_sent_back(self):
        private='PRIVATE_REASONING_DO_NOT_RETAIN'
        secret='sk_'+('A'*28)
        _,entry,client,_=self.exercise(replies=[malformed('{"private_reasoning":"'+private+' '+secret),response(candidate())])
        material=json.dumps(entry)+client.request.call_args.args[1][0]['content']
        self.assertNotIn(private,material);self.assertNotIn(secret,material)
        verify_review_permissions(entry)

    def test_audit_rejects_changed_facts_feedback_invalid_diagnostic_and_protocol(self):
        _,original,_,_=self.exercise()
        for mutation in ('facts','feedback','diagnostic','version','raw','accepted_first','reserve'):
            entry=copy.deepcopy(original);c=entry['review_correction'];first=c['first_attempt']
            if mutation=='facts':c['input']['observations'][0]['new_neighbor_oversaturated_km']=0
            elif mutation=='feedback':c['input']['review_feedback']['arguments_diagnostic']['offset']-=1
            elif mutation=='diagnostic':first['arguments_diagnostic']['offset']=first['arguments_diagnostic']['length']+1
            elif mutation=='version':c['version']='unknown'
            elif mutation=='raw':first['raw_arguments']='do not retain'
            elif mutation=='accepted_first':first['selected']={}
            else:entry['input']['context']['reserved_model_requests']=10;entry['observation_digest']=digest(entry['input'])
            c['observation_digest']=digest(c['input'])
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):verify_review_permissions(entry)

    def test_binding_an_old_programme_removes_new_permission_and_existing_runs_freeze_it(self):
        session=PlanningSession(Mock(),'旧任务')
        old=SimpleNamespace(job_id='j',state=dict(runs=[]))
        session.bind_programme({},[old])
        self.assertIsNone(session.argument_correction_protocol)
        self.assertNotIn('argument_correction_protocol',old.state)
        session.bind_programme(dict(argument_correction_protocol=ARGUMENT_CORRECTION_VERSION),[old])
        self.assertEqual(old.state['argument_correction_protocol'],ARGUMENT_CORRECTION_VERSION)
        old.state['runs']=[dict(name='baseline')]
        with self.assertRaises(ValueError):session.bind_programme({},[old])


if __name__=='__main__':unittest.main()
