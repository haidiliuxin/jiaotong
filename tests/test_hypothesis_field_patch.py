"""Synthetic transport around the real initial controller; no paid requests."""
import copy
import json
import unittest
from sealed_planning_fixtures import load_sealed
from unittest.mock import patch
import test_initial_planner as fixtures
from test_natural_plan import experiment,model_reply
from traffic_agent import hypothesis_field_patch as fields,initial_hypothesis_review as review
from traffic_agent.feedback_planner import digest
from traffic_agent.initial_planner import evidence,verify_initial
from traffic_agent.llm_client import ModelError
from traffic_agent.planning_attempt import listing
from traffic_agent.runner import now

def question(text='至少一种停车措施降低七东线核心耗时',quantifier='all',policies='parking'):
    return dict(hypothesis=text,evidence_needed='本组同需求新基准与措施的核心耗时',
                criterion=dict(test='core_time_decrease',studies=[1],policies=policies,quantifier=quantifier))

def proposal(h):return model_reply([experiment(['parking'])],assessment=[h])
def selection(value,name=fields.NAME):return dict(calls=[dict(name=name,arguments=json.dumps(value) if not isinstance(value,str) else value)])

class HypothesisFieldPatchTests(unittest.TestCase):
    setup_preview=fixtures.InitialPlannerTests.setup_preview
    records=fixtures.InitialPlannerTests.records
    def preview(self,text=fixtures.REQUEST):return self.manager.preview(dict(request=text,matcher='model'))

    def test_original_existential_and_universal_meanings_are_restored_with_closed_fields_on_both_wires(self):
        for wire in ('responses','chat_completions'):
            for text,wrong,right in [('至少一种停车措施降低七东线核心耗时','all','any'),('所有停车措施降低七东线核心耗时','any','all')]:
                with self.subTest(wire=wire,text=text):
                    h=question(text,wrong);self.setup_preview([proposal(h),selection(dict(H1=dict(quantifier=right)))],wire)
                    result=self.preview();first,last=self.records()
                    self.assertEqual(first['hypothesis_check']['issues'][0]['allowed_fields'],dict(quantifier=[right]))
                    body=json.loads(self.opener.open.call_args_list[1].args[0].data)
                    tool=body['tools'][0]['function'] if wire=='chat_completions' else body['tools'][0]
                    prop=tool['parameters']['properties']['H1']
                    self.assertEqual(tool['name'],fields.NAME);self.assertTrue(tool['strict'])
                    self.assertEqual(prop,dict(type='object',properties=dict(quantifier=dict(type='string',enum=[right])),required=['quantifier'],additionalProperties=False))
                    self.assertEqual(last['proposal']['assessment'][0],dict(h,criterion=dict(h['criterion'],quantifier=right)))
                    self.assertEqual(last['proposal_origin'],fields.ORIGIN)
                    self.assertEqual(last['policy_patch']['selections'],dict(H1=dict(quantifier=right)))
                    self.assertEqual(verify_initial(result,dict(created=now(),initial_planning_digest=digest(evidence(result)))),2)
                    shown=next(r for r in listing(self.root) if r['ordinal']==2)
                    self.assertEqual(shown['policy_patch']['selections'],dict(H1=dict(quantifier=right)))
                    self.native.assert_not_called();self.assertEqual(self.opener.open.call_count,2);patch.stopall()

    def test_two_wrong_fields_require_explicit_selections_in_one_shared_request(self):
        self.setup_preview([proposal(question(policies='combined')),selection(dict(H1=dict(policies='parking',quantifier='any')))])
        result=self.preview();first,last=self.records()
        allowed=first['hypothesis_check']['issues'][0]['allowed_fields']
        self.assertEqual(allowed['quantifier'],['any']);self.assertIn('parking',allowed['policies'])
        self.assertEqual(verify_initial(result,dict(created=now(),initial_planning_digest=digest(evidence(result)))),2)
        self.assertEqual(last['policy_patch']['selections'],dict(H1=dict(policies='parking',quantifier='any')))
        self.native.assert_not_called()

    def test_cannot_weaken_a_valid_universal_question_or_infer_a_missing_quantifier(self):
        for h in (question('所有停车措施降低核心耗时',quantifier='all',policies='combined'),
                  question('停车措施降低核心耗时',quantifier='all',policies='combined')):
            self.setup_preview([proposal(h),selection(dict(H1=dict(quantifier='any')))])
            with self.assertRaises(ModelError):self.preview()
            first,last=self.records();self.assertNotIn('allowed_fields',first['hypothesis_check']['issues'][0])
            self.assertEqual(last['policy_patch']['version'],'hypothesis-policy-patch-v1')
            self.assertEqual(self.opener.open.call_count,2);self.native.assert_not_called();patch.stopall()

    def test_malformed_extra_omitted_duplicate_and_whole_plan_changes_are_rejected_with_usage(self):
        replies=[selection(x) for x in ({},dict(H1={}),dict(H1=dict(quantifier='all')),dict(H1=dict(quantifier='any',policies='parking')),
                   dict(H1=dict(quantifier='any'),H2=dict(quantifier='any')),dict(H1=dict(quantifier='UNIT_PRIVATE')),
                   {'H1':{'quantifier':'any','hypothesis':'UNIT_PRIVATE'}},[])]
        replies+=[selection('{"H1":{"quantifier":"any","quantifier":"all"}}'),proposal(question(quantifier='any'))]
        for second in replies:
            with self.subTest(reply=second):
                self.setup_preview([proposal(question()),second])
                with self.assertRaises(ModelError):self.preview()
                last=self.records()[-1];self.assertEqual(last['status'],'validation_failed')
                self.assertEqual(last['usage']['tokens'],100);self.assertEqual(last['policy_patch']['status'],'rejected')
                self.assertNotIn('proposal',last);self.assertNotIn('UNIT_PRIVATE',json.dumps(self.records()))
                self.native.assert_not_called();self.assertEqual(self.opener.open.call_count,2);patch.stopall()

    def test_resealed_digest_cannot_hide_changed_meaning_receipt_schema_or_protocol(self):
        self.setup_preview([proposal(question()),selection(dict(H1=dict(quantifier='any')))])
        result=self.preview()
        for mutate in (lambda r:r.update(initial_review_protocol='initial-plan-review-v4'),
                       lambda r:r['initial_planning_attempts'][1]['policy_patch']['selections']['H1'].update(quantifier='all'),
                       lambda r:r['initial_planning_attempts'][1]['proposal']['assessment'][0].update(hypothesis='改成另一个问题'),
                       lambda r:r['initial_planning_attempts'][1]['usage'].update(request_schema_sha256='0'*64),
                       lambda r:r['initial_planning_attempts'][0]['hypothesis_check']['issues'][0]['allowed_fields'].update(quantifier=['any','all'])):
            bad=copy.deepcopy(result);mutate(bad)
            with self.assertRaises((ValueError,ModelError)):verify_initial(bad,dict(created=now(),initial_planning_digest=digest(evidence(bad))))

    def test_rc19_original_failure_replays_under_v2_and_new_field_reply_keeps_all_public_questions(self):
        original, = load_sealed('rc19_raw_evidence')
        public=original['proposal'];check=original['hypothesis_check']
        self.assertEqual(review.inspect(public['assessment'],check['design'],check['budget_check'],check['version'],check['wording_protocol'])[1],check)
        studies=[dict(study=s['study'],strategies=s['strategies'],road_name=s['road'],target_ref=s['target'],demand_profile=s['demand_profile'],rationale=s['rationale']) for s in public['studies']]
        self.setup_preview([model_reply(studies,assessment=public['assessment'],goal=public['goal'],reasoning=public['reason']),
                            selection(dict(H1=dict(quantifier='any')))],catalog=fixtures.MIXED_CAT)
        result=self.preview(original['request']);final=result['initial_planning_attempts'][-1]['proposal']
        expected=copy.deepcopy(public);expected['assessment'][0]['criterion']['quantifier']='any'
        self.assertEqual(final,expected);self.assertEqual(result['model_budget']['minimum_required'],6)
        self.assertEqual(verify_initial(result,dict(created=now(),initial_planning_digest=digest(evidence(result)))),2)
        self.native.assert_not_called();self.assertEqual(self.opener.open.call_count,2)

if __name__=='__main__':unittest.main()
