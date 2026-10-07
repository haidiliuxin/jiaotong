"""Real planning controller with synthetic transport; no provider/native calls."""
import copy
import io
import json
import unittest
from sealed_planning_fixtures import load_sealed
from unittest.mock import Mock,patch
from urllib.error import URLError

import test_initial_planner as fixtures
from test_natural_plan import experiment,model_reply
from traffic_agent.feedback_planner import digest
from traffic_agent.initial_planner import evidence,verify_initial
from traffic_agent.llm_client import ModelError
from traffic_agent.planning_attempt import listing,safe_proposal
from traffic_agent.runner import now
from traffic_agent.task import Task
from traffic_agent import hypothesis_policy_patch as policy_patch


def questions(valid=False):
    rows=[
        dict(hypothesis='长江村路的单向组织（含组合）方案能通过全部推荐约束。',
             evidence_needed='分别检查本组单向及组合的实际约束和控制响应。',
             criterion=dict(test='admissible',studies=[1],policies='all',quantifier='all')),
        dict(hypothesis='长江村路交通组织（单向或组合）在核心总耗时上严格优于本组仅停车方案。',
             evidence_needed='各交通组织措施的核心耗时分别低于同需求仅停车的耗时。',
             criterion=dict(test='core_advantage_over_parking',studies=[1],policies='parking',quantifier='all')),
        dict(hypothesis='长江村路组合方案在核心耗时上分别优于两个单项。',
             evidence_needed='组合耗时分别与本组两个单项配对；不能证明协同成因。',
             criterion=dict(test='combination_core_advantage',studies=[1],policies='all',quantifier='all')),
        dict(hypothesis='七东线至少一个停车方案降低本组冻结核心总耗时。',
             evidence_needed='本组新基准与停车措施的核心耗时。',
             criterion=dict(test='core_time_decrease',studies=[2],policies='parking',quantifier='any')),
    ]
    if valid:
        for h,policy in zip(rows,('control_and_combined','control_and_combined','combined','parking')):
            h['criterion']['policies']=policy
    return rows


def reply(valid=False,assessment=None,studies=None):
    return model_reply(studies or [experiment(['parking','oneway'],road='长江村路'),experiment(['parking'],road='七东线')],
                       assessment=questions(valid) if assessment is None else assessment)


def patch_reply(selections=None,arguments=None,name=policy_patch.NAME):
    if selections is None:selections=dict(H1='control_and_combined',H2='control_and_combined',H3='combined')
    return dict(calls=[dict(name=name,arguments=json.dumps(selections) if arguments is None else arguments)])


class InitialHypothesisReviewTests(unittest.TestCase):
    setup_preview=fixtures.InitialPlannerTests.setup_preview
    records=fixtures.InitialPlannerTests.records

    def preview(self,text=fixtures.PROGRAMME_REQUEST):
        return self.manager.preview(dict(request=text,matcher='model'))

    def test_corrects_all_criteria_once_through_both_actual_wire_histories(self):
        for wire in ('chat_completions','responses'):
            with self.subTest(wire=wire):
                self.setup_preview([reply(),patch_reply()],wire,catalog=fixtures.MIXED_CAT)
                result=self.preview();first,last=self.records()
                self.assertEqual(result['status'],'ready');self.assertEqual(result['allocated_runs'],6)
                self.assertEqual(result['model_budget']['minimum_required'],6)
                self.assertEqual(result['model_budget']['used'],2)
                self.assertEqual(first['validation_code'],'invalid_hypothesis_criterion')
                self.assertEqual([i['hypothesis_id'] for i in first['hypothesis_check']['issues']],['H1','H2','H3'])
                self.assertEqual(first['proposal']['assessment'][1]['criterion']['policies'],'parking')
                self.assertEqual(first['proposal']['assessment'][1]['criterion_status'],'invalid')
                self.assertEqual(last['proposal']['assessment'][1]['criterion']['policies'],'control_and_combined')
                self.assertEqual([h['hypothesis'] for h in first['proposal']['assessment']],
                                 [h['hypothesis'] for h in last['proposal']['assessment']])
                self.assertEqual([a['status'] for a in (first,last)],['validation_failed','ready'])
                body=json.loads(self.opener.open.call_args_list[1].args[0].data)
                history=body['messages'][1:] if wire=='chat_completions' else body['input']
                feedback=json.loads(history[-1]['content' if wire=='chat_completions' else 'output'])
                self.assertEqual(feedback['hypothesis_check'],first['hypothesis_check'])
                tool=body['tools'][0]['function'] if wire=='chat_completions' else body['tools'][0]
                if wire=='responses':self.assertEqual(tool,policy_patch.tool_for(first['hypothesis_check']))
                else:self.assertEqual(tool['name'],policy_patch.NAME)
                params=tool['parameters']
                self.assertFalse(params['additionalProperties']);self.assertTrue(tool['strict'])
                self.assertEqual(set(params['required']),{'H1','H2','H3'})
                self.assertEqual(params['properties']['H3'],dict(type='string',enum=['combined']))
                self.assertEqual(body['messages'][0]['content'] if wire=='chat_completions' else body['instructions'],policy_patch.INSTRUCTIONS)
                self.assertEqual(last['policy_patch']['selections'],json.loads(patch_reply()['calls'][0]['arguments']))
                self.assertEqual(last['policy_patch']['source_attempt_id'],first['id'])
                self.assertEqual(last['proposal_origin'],policy_patch.ORIGIN)
                self.assertIn(fixtures.PRIVATE,json.dumps(history))
                saved=''.join(p.read_text('utf-8') for p in self.root.rglob('*.json'))
                self.assertNotIn(fixtures.PRIVATE,saved);self.assertNotIn('UNIT_TEST_ONLY',saved)
                self.assertEqual(verify_initial(result,dict(created=now(),initial_planning_digest=digest(evidence(result)))),2)
                shown=next(a for a in listing(self.root) if a['ordinal']==1)
                self.assertEqual(len(shown['hypothesis_check']['issues']),3)
                self.assertEqual(shown['proposal']['assessment'][1]['criterion']['policies'],'parking')
                shown_last=next(a for a in listing(self.root) if a['ordinal']==2)
                self.assertEqual(shown_last['policy_patch']['status'],'applied')
                self.assertEqual(shown_last['proposal_origin'],policy_patch.ORIGIN)
                self.native.assert_not_called();self.assertFalse((self.root/'jobs').exists())
                self.assertEqual(self.preview(),result);self.assertEqual(self.opener.open.call_count,2)
                patch.stopall()

    def test_second_bad_criterion_and_transport_failure_never_get_a_third_call(self):
        for second in (reply(),URLError('local transport failure')):
            with self.subTest(second=type(second).__name__):
                self.setup_preview([reply(),second,patch_reply()],catalog=fixtures.MIXED_CAT)
                with self.assertRaises(ModelError):self.preview()
                self.assertEqual(self.opener.open.call_count,2);self.native.assert_not_called()
                self.assertEqual(len(self.records()),2);self.assertEqual(self.manager.tickets,{})
                last=self.records()[-1]
                self.assertEqual(last['status'],'model_failed' if isinstance(second,Exception) else 'validation_failed')
                if not isinstance(second,Exception):
                    self.assertEqual(last['usage']['tokens'],100)
                    self.assertEqual(last['policy_patch']['status'],'rejected')
                    self.assertNotIn('proposal',last)
                patch.stopall()

    def test_cannot_drop_rephrase_or_rescope_original_questions(self):
        mutations=[lambda h:h.pop(),lambda h:h[0].update(hypothesis='新弱化问题'),
                   lambda h:h[0].update(evidence_needed='新的证据要求'),
                   lambda h:h[0]['criterion'].update(quantifier='any'),
                   lambda h:h[0]['criterion'].update(test='unavailable'),
                   lambda h:h[3]['criterion'].update(policies='all'),
                   lambda h:h[0]['criterion'].update(studies=[2])]
        for mutate in mutations:
            with self.subTest(change=mutate):
                h=questions(True);mutate(h)
                self.setup_preview([reply(),reply(assessment=h)],catalog=fixtures.MIXED_CAT)
                with self.assertRaises((ModelError,ValueError)):self.preview()
                self.assertEqual(self.opener.open.call_count,2);self.native.assert_not_called()
                self.assertEqual(self.manager.tickets,{})
                patch.stopall()

    def test_cannot_change_execution_design_during_criterion_repair(self):
        studies=[experiment(['parking','oneway'],road='长江村路',target='L_3237_3236'),experiment(['parking'],road='七东线')]
        catalog=dict(fixtures.MIXED_CAT,targets={'L_3237_3236':dict(kind='oneway',nodes=[3237,3236],road='长江村路')})
        self.setup_preview([reply(),reply(True,studies=studies)],catalog=catalog)
        with self.assertRaisesRegex(ModelError,'整份重写方案'):self.preview()
        self.assertEqual(self.opener.open.call_count,2);self.native.assert_not_called()

    def test_seventh_slot_repairs_exact_budget_but_unknown_study_does_not(self):
        h=questions()[:1];h[0]['hypothesis']='本次停车措施的观测问题';h[0]['criterion']['policies']='combined'
        text=fixtures.REQUEST.replace('不比较强度','并比较强度').replace('最多3次','最多5次')
        self.setup_preview([model_reply([experiment(['parking'])],assessment=h),patch_reply(dict(H1='parking'))],catalog=fixtures.CAT)
        result=self.preview(text)
        self.assertEqual(result['status'],'ready')
        self.assertEqual(self.opener.open.call_count,2);self.native.assert_not_called()
        check=self.records()[0]['hypothesis_check']
        self.assertTrue(check['correction_feasible']);self.assertEqual(check['budget_check']['model'],dict(required=6,used=1,limit=7))
        self.assertEqual(result['model_budget']['minimum_required'],7)
        from traffic_agent import initial_hypothesis_review as review
        exhausted=copy.deepcopy(check['budget_check']);exhausted['model']['required']=7
        self.assertFalse(review.inspect(h,check['design'],exhausted,check['version'],check['wording_protocol'])[1]['correction_feasible'])
        patch.stopall()
        h=questions();h[0]['criterion']['studies']=[5]
        self.setup_preview([reply(assessment=h)],catalog=fixtures.MIXED_CAT)
        with self.assertRaises(ModelError):self.preview()
        self.assertEqual(self.opener.open.call_count,1);self.native.assert_not_called()

    def test_seventh_slot_exposes_exact_mixed_policy_field_from_failed_shape(self):
        from traffic_agent import initial_hypothesis_review as review
        question=dict(hypothesis='至少存在单向组织或其组合方案在长江村路通过全部约束并保护邻区',
                      evidence_needed='分别检查单向与组合的推荐资格和邻区约束。',
                      criterion=dict(test='admissible',studies=[1],policies='all',quantifier='any'))
        scope=dict(studies=[dict(source_studies=[1],task=dict(scenario='oneway',road_name='长江村路',
                    demand_factors=[1.0],max_neighbor_time_increase_pct=0.5,max_new_oversaturated_km=0.1))],aliases=[])
        budget=dict(fits=True,model=dict(required=6,used=1,limit=7))
        _,check=review.inspect([question],scope,budget)
        self.assertEqual(check['issues'][0]['allowed_policies'],['control_and_combined'])
        self.assertTrue(check['correction_feasible'])
        corrected=copy.deepcopy(question);corrected['criterion']['policies']='control_and_combined'
        self.assertEqual(review.inspect([corrected],scope,budget)[1]['issues'],[])
        self.assertEqual(corrected['hypothesis'],question['hypothesis'])
        self.assertEqual(corrected['criterion']['quantifier'],'any')

    def test_non_policy_wording_conflict_is_not_repaired_by_weakening_the_test(self):
        h=questions(True)
        h[3]['hypothesis']='七东线至少一个停车方案降低本组核心总耗时并通过全部推荐约束。'
        self.setup_preview([reply(assessment=h),reply(True)],catalog=fixtures.MIXED_CAT)
        with self.assertRaises(ModelError):self.preview()
        self.assertEqual(self.opener.open.call_count,1);self.native.assert_not_called()
        check=self.records()[0]['hypothesis_check']
        self.assertEqual(check['issues'][0]['allowed_policies'],[])
        self.assertFalse(check['correction_feasible'])

    def test_missing_transport_history_and_malformed_criteria_do_not_spend_correction(self):
        self.setup_preview([],catalog=fixtures.MIXED_CAT)
        self.client.request=Mock(return_value=reply())
        with self.assertRaises(ModelError):self.preview()
        self.client.request.assert_called_once();self.native.assert_not_called()
        patch.stopall()
        for bad in ({},dict(test='unknown',policies='parking',studies=[1],quantifier='all')):
            h=questions();h[0]['criterion']=bad
            self.setup_preview([reply(assessment=h)],catalog=fixtures.MIXED_CAT)
            with self.assertRaises(ModelError):self.preview()
            self.assertEqual(self.opener.open.call_count,1);self.native.assert_not_called()
            patch.stopall()

    def test_prerequisite_and_criterion_repairs_share_one_initial_slot(self):
        self.setup_preview([fixtures.wrong_prerequisite(),reply(),reply(True)],catalog=fixtures.MIXED_CAT)
        with self.assertRaises(ModelError):self.preview()
        self.assertEqual(self.opener.open.call_count,2);self.native.assert_not_called()
        self.assertEqual([r['validation_code'] for r in self.records()],['invalid_prerequisite','invalid_hypothesis_criterion'])

    def test_resealed_receipt_cannot_hide_changed_question_errors_or_protocol(self):
        self.setup_preview([reply(),patch_reply()],catalog=fixtures.MIXED_CAT)
        result=self.preview()
        mutations=[lambda r:r['initial_planning_attempts'][0]['hypothesis_check']['issues'].pop(),
                   lambda r:r['initial_planning_attempts'][0]['proposal']['assessment'][0].update(hypothesis='被改写的问题'),
                   lambda r:r.update(initial_review_protocol='initial-plan-review-v2'),
                   lambda r:r.update(initial_review_protocol='initial-plan-review-v3'),
                   lambda r:r['initial_planning_attempts'][1].update(validation_feedback={}),
                   lambda r:r['initial_planning_attempts'][1]['budget_check']['model'].update(used=1),
                   lambda r:r['initial_planning_attempts'][1]['policy_patch']['selections'].update(H4='parking'),
                   lambda r:r['initial_planning_attempts'][1]['policy_patch'].update(source_attempt_id='preview_'+'0'*32),
                   lambda r:r['initial_planning_attempts'][1].pop('proposal_origin'),
                   lambda r:r['initial_planning_attempts'][1]['proposal'].update(reason='非模型提交的新理由'),
                   lambda r:r['initial_planning_attempts'][0]['hypothesis_check'].pop('wording_protocol'),
                   lambda r:r['initial_planning_attempts'][1]['usage'].update(request_schema_sha256='0'*64)]
        for mutate in mutations:
            bad=copy.deepcopy(result);mutate(bad)
            with self.subTest(change=mutate),self.assertRaises(ValueError):
                verify_initial(bad,dict(created=now(),initial_planning_digest=digest(evidence(bad))))
        # Removing both copies must not silently downgrade the audit to v1.
        bad=copy.deepcopy(result)
        bad['initial_planning_attempts'][0]['hypothesis_check'].pop('wording_protocol')
        bad['initial_planning_attempts'][1]['validation_feedback']['hypothesis_check'].pop('wording_protocol')
        with self.assertRaisesRegex(ValueError,'口径协议'):
            verify_initial(bad,dict(created=now(),initial_planning_digest=digest(evidence(bad))))

    def test_invalid_combinations_are_retained_but_unknown_fields_are_never_archived(self):
        draft=json.loads(reply()['calls'][0]['arguments']);draft['secret']='UNIT_PRIVATE'
        saved=safe_proposal(draft)
        self.assertEqual(saved['assessment'][1]['criterion']['policies'],'parking')
        self.assertEqual(safe_proposal(saved),saved)
        draft['decision']['assessment'][0]['criterion']['secret']='UNIT_PRIVATE'
        saved=safe_proposal(draft)
        self.assertNotIn('UNIT_PRIVATE',json.dumps(saved))
        self.assertNotIn('criterion',saved['assessment'][0])

    def test_corrected_receipt_is_frozen_before_worker_starts(self):
        h=[dict(hypothesis='停车措施降低核心耗时',evidence_needed='本次新基准与治理耗时',
                criterion=dict(test='core_time_decrease',studies=[1],policies='combined',quantifier='any'))]
        self.setup_preview([model_reply([experiment(['parking'])],assessment=h),
                            patch_reply(dict(H1='parking'))])
        result=self.preview(fixtures.REQUEST)
        agent=Mock(job_id='LOCAL_CRITERION_REPAIR',job=self.root/'jobs/LOCAL_CRITERION_REPAIR',
                   task=Task.from_dict(result['task']),state=dict(status='created',runs=[],run_count=0))
        self.native.return_value=agent
        agent.run.side_effect=RuntimeError('synthetic stop before native execution')
        launched=self.manager.launch(result['ticket']);self.manager.active.join(5)
        self.assertFalse(self.manager.active.is_alive())
        saved=json.loads((self.root/'dispatches'/f"{launched['id']}.json").read_text('utf-8'))
        plan=json.loads((self.root/'programmes'/launched['id']/'plan.json').read_text('utf-8'))
        self.assertEqual(saved['status'],'failed')
        self.assertEqual(saved['model_budget']['used'],2)
        self.assertEqual(verify_initial(saved,plan),2)
        self.assertEqual(self.opener.open.call_count,2)

    def test_incomplete_extra_unknown_and_duplicate_patch_fields_stop_with_cost_preserved(self):
        valid=json.loads(patch_reply()['calls'][0]['arguments'])
        cases=[dict(H3='combined'),dict(valid,H4='parking'),dict(valid,hypothesis='UNIT_PRIVATE'),
               dict(valid,H1='all'),dict(valid,H2='UNIT_PRIVATE'),dict(valid,H3={'secret':'UNIT_PRIVATE'}),[]]
        replies=[patch_reply(arguments=json.dumps(v)) for v in cases]
        replies+=[patch_reply(arguments='{"H1":"control_and_combined","H2":"control_and_combined","H3":"all","H3":"combined"}'),
                  patch_reply(name='UNIT_PRIVATE'),patch_reply(arguments='not json UNIT_PRIVATE')]
        for second in replies:
            with self.subTest(parameters=second):
                self.setup_preview([reply(),second],catalog=fixtures.MIXED_CAT)
                with self.assertRaises(ModelError):self.preview()
                last=self.records()[-1]
                self.assertEqual(last['status'],'validation_failed');self.assertEqual(last['usage']['tokens'],100)
                self.assertEqual(last['policy_patch']['status'],'rejected');self.assertNotIn('proposal',last)
                self.assertNotIn('UNIT_PRIVATE',json.dumps(self.records()))
                self.assertEqual(self.opener.open.call_count,2);self.native.assert_not_called()
                self.assertEqual(self.manager.tickets,{})
                patch.stopall()

    def test_multiple_patch_calls_cannot_be_combined_or_hide_a_third_request(self):
        self.setup_preview([reply()],catalog=fixtures.MIXED_CAT)
        data=json.load(fixtures.wire_response(patch_reply(),ordinal=2))
        calls=data['choices'][0]['message']['tool_calls']
        calls.append(dict(calls[0],id='extra_call'))
        self.opener.open.side_effect=[fixtures.wire_response(reply()),io.BytesIO(json.dumps(data).encode())]
        with self.assertRaises(ModelError):self.preview()
        last=self.records()[-1]
        self.assertEqual(last['status'],'validation_failed');self.assertEqual(last['policy_patch']['tool_call_count'],2)
        self.assertEqual(last['usage']['tokens'],100);self.native.assert_not_called()
        self.assertEqual(self.opener.open.call_count,2)

    def test_legacy_v3_and_v4_original_fixtures_still_audit_with_original_wording(self):
        # Original offline fixture sealed before this protocol change, not a
        # newly repaired result with its version relabelled for the test.
        for stem,version,has_patch in (('criterion_repair','initial-plan-review-v3',False),
                                       ('policy_patch','initial-plan-review-v4',True)):
            with self.subTest(version=version):
                result, = load_sealed(stem+'_helpers')
                self.assertEqual(result['initial_review_protocol'],version)
                self.assertEqual(result['question_wording_protocol'],'bounded-question-wording-v1')
                self.assertNotIn('wording_protocol',result['initial_planning_attempts'][0]['hypothesis_check'])
                self.assertEqual('policy_patch' in result['initial_planning_attempts'][-1],has_patch)
                self.assertEqual(verify_initial(result,dict(created=now(),initial_planning_digest=digest(evidence(result)))),2)

    def test_preserved_rc17_public_questions_accept_an_explicit_synthetic_patch_without_rewording(self):
        # Actual public questions/design plus synthetic missing alternatives and
        # patch reply. This is an offline boundary test, not a live replay/pass.
        originals=sorted(load_sealed('rc17_raw_evidence'),key=lambda a:a['ordinal'])
        first,last=originals
        self.assertEqual(last['status'],'validation_failed')
        public=first['proposal']
        h=[{k:v for k,v in item.items() if k in ('hypothesis','evidence_needed','criterion')} for item in public['assessment']]
        studies=[dict(study=s['study'],strategies=s['strategies'],road_name=s['road'],target_ref=s['target'],
                      demand_profile=s['demand_profile'],rationale=s['rationale']) for s in public['studies']]
        self.setup_preview([model_reply(studies,assessment=h,goal=public['goal'],reasoning=public['reason']),
                            patch_reply(dict(H2='combined'))],catalog=fixtures.MIXED_CAT)
        result=self.preview(first['request'])
        repaired=result['initial_planning_attempts'][-1]['proposal']
        self.assertEqual([x['hypothesis'] for x in repaired['assessment']],[x['hypothesis'] for x in public['assessment']])
        self.assertIn('已试措施组合',repaired['assessment'][1]['hypothesis'])
        self.assertIn('已试组合措施',last['proposal']['assessment'][1]['hypothesis'])
        self.assertEqual(result['allocated_runs'],6);self.assertEqual(result['model_budget']['minimum_required'],6)
        self.assertEqual(verify_initial(result,dict(created=now(),initial_planning_digest=digest(evidence(result)))),2)
        self.native.assert_not_called();self.assertEqual(self.opener.open.call_count,2)

    def test_sealed_rc18_public_note_is_not_a_second_condition_and_v1_failure_stays_reproducible(self):
        from traffic_agent import initial_hypothesis_review as review,question_wording as wording
        original, = load_sealed('rc18_raw_evidence')
        public=original['proposal'];check=original['hypothesis_check']
        self.assertEqual(review.inspect(public['assessment'],check['design'],check['budget_check'],check['version'],wording.LEGACY_VERSION)[1],check)
        self.assertEqual(original['status'],'validation_failed');self.assertFalse(check['correction_feasible'])
        studies=[dict(study=s['study'],strategies=s['strategies'],road_name=s['road'],target_ref=s['target'],
                      demand_profile=s['demand_profile'],rationale=s['rationale']) for s in public['studies']]
        self.setup_preview([model_reply(studies,assessment=public['assessment'],goal=public['goal'],reasoning=public['reason'])],catalog=fixtures.MIXED_CAT)
        result=self.preview(original['request'])
        self.assertEqual(result['status'],'ready');self.assertEqual(self.opener.open.call_count,1)
        self.assertEqual(result['initial_planning_attempts'][0]['proposal']['assessment'],public['assessment'])
        self.assertEqual(result['assessment'][0]['evaluation']['interpretation']['wording_notes'][0]['text'],'且判定与是否通过推荐约束分开')
        self.assertEqual(verify_initial(result,dict(created=now(),initial_planning_digest=digest(evidence(result)))),1)
        self.native.assert_not_called()


if __name__=='__main__':unittest.main()
