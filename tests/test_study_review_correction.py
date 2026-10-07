"""Offline recovery of a measured scope error; no provider or native execution."""
import copy
import unittest
from unittest.mock import Mock

from traffic_agent.feedback_planner import PlanningSession, verify_review_permissions
from traffic_agent.planning_budget import route_guard
from traffic_agent.planning_response import HANDOFF_CORRECTION_VERSION, correction_eligible
from traffic_agent.study_handoff import contract as handoff_contract
from test_measured_numbers import observations
from test_planning_response import response


def value(bad=False):
    return dict(action='study_2',summary=('七东线停车1级核心耗时降14.05%（337.8→286.7 pcu·h）。' if bad else
                '七东线核心耗时降14.05%（345.47→296.93 pcu·h）；area_1耗时降15.15%（337.85→286.67 pcu·h）。'),
                reason='继续下一组独立研究，不能借用当前组的治理结果。',risk='这里只复核本组耗时，不代表其他范围都得到改善。',
                next_check='下一组将建立自己的新基准，然后测停车2级、单向及组合。',evidence_refs=['a:parking_1'])


class StudyReviewCorrectionTests(unittest.TestCase):
    def test_next_experiment_language_does_not_hide_an_explicit_measured_level(self):
        from traffic_agent.executed_facts import level_issues,policy_facts
        facts=policy_facts(observations())
        for prefix in ('然后测','后续测试','随后试验','再测'):
            self.assertEqual(level_issues(prefix+'停车2级。','next_check',facts,'a'),[])
        for prefix in ('然后测得','后续测试结果为','已测试','实测采用'):
            self.assertEqual(level_issues(prefix+'停车2级。','summary',facts,'a')[0]['code'],'executed_parking_level')

    def exercise(self,change=None,second=None,limit=6,first=None):
        studies=[dict(id='study_2',title='下一独立单向组',task=dict(scenario='oneway'),runs=4)]
        menu={'study_2':dict(title='下一独立单向组',scenario='oneway',runs=4)}
        context=dict(study_review_correction_protocol=HANDOFF_CORRECTION_VERSION,programme_final_group=False,
                     study_handoff=handoff_contract(menu),route_budget=route_guard(studies,limit-1),
                     reserved_model_requests=2,
                     programme_evidence=dict(groups=[dict(id='study_1',job_id='a',execution_complete=True),
                                                      dict(id='study_2',job_id='b',execution_complete=False)],findings=[]))
        if change:change(context)
        client=Mock(request=Mock(side_effect=[response(value(True) if first is None else first),response(second or value())]))
        session=PlanningSession(client,'离线实测范围修正回归',initial_usage=dict(tokens=10),limit=limit,risk_review_protocol=None)
        session.pending_studies=studies;session.reserved_requests=2
        turn=session.decide('parking','a',observations(),menu,context,allow_stop=False)
        return session,turn,client

    def test_scope_mismatch_is_kept_and_model_revision_is_rechecked(self):
        session,turn,client=self.exercise()
        self.assertEqual(turn['status'],'accepted',turn.get('validation'))
        first=turn['review_correction']['first_attempt']
        self.assertEqual(first['conclusion_check']['issues'][0]['code'],'measured_time_scope')
        self.assertIn('337.8→286.7',first['proposal_excerpt']['summary'])
        self.assertIn('345.47→296.93',turn['summary'])
        self.assertEqual(session.budget()['used'],3)
        self.assertEqual(client.request.call_count,2)
        for key in ('observations','evidence_refs','context','allowed_actions'):
            self.assertEqual(turn['input'][key],turn['review_correction']['input'][key])
        verify_review_permissions(turn)

    def test_legacy_missing_measurements_and_reserved_requests_never_trigger_repair(self):
        changes=[lambda c:c.pop('study_review_correction_protocol'),
                 lambda c:c['programme_evidence']['groups'][0].update(execution_complete=False),
                 lambda c:c['programme_evidence']['groups'][0].update(job_id='other'),
                 lambda c:c.update(reserved_model_requests=5),
                 lambda c:c['route_budget']['choices']['study_2'].update(required_requests=5),
                 lambda c:c['route_budget']['choices']['study_2'].update(required_requests=True),
                 lambda c:c['route_budget']['choices']['study_2'].update(feasible=False)]
        for change in changes:
            session,turn,client=self.exercise(change=change)
            self.assertEqual(turn['status'],'rejected')
            self.assertFalse(correction_eligible(turn))
            self.assertNotIn('review_correction',turn)
            self.assertNotIn('selected',turn)
            client.request.assert_called_once()
        _,turn,client=self.exercise(limit=4)
        self.assertNotIn('review_correction',turn)
        client.request.assert_called_once()

    def test_second_numeric_error_does_not_pass_or_trigger_another_request(self):
        bad=value();bad['summary']='核心耗时降15.15%（345.47→296.93 pcu·h）。'
        session,turn,client=self.exercise(second=bad)
        self.assertEqual(turn['status'],'rejected')
        self.assertTrue(session.halted)
        self.assertEqual(turn['conclusion_check']['issues'][0]['code'],'measured_time_arithmetic')
        self.assertEqual(client.request.call_count,2)
        self.assertNotIn('selected',turn)
        verify_review_permissions(turn)

    def test_malformed_action_cannot_crash_the_error_handler_or_request_a_repair(self):
        for action in ([],{},42,None):
            bad=value(True);bad['action']=action
            session,turn,client=self.exercise(first=bad)
            self.assertEqual(turn['status'],'rejected')
            self.assertEqual(turn['validation']['code'],'action_not_allowed')
            self.assertTrue(session.halted)
            self.assertNotIn('review_correction',turn)
            client.request.assert_called_once()

    def test_forced_legacy_protocol_or_changed_action_cannot_recertify_repair(self):
        _,turn,_=self.exercise()
        for kind in ('version','action','group','facts'):
            changed=copy.deepcopy(turn)
            if kind=='version':changed['review_correction']['version']='final-review-correction-v1'
            elif kind=='action':changed['action']='stop'
            elif kind=='group':changed['input']['context']['programme_evidence']['groups'][0]['execution_complete']=False
            else:changed['review_correction']['input']['observations'][0]['metrics']['core']['running_time_pcu_h']=337.8
            with self.subTest(kind=kind),self.assertRaises(ValueError):verify_review_permissions(changed)


if __name__=='__main__':unittest.main()
