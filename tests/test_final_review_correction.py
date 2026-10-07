"""Offline review recovery; no service or native simulator is contacted."""
import copy
import json
import unittest
from unittest.mock import Mock

from traffic_agent.feedback_planner import PlanningSession, digest, verify_review_permissions
from traffic_agent.llm_client import ModelError
from traffic_agent.planning_response import correction_payload, request_usages
from test_planning_response import inputs, decision, response


def context():
    observed,ctx=inputs()
    ctx.update(programme_final_group=True,reserved_model_requests=0,
        programme_evidence=dict(groups=[dict(id='study_1',execution_complete=True)],
            findings=copy.deepcopy(ctx['research_evidence']['findings'])))
    return observed,ctx


def invalid():
    value=decision()
    value['hypothesis_updates'][0]['finding_ids'].append('strength')
    return value


class FinalReviewCorrectionTests(unittest.TestCase):
    def exercise(self,replies=None,limit=6,**kwargs):
        observed,ctx=context();ctx.update(kwargs)
        client=Mock(request=Mock(side_effect=replies or [response(invalid()),response(decision())]))
        session=PlanningSession(client,'离线修正检查',initial_usage=dict(tokens=10),limit=limit,risk_review_protocol=None)
        changes=[]
        session.on_update=lambda:changes.append(dict(trace=copy.deepcopy(session.trace),budget=session.budget()))
        entry=session.decide('route','j',observed,{},ctx)
        return session,entry,client,changes

    def test_full_review_is_reissued_once_with_frozen_facts_and_both_usages(self):
        session,entry,client,changes=self.exercise()
        self.assertEqual(entry['status'],'accepted',entry.get('validation'))
        correction=entry['review_correction'];first=correction['first_attempt']
        self.assertEqual(first['validation']['code'],'unmeasured_finding')
        self.assertEqual(first['proposal_excerpt'],invalid())
        self.assertEqual(first['status'],'rejected')
        self.assertNotIn('proposal_excerpt',entry)
        self.assertEqual(client.request.call_count,2)
        self.assertEqual(session.budget(),dict(limit=6,used=3,remaining=3,known_tokens=92,unknown_usage=0,halted=False))
        self.assertEqual(len(request_usages(entry)),2)
        self.assertEqual(correction['input'],json.loads(client.request.call_args.args[1][0]['content']))
        for key in ('observations','context','allowed_actions','evidence_refs','request','phase'):
            self.assertEqual(correction['input'][key],entry['input'][key])
        self.assertEqual(correction['input']['model_budget']['known_tokens'],51)
        feedback=correction['input']['review_feedback']
        self.assertEqual(feedback['allowed_finding_ids']['hypothesis_updates'],['benefit'])
        pending=next(c for c in changes if c['trace'][-1].get('review_correction') and c['trace'][-1]['status']=='awaiting_model')
        self.assertEqual(pending['budget']['used'],3)
        self.assertEqual(pending['budget']['unknown_usage'],1)
        self.assertNotIn('selected',pending['trace'][-1])
        verify_review_permissions(entry)

    def test_second_rejection_or_transport_failure_stops_and_keeps_unknown_usage(self):
        for result in (response(invalid()),ModelError('模型网络连接失败或超时')):
            with self.subTest(result=type(result).__name__):
                s,e,client,_=self.exercise([response(invalid()),result])
                self.assertEqual((e['status'],s.used),('rejected',3))
                self.assertTrue(s.halted)
                self.assertEqual(s.budget()['unknown_usage'],int(isinstance(result,ModelError)))
                self.assertEqual(client.request.call_count,2)
                self.assertNotIn('selected',e)
                verify_review_permissions(e)
                self.assertEqual(s.decide('route','j',context()[0],{},context()[1])['status'],'stopped')
                self.assertEqual(client.request.call_count,2)

    def test_first_transport_error_never_spends_a_correction_slot(self):
        for failure in (ModelError('模型网络连接失败或超时'),RuntimeError('PRIVATE_EXCEPTION_TEXT')):
            s,e,client,_=self.exercise([failure])
            self.assertEqual(s.used,2)
            self.assertNotIn('review_correction',e)
            self.assertNotIn('PRIVATE_EXCEPTION_TEXT',json.dumps(e))
            client.request.assert_called_once()

    def test_cost_conflict_is_reassessed_and_unknown_first_usage_is_not_zero(self):
        bad=decision();bad['cost_acknowledgments'][0]['neighbor_new_oversaturated_km']=0
        first=response(bad);first['metadata']=dict(tokens=None)
        s,e,_,_=self.exercise([first,response(decision())])
        self.assertEqual(e['status'],'accepted')
        self.assertEqual(s.budget()['known_tokens'],51)
        self.assertEqual(s.budget()['unknown_usage'],1)
        self.assertEqual(e['review_correction']['first_attempt']['validation']['code'],'measured_conclusion')
        self.assertTrue(e['review_correction']['input']['review_feedback']['conclusion_issues'])
        self.assertFalse(e['conclusion_check']['issues'])
        verify_review_permissions(e)
        e['reason']=''
        with self.assertRaises(ValueError):verify_review_permissions(e)

    def test_missing_comparisons_final_permission_reserve_or_budget_prevent_repair(self):
        cases=[dict(programme_final_group=False),dict(reserved_model_requests=1),dict(programme_evidence=dict(groups=[])),
            dict(programme_evidence=dict(groups=[dict(execution_complete=False)])),dict(limit=2)]
        for case in cases:
            with self.subTest(case=case):
                s,e,client,_=self.exercise(**case)
                self.assertEqual(e['status'],'rejected')
                self.assertNotIn('review_correction',e)
                client.request.assert_called_once()

    def test_wrong_phase_or_extra_actions_or_pending_study_prevents_repair(self):
        for case in ('target','action','pending'):
            client=Mock(request=Mock(return_value=response(invalid())))
            s=PlanningSession(client,'离线修正权限',risk_review_protocol=None)
            if case=='pending':s.pending_studies=[dict(id='study_2')]
            observed,ctx=context()
            e=s.decide('oneway_target' if case=='target' else 'parking','j',observed,
                {'candidate':dict(title='仍可执行')} if case=='action' else {},ctx)
            self.assertNotIn('review_correction',e)
            client.request.assert_called_once()

    def test_malformed_or_excerpted_proposals_are_not_repaired_or_leaked(self):
        secret='sk_'+('A'*28)
        malformed=response(invalid());malformed['calls'][0]['arguments']='{'
        redacted=invalid();redacted['risk']=secret
        extra=invalid();extra['private_reasoning']='DO_NOT_RETAIN'
        wrong=invalid();wrong['action']='invented'
        for value in (malformed,response(redacted),response(extra),response(wrong)):
            s,e,client,_=self.exercise([value])
            self.assertNotIn('review_correction',e)
            self.assertNotIn(secret,json.dumps(e))
            self.assertNotIn('DO_NOT_RETAIN',json.dumps(e))
            client.request.assert_called_once()

    def test_second_failure_is_not_overwritten_and_only_one_correction_per_session(self):
        s,e,client,_=self.exercise([response(invalid()),response(decision()),response(invalid())])
        self.assertEqual(e['status'],'accepted')
        second=s.decide('route','j',context()[0],{},context()[1])
        self.assertEqual(second['status'],'rejected')
        self.assertNotIn('review_correction',second)
        self.assertEqual(client.request.call_count,3)

    def test_auditor_rejects_changed_facts_feedback_ledger_and_original_failure(self):
        _,entry,_,_=self.exercise()
        for field in ('observations','menu','feedback','budget','time','first','version','nested','capture','identity'):
            e=copy.deepcopy(entry);c=e['review_correction']
            if field=='observations':c['input']['observations'][0]['new_neighbor_oversaturated_km']=0
            elif field=='menu':c['input']['allowed_actions']['extra']={}
            elif field=='feedback':c['input']['review_feedback']['validation']['code']='invented'
            elif field=='budget':c['input']['model_budget']['used']-=1
            elif field=='time':c['time']='2000-01-01T00:00:00+00:00'
            elif field=='first':
                c['first_attempt']['proposal_excerpt']=decision()
                c['input']=correction_payload(e['input'],c['first_attempt'])
            elif field=='version':c['version']='invented'
            elif field=='nested':c['first_attempt']['review_correction']={'version':'invented'}
            elif field=='capture':c['first_attempt']['proposal_capture']['excerpted']=True
            else:c['first_attempt']['job_id']='different'
            c['observation_digest']=digest(c['input'])
            with self.subTest(field=field),self.assertRaises(ValueError):verify_review_permissions(e)


if __name__=='__main__':unittest.main()
