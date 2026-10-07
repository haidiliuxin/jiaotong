"""Synthetic selection references; no provider, credential or native calls."""
import copy
import hashlib
import json
import unittest
from unittest.mock import Mock

from traffic_agent import choice_evidence as ce
from traffic_agent.feedback_planner import PlanningSession, decision_tool, verify_review_permissions
from traffic_agent.planning_response import ResponseValidationError, capture_proposal, CHOICE_CORRECTION_VERSION
from test_feedback_planner import choice_basis, reply


def payload(phase='parking', single=False, trial=False, version=ce.V2_VERSION):
    roads=[dict(index=i,name='合成路',from_node=i+1,to_node=i+2,speed_kmh=10+i,
                vc=1.2,parking=3,flow_pcu_h=1000+i) for i in range(2)]
    options={f'candidate_{i}':dict(title=f'合成候选{i}',region=f'area_{i}',
             changes=[dict(from_node=r['from_node'],to_node=r['to_node'],parking=2)]) for i,r in enumerate(roads)}
    observed=[dict(ref='job:baseline',run='baseline',demand_factor=1,changes=[],core_roads=roads,
                  spatial=dict(regions=[dict(id=f'area_{i}',title=f'合成区域{i}',roads=[r]) for i,r in enumerate(roads)]))]
    if phase=='oneway_target':
        options={k:dict(title=v['title'],closed_direction=[r['from_node'],r['to_node']],roads=[r],
                       baseline_car_flow=946.55,topology=dict(eligible=True,bus_routes_checked=2))
                 for (k,v),r in zip(options.items(),roads)}
    if single:options.pop('candidate_1')
    if trial:
        observed.append(dict(ref='job:trial',run='trial',demand_factor=1,changes=[{'parking':2}],
                             delta=dict(core=dict(running_time_change_pct=-20)),eligible=False))
    p=dict(phase=phase,allowed_actions=options,observations=observed,evidence_refs=[o['ref'] for o in observed],
           context=dict(choice_basis_protocol=version,measured_conclusion=dict(costs=[]),
                        review_permissions=dict(programme_review_actions=[])))
    p['context']['choice_observations']=ce.observations(p)
    return p


class ChoiceEvidenceTests(unittest.TestCase):
    def proposal(self,p,action='candidate_0'):
        return dict(action=action,evidence_refs=list(p['evidence_refs']),selection_basis=choice_basis(action,p))

    def test_index_only_references_frozen_inputs_and_units(self):
        for phase in ('parking','oneway_target'):
            p=payload(phase);facts=p['context']['choice_observations']
            self.assertTrue(ce.covered(p))
            self.assertEqual({s['ref'] for s in facts['sources']},{'job:baseline'})
            self.assertFalse(any('speed_kmh' in s or 'baseline_car_flow' in s for s in facts['sources']))
            self.assertEqual(facts['units']['baseline_car_flow'],'veh/h')
            self.assertEqual(facts['units']['flow_pcu_h'],'pcu/h')
            ce.validate(self.proposal(p),p)
        facts['units']['baseline_car_flow']='changed'
        self.assertEqual(ce.UNITS['baseline_car_flow'],'veh/h')

    def test_selected_and_alternative_observations_both_required(self):
        p=payload();valid=self.proposal(p)
        for remove in (0,1):
            v=copy.deepcopy(valid);v['selection_basis']['observed_ids'].pop(remove)
            with self.subTest(remove=remove),self.assertRaises(ResponseValidationError):ce.validate(v,p)

    def test_v3_separates_chosen_and_deferred_evidence(self):
        p=payload(version=ce.V3_VERSION);valid=self.proposal(p)
        basis=valid['selection_basis']
        self.assertEqual(basis['observed_ids'],['area:job:baseline:area_0'])
        self.assertEqual(basis['alternative_observed_id'],'area:job:baseline:area_1')
        self.assertIn('alternative_observed_id',ce.schema(p)['anyOf'][1]['properties'])
        ce.validate(valid,p);ce.verify(dict(valid,input=p,status='accepted'))
        for bad in (None,'area:job:baseline:area_0','future:result'):
            altered=copy.deepcopy(valid);altered['selection_basis']['alternative_observed_id']=bad
            with self.subTest(bad=bad),self.assertRaises(ResponseValidationError):ce.validate(altered,p)
        old=payload(version=ce.V2_VERSION)
        self.assertNotIn('alternative_observed_id',ce.schema(old)['anyOf'][1]['properties'])

    def test_v3_requires_latest_feedback_and_handles_shared_baseline(self):
        p=payload(trial=True,version=ce.V3_VERSION);valid=self.proposal(p)
        ce.validate(valid,p)
        valid['selection_basis']['observed_ids'].remove('trial:job:trial')
        with self.assertRaisesRegex(ResponseValidationError,'最近一次'):ce.validate(valid,p)
        shared=payload(single=True,version=ce.V3_VERSION)
        shared['allowed_actions']['candidate_1']=copy.deepcopy(shared['allowed_actions']['candidate_0'])
        shared['allowed_actions']['candidate_1']['changes'][0]['parking']=1
        shared['context']['choice_observations']=ce.observations(shared)
        same=self.proposal(shared)
        self.assertEqual(same['selection_basis']['observed_ids'],[same['selection_basis']['alternative_observed_id']])
        ce.validate(same,shared)

    def test_v4_reuses_top_level_latest_feedback_citation(self):
        p=payload(trial=True,version=ce.V4_VERSION);valid=self.proposal(p)
        self.assertEqual(valid['selection_basis']['observed_ids'],['area:job:baseline:area_0'])
        self.assertEqual(valid['selection_basis']['alternative_observed_id'],'area:job:baseline:area_1')
        ce.validate(valid,p)
        ce.verify(dict(valid,input=p,status='accepted'))
        missing=copy.deepcopy(valid);missing['evidence_refs'].remove('job:trial')
        with self.assertRaisesRegex(ResponseValidationError,'evidence_refs'):ce.validate(missing,p)
        wrong=copy.deepcopy(valid);wrong['selection_basis']['observed_ids']=['trial:job:trial']
        with self.assertRaisesRegex(ResponseValidationError,'所选候选'):ce.validate(wrong,p)

    def test_v5_menu_sets_reference_nullability_before_model_call(self):
        for phase in ('parking','oneway_target'):
            for single in (False,True):
                with self.subTest(phase=phase,single=single):
                    p=payload(phase,single=single,version=ce.VERSION)
                    frozen=copy.deepcopy(p);schema=ce.schema(p)
                    self.assertEqual(p,frozen)
                    self.assertEqual(schema['type'],'object')
                    valid=self.proposal(p);ce.validate(valid,p)
                    for field in ('alternative_action','alternative_observed_id'):
                        offered=schema['properties'][field]
                        self.assertEqual(offered['type'],'null' if single else 'string')
                        self.assertEqual(None in offered['enum'],single)
                        self.assertIn(valid['selection_basis'][field],offered['enum'])
                    if not single:
                        valid['selection_basis']['alternative_observed_id']=None
                        with self.assertRaisesRegex(ResponseValidationError,'备选取舍'):
                            ce.validate(valid,p)

    def test_v5_keeps_stop_and_latest_feedback_semantics(self):
        p=payload(trial=True,version=ce.VERSION)
        p['allowed_actions']['stop']=dict(title='结束试验')
        schema=ce.schema(p)
        self.assertEqual(schema['anyOf'][0],dict(type='null'))
        ce.validate(dict(action='stop',selection_basis=None),p)
        valid=self.proposal(p);ce.validate(valid,p)
        self.assertNotIn('trial:job:trial',valid['selection_basis']['observed_ids'])
        missing=copy.deepcopy(valid);missing['evidence_refs'].remove('job:trial')
        with self.assertRaisesRegex(ResponseValidationError,'evidence_refs'):ce.validate(missing,p)
        wrong=copy.deepcopy(valid);wrong['selection_basis']=None
        with self.assertRaisesRegex(ResponseValidationError,'选点必须'):ce.validate(wrong,p)

    def test_v4_wire_schemas_remain_frozen(self):
        # Captured from 53b467b1 before the real v4 run ended. Legacy schemas
        # keep their original nullability, including the historical mismatch.
        expected={
            (False,False):'9519dad5bf5259cedd16a82941173f648e0f33bafdd8a626bb92bbb102bf1022',
            (False,True):'7d3cf291507a94647ad5f98fa5490d466e289cd00248ae200e87bd5b0411525a',
            (True,False):'babc0a77d6ee99a646273d1b2c09b948dc1deefce27ecc8f4225b36a710f6f73',
            (True,True):'59ce24c95828bede0209d6a8d9b5f1471a1d9dc9ce8dce389747c12050282ac8',
        }
        for (single,trial),digest in expected.items():
            schema=ce.schema(payload(single=single,trial=trial,version=ce.V4_VERSION))
            actual=hashlib.sha256(json.dumps(schema,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            self.assertEqual(actual,digest)

    def test_invalid_refs_and_alternatives_fail_before_execution(self):
        p=payload();valid=self.proposal(p)
        for field,value in [('observed_ids',[]),('observed_ids',['future:result']),
                            ('observed_ids',valid['selection_basis']['observed_ids']*2),
                            ('observed_ids',[{}]),('alternative_action','candidate_0'),
                            ('alternative_action','not_in_menu'),('alternative_action',None),
                            ('working_hypothesis','test'),('disconfirming_result',123),
                            ('alternative_tradeoff','说明'*200)]:
            v=copy.deepcopy(valid);v['selection_basis'][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ResponseValidationError):ce.validate(v,p)

    def test_later_trial_must_include_most_recent_pair_even_if_negative(self):
        p=payload(trial=True)
        latest=copy.deepcopy(p['observations'][-1]);latest.update(ref='job:latest',run='latest')
        p['observations'].append(latest);p['context']['choice_observations']=ce.observations(p)
        v=self.proposal(p);ce.validate(v,p)
        self.assertIn('trial:job:latest',v['selection_basis']['observed_ids'])
        v['selection_basis']['observed_ids'][-1]='trial:job:trial'
        with self.assertRaisesRegex(ResponseValidationError,'最近一次'):ce.validate(v,p)

    def test_same_region_measured_trial_can_support_untested_alternative_only_in_v2(self):
        p=payload(trial=True)
        p['observations'][-1]['changes']=[dict(from_node=1,to_node=2,parking=1)]
        p['observations'][-1]['regional_checks']=[dict(region='area_0',time_change_pct=-20,passed=True)]
        p['context']['choice_observations']=ce.observations(p)
        trial=next(s for s in p['context']['choice_observations']['sources'] if s['kind']=='trial')
        self.assertIn('candidate_0',trial['related_actions'])
        self.assertNotIn('candidate_1',trial['related_actions'])
        chosen=self.proposal(p,'candidate_1')
        chosen['selection_basis']['observed_ids'].remove('area:job:baseline:area_0')
        ce.validate(chosen,p)
        ce.verify(dict(chosen,input=p,status='accepted'))
        changed=copy.deepcopy(p)
        next(s for s in changed['context']['choice_observations']['sources'] if s['kind']=='trial')['related_actions'].append('candidate_1')
        with self.assertRaisesRegex(ValueError,'改写'):ce.verify(dict(chosen,input=changed,status='accepted'))
        # The chosen action still needs its own new-baseline area observation.
        missing=copy.deepcopy(chosen)
        missing['selection_basis']['observed_ids'].remove('area:job:baseline:area_1')
        with self.assertRaisesRegex(ResponseValidationError,'所选候选'):ce.validate(missing,p)
        legacy=copy.deepcopy(p)
        legacy['context']['choice_basis_protocol']=ce.LEGACY_VERSION
        legacy['context']['choice_observations']=ce.observations(legacy)
        self.assertNotIn('related_actions',next(s for s in legacy['context']['choice_observations']['sources'] if s['kind']=='trial'))
        with self.assertRaisesRegex(ResponseValidationError,'备选取舍'):ce.validate(chosen,legacy)

    def test_other_region_trial_cannot_substitute_for_alternative_evidence(self):
        p=payload(trial=True)
        p['observations'][-1]['changes']=[dict(from_node=2,to_node=3,parking=1)]
        p['observations'][-1]['regional_checks']=[dict(region='area_1',time_change_pct=-20,passed=True)]
        p['context']['choice_observations']=ce.observations(p)
        chosen=self.proposal(p,'candidate_1')
        chosen['selection_basis']['observed_ids'].remove('area:job:baseline:area_0')
        with self.assertRaisesRegex(ResponseValidationError,'备选取舍'):ce.validate(chosen,p)

    def test_strength_alternatives_can_share_same_observed_area(self):
        p=payload(single=True)
        p['allowed_actions']['candidate_1']=copy.deepcopy(p['allowed_actions']['candidate_0'])
        p['allowed_actions']['candidate_1']['changes'][0]['parking']=1
        p['context']['choice_observations']=ce.observations(p)
        v=self.proposal(p)
        self.assertEqual(len(v['selection_basis']['observed_ids']),1)
        ce.validate(v,p)

    def test_single_candidate_and_handoff_are_explicit(self):
        p=payload(single=True);v=self.proposal(p)
        self.assertIsNone(v['selection_basis']['alternative_action']);ce.validate(v,p)
        p['allowed_actions']['study_2']=dict(title='下一研究组')
        ce.validate(self.proposal(p,'study_2'),p)
        with self.assertRaises(ResponseValidationError):ce.validate(dict(v,action='study_2'),p)
        v['selection_basis']['alternative_action']='invented'
        with self.assertRaises(ResponseValidationError):ce.validate(v,p)

    def test_non_spatial_fallback_uses_own_directions_only(self):
        p=payload();p['observations'][0].pop('spatial')
        for opt in p['allowed_actions'].values():opt.pop('region')
        p['context']['choice_observations']=ce.observations(p)
        self.assertTrue(ce.covered(p))
        self.assertTrue(all(s['kind']=='road' for s in p['context']['choice_observations']['sources']))
        ce.validate(self.proposal(p),p)

    def test_capture_preserves_public_basis_and_removes_unknown_fields(self):
        p=payload();v=self.proposal(p);v['selection_basis']['secret_reasoning']='never retain'
        captured,report=capture_proposal(v,decision_tool(p)['parameters'])
        self.assertNotIn('secret_reasoning',captured['selection_basis'])
        self.assertTrue(report['excerpted'])
        self.assertEqual(captured['selection_basis']['observed_ids'],v['selection_basis']['observed_ids'])

    def test_audit_rebuilds_index_and_rejects_protocol_or_fact_tampering(self):
        p=payload();entry=dict(self.proposal(p),input=p,status='accepted');ce.verify(entry)
        for part in ('version','unit','source','basis','context_version'):
            bad=copy.deepcopy(entry);context=bad['input']['context'];facts=context['choice_observations']
            if part=='version':facts['version']='invented'
            if part=='unit':facts['units']['baseline_car_flow']='pcu/h'
            if part=='source':facts['sources'][0]['ref']='future:trial'
            if part=='basis':bad.pop('selection_basis')
            if part=='context_version':context['choice_basis_protocol']='invented'
            with self.subTest(part=part),self.assertRaises(ValueError):ce.verify(bad)

    def test_legacy_never_gains_new_evidence(self):
        entry=dict(input=dict(context={}),status='accepted')
        ce.verify(entry)
        for bad in (dict(entry,selection_basis=None),dict(entry,input=dict(context={'choice_observations':{}}))):
            with self.assertRaises(ValueError):ce.verify(bad)

    def test_closing_round_has_no_unused_selection_fields(self):
        p=payload();p['phase']='route';p['allowed_actions']={'stop':dict(title='结束')}
        p['context']['choice_observations']=ce.observations(p)
        self.assertIsNone(ce.schema(p))
        self.assertNotIn('selection_basis',decision_tool(p)['parameters']['properties'])
        with self.assertRaises(ValueError):ce.verify(dict(input=p,status='accepted',action='stop',selection_basis=None))

    def test_missing_candidate_observations_do_not_consume_model_slot(self):
        p=payload();p['observations'][0].pop('spatial');p['observations'][0]['core_roads']=[]
        client=Mock();session=PlanningSession(client,'合成缺测',choice_basis_protocol=ce.V2_VERSION)
        result=session.decide('parking','job',p['observations'],p['allowed_actions'])
        self.assertEqual(result['status'],'blocked');self.assertFalse(result['requested'])
        self.assertEqual(session.used,1);client.request.assert_not_called()

    def test_real_session_keeps_negative_feedback_and_rejects_missing_own_ref(self):
        p=payload(trial=True)
        def choose(instructions,inputs,tools,choice):
            import json
            current=json.loads(inputs[0]['content'])
            result=reply('candidate_0',current['evidence_refs'],current)
            value=json.loads(result['calls'][0]['arguments'])
            value['selection_basis']['observed_ids'].pop(0)
            result['calls'][0]['arguments']=json.dumps(value)
            return result
        client=Mock(request=Mock(side_effect=choose))
        session=PlanningSession(client,'合成无效引用',choice_basis_protocol=ce.V2_VERSION)
        entry=session.decide('parking','job',p['observations'],p['allowed_actions'],allow_stop=False)
        self.assertEqual(entry['validation']['code'],'choice_basis');self.assertEqual(entry['action'],'stop')
        self.assertFalse(entry['proposal_capture']['excerpted'])
        self.assertTrue(session.halted);self.assertEqual(session.used,3)
        self.assertEqual(entry['review_correction']['version'],CHOICE_CORRECTION_VERSION)
        self.assertEqual(client.request.call_count,2)
        verify_review_permissions(entry)

    def test_missing_alternative_citation_gets_one_frozen_repair(self):
        import json
        p=payload(trial=True)
        replies=[]
        def choose(instructions,inputs,tools,choice):
            current=json.loads(inputs[0]['content'])
            result=reply('candidate_0',current['evidence_refs'],current)
            value=json.loads(result['calls'][0]['arguments'])
            if not replies:
                value['selection_basis']['observed_ids'].remove('area:job:baseline:area_1')
            result['calls'][0]['arguments']=json.dumps(value)
            replies.append(value)
            return result
        client=Mock(request=Mock(side_effect=choose))
        session=PlanningSession(client,'合成证据修正',choice_basis_protocol=ce.V2_VERSION)
        entry=session.decide('parking','job',p['observations'],p['allowed_actions'],allow_stop=False)
        self.assertEqual(entry['status'],'accepted')
        self.assertEqual(entry['action'],'candidate_0')
        self.assertEqual(entry['review_correction']['version'],CHOICE_CORRECTION_VERSION)
        self.assertEqual(entry['review_correction']['first_attempt']['validation']['code'],'choice_basis')
        self.assertEqual(client.request.call_count,2)
        feedback=entry['review_correction']['input']['review_feedback']
        self.assertEqual(feedback['retained_action'],'candidate_0')
        self.assertEqual(feedback['retained_alternative_action'],'candidate_1')
        self.assertEqual(feedback['recent_trial'],'trial:job:trial')
        self.assertIn('area:job:baseline:area_1',feedback['relevant_observation_ids'])
        self.assertEqual(session.budget()['used'],3)
        verify_review_permissions(entry)
        changed=copy.deepcopy(entry)
        changed['review_correction']['first_attempt']['proposal_excerpt']['action']='candidate_1'
        with self.assertRaises(ValueError):verify_review_permissions(changed)

    def test_v4_and_v5_repairs_keep_top_level_feedback_citation(self):
        for version in (ce.V4_VERSION,ce.VERSION):
            with self.subTest(version=version):
                p=payload(trial=True,version=version)
                def choose(instructions,inputs,tools,choice):
                    current=json.loads(inputs[0]['content'])
                    result=reply('candidate_0',current['evidence_refs'],current)
                    if 'review_feedback' not in current:
                        value=json.loads(result['calls'][0]['arguments'])
                        # A provider can still return invalid tool arguments;
                        # the local gate must retain its bounded repair.
                        value['selection_basis']['alternative_observed_id']=None
                        result['calls'][0]['arguments']=json.dumps(value)
                    return result
                client=Mock(request=Mock(side_effect=choose))
                session=PlanningSession(client,'合成独立引用修正',choice_basis_protocol=version)
                entry=session.decide('parking','job',p['observations'],p['allowed_actions'],allow_stop=False)
                self.assertEqual(entry['status'],'accepted')
                self.assertEqual(client.request.call_count,2)
                feedback=entry['review_correction']['input']['review_feedback']
                self.assertIn('顶层evidence_refs须引用其对应的实际场景ref，无须在observed_ids重复',feedback['instruction'])
                self.assertNotIn('trial:job:trial',entry['selection_basis']['observed_ids'])
                self.assertIn('job:trial',entry['evidence_refs'])
                verify_review_permissions(entry)

    def test_citation_repair_cannot_change_experiment_or_spend_final_reserved_slot(self):
        import json
        p=payload(trial=True)
        def choose(instructions,inputs,tools,choice):
            current=json.loads(inputs[0]['content'])
            result=reply('candidate_0',current['evidence_refs'],current)
            value=json.loads(result['calls'][0]['arguments'])
            if 'review_feedback' in current:
                value['action']='candidate_1'
            else:
                value['selection_basis']['observed_ids'].remove('area:job:baseline:area_1')
            result['calls'][0]['arguments']=json.dumps(value)
            return result
        client=Mock(request=Mock(side_effect=choose))
        session=PlanningSession(client,'合成修正越界',choice_basis_protocol=ce.V2_VERSION)
        entry=session.decide('parking','job',p['observations'],p['allowed_actions'],allow_stop=False)
        self.assertEqual(entry['validation']['code'],'correction_action')
        self.assertTrue(session.halted)
        verify_review_permissions(entry)
        shallow=PlanningSession(client,'合成预算不足',limit=3,choice_basis_protocol=ce.V2_VERSION)
        rejected=shallow.decide('parking','job',p['observations'],p['allowed_actions'],allow_stop=False)
        self.assertEqual(rejected['validation']['code'],'choice_basis')
        self.assertNotIn('review_correction',rejected)

        # A multi-study plan with no spare request must preserve every later
        # candidate and study review slot, even when a citation is repairable.
        exact=PlanningSession(client,'合成恰好六次预算',limit=6,choice_basis_protocol=ce.V2_VERSION)
        context=dict(completion_guard=dict(required_model_requests=3),reserved_model_requests=2)
        rejected=exact.decide('parking','job',p['observations'],p['allowed_actions'],context,allow_stop=False)
        self.assertEqual(rejected['validation']['code'],'choice_basis')
        self.assertNotIn('review_correction',rejected)
        self.assertEqual(exact.used,2)

    def test_unknown_protocol_is_rejected_without_a_model_call(self):
        with self.assertRaises(ValueError):PlanningSession(Mock(),'合成协议',choice_basis_protocol='invented')


if __name__=='__main__':unittest.main()
