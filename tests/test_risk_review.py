"""Observed costs and prospective hypotheses have different evidence status."""
import copy,json,unittest
from unittest.mock import Mock
from types import SimpleNamespace
from traffic_agent import risk_review as rr
from traffic_agent.feedback_planner import PlanningSession,decision_tool,verify_review_permissions,run_observations
from traffic_agent.planning_payload import expand
from traffic_agent.planning_response import ResponseValidationError
from traffic_agent.planning_review import display_trace
from traffic_agent.report import risk_lines
from test_choice_evidence import payload
from test_feedback_planner import reply


class RiskReviewTests(unittest.TestCase):
    def evidence(self):
        return dict(observations=[dict(ref='j:baseline')],context=dict(risk_review_protocol=rr.VERSION,
            measured_conclusion=dict(costs=[dict(ref='j:bad',has_measured_cost=True,constraint_passed=False,
                neighbor_new_oversaturated_km=1.03054),dict(ref='j:tiny',has_measured_cost=True,constraint_passed=True,
                neighbor_time_change_pct=.0000149734),dict(ref='j:unknown',has_measured_cost=None,constraint_passed=None)])))

    def concern(self):
        return dict(hypothesis='邻区新增过饱和可能伴随动态排队，当前尚未测量。',basis_refs=['j:bad'],
                    verification_needs=['dynamic_queue_evidence'])

    def test_schema_and_runtime_keep_unknown_dynamic_claims_in_a_separate_unverified_field(self):
        p=payload();p['context']['risk_review_protocol']=rr.VERSION
        fields=decision_tool(p)['parameters']['properties']
        self.assertNotIn('risk',fields);self.assertIn('unverified_risks',fields)
        risk_fields=fields['unverified_risks']['items']['properties']
        self.assertNotIn('verification_needed',risk_fields);self.assertIn('verification_needs',risk_fields)
        p=self.evidence();value=dict(unverified_risks=[self.concern()]);rr.validate(value,p)
        for field,bad in [('basis_refs',['future:run']),('basis_refs',['j:bad','j:bad']),
                          ('verification_needs',[]),('verification_needs',['unknown']),
                          ('verification_needs',['dynamic_queue_evidence']*2),('verification_needs',[{}]),
                          ('verification_needed','接口只有汇总里程')]:
            broken=copy.deepcopy(value);broken['unverified_risks'][0][field]=bad
            with self.subTest(field=field,bad=bad),self.assertRaises(ResponseValidationError):rr.validate(broken,p)
        rr.validate(dict(unverified_risks=[]),p)

    def test_measured_costs_tiny_positive_unknown_and_model_hypothesis_survive_display_and_report(self):
        p=self.evidence();before=copy.deepcopy(p)
        e=dict(input=p,status='accepted',unverified_risks=[self.concern()],**rr.generated_values(p))
        rr.verify(e);shown=rr.display(e)
        self.assertIn('3项治理配对，其中2项记录到代价增加、1项约束未通过',shown['measured_summary'])
        self.assertNotIn('动态排队',shown['measured_summary'])
        self.assertEqual({k:shown['unverified'][0][k] for k in self.concern()},self.concern())
        self.assertEqual(e['unverified_risks'],[self.concern()])
        self.assertEqual(shown['unverified'][0]['verification_evidence'][0]['status'],'separate_evidence_required')
        self.assertEqual(p,before)
        report='\n'.join(risk_lines(e))
        self.assertIn('实测代价（来自观测）',report)
        self.assertIn('模型待验证风险',report);self.assertIn('需要另行采集',report)
        self.assertIn('模型选择类型，提供情况来自本轮输入',report)
        self.assertIn('不证明风险已经发生',report)
        e['unverified_risks']=[]
        self.assertIn('不表示没有风险','\n'.join(risk_lines(e)))

    def test_generated_cost_summary_cannot_be_rewritten_and_legacy_remains_original(self):
        p=self.evidence();e=dict(input=p,status='accepted',unverified_risks=[],**rr.generated_values(p))
        for field,value in [('risk','全部无代价'),('risk_origin',None)]:
            with self.subTest(field=field),self.assertRaises(ValueError):rr.verify(dict(e,**{field:value}))
        e['input']['context'].pop('risk_review_protocol')
        with self.assertRaisesRegex(ValueError,'旧记录'):rr.verify(e)
        old=dict(input=dict(context={}),status='accepted',risk='保留原始风险说明')
        rr.verify(old);self.assertIsNone(rr.display(old))
        self.assertIn('保留原始风险说明','\n'.join(risk_lines(old)))

    def test_session_accepts_model_concern_and_derived_public_status_without_rewriting_it(self):
        p=payload()
        def model(instructions,inputs,tools,choice):
            actual=expand(json.loads(inputs[0]['content']));result=reply('candidate_0',actual['evidence_refs'],actual)
            value=json.loads(result['calls'][0]['arguments'])
            value['unverified_risks']=[dict(self.concern(),basis_refs=actual['evidence_refs'][:1])]
            result['calls'][0]['arguments']=json.dumps(value);return result
        session=PlanningSession(Mock(request=Mock(side_effect=model)),'原始问题',choice_basis_protocol=p['context']['choice_basis_protocol'])
        entry=session.decide(p['phase'],'j',p['observations'],p['allowed_actions'],p['context'],allow_stop=False)
        self.assertEqual(entry['status'],'accepted',entry.get('validation'))
        verify_review_permissions(entry)
        self.assertEqual(entry['risk_origin'],rr.VERSION)
        public=display_trace([entry])[0]
        self.assertEqual(public['risk_review']['unverified'][0]['hypothesis'],entry['unverified_risks'][0]['hypothesis'])
        self.assertNotIn('verification_needed',entry['unverified_risks'][0])
        self.assertNotIn('risk_review',entry)

    def test_freezing_legacy_binding_and_unknown_version(self):
        s=PlanningSession(Mock(),'原始问题');a=SimpleNamespace(job_id='j',state=dict(runs=[]))
        s.bind_programme(dict(risk_review_protocol=rr.VERSION),[a])
        self.assertEqual(a.state['risk_review_protocol'],rr.VERSION)
        a.state['runs']=[dict(name='baseline')]
        with self.assertRaisesRegex(ValueError,'执行开始后'):s.bind_programme({},[a])
        with self.assertRaisesRegex(ValueError,'执行开始后'):s.bind_programme(dict(risk_review_protocol=rr.LEGACY_VERSION),[a])
        a.state['runs']=[];s.bind_programme(dict(risk_review_protocol=rr.LEGACY_VERSION),[a])
        self.assertEqual(s.risk_review_protocol,rr.LEGACY_VERSION)
        a.state['runs']=[];s.bind_programme({},[a]);self.assertIsNone(s.risk_review_protocol)
        self.assertNotIn('risk_review_protocol',a.state)
        with self.assertRaisesRegex(ValueError,'未知'):rr.active(dict(context=dict(risk_review_protocol='unknown')))

    def test_unexpected_old_public_risk_is_rejected_but_original_words_are_kept(self):
        def model(_i,inputs,_t,_c):
            p=expand(json.loads(inputs[0]['content']));r=reply('stop',p['evidence_refs'],p)
            value=json.loads(r['calls'][0]['arguments']);value['risk']='这是原模型未获执行的风险说明。'
            r['calls'][0]['arguments']=json.dumps(value);return r
        client=Mock(request=Mock(side_effect=model))
        e=PlanningSession(client,'原始请求').decide('route','j',[dict(ref='j:baseline')],{})
        self.assertEqual(e['status'],'rejected');self.assertEqual(e['validation']['code'],'argument_fields')
        self.assertEqual(e['proposal_excerpt']['risk'],'这是原模型未获执行的风险说明。')
        client.request.assert_called_once()

    def test_v1_keeps_original_free_verification_words_and_never_gains_new_availability(self):
        p=payload();p['context']['risk_review_protocol']=rr.LEGACY_VERSION
        fields=decision_tool(p)['parameters']['properties']['unverified_risks']['items']['properties']
        self.assertIn('verification_needed',fields);self.assertNotIn('verification_needs',fields)
        p=self.evidence();p['context']['risk_review_protocol']=rr.LEGACY_VERSION
        concern=dict(self.concern());concern.pop('verification_needs')
        concern['verification_needed']='保留旧模型对接口的原始说明，不作事后修正。'
        e=dict(input=p,status='accepted',unverified_risks=[concern],**rr.generated_values(p))
        rr.verify(e);shown=rr.display(e)
        self.assertEqual(shown['version'],rr.LEGACY_VERSION)
        self.assertEqual(shown['unverified'],[concern])
        self.assertIn('还需验证（模型原文）','\n'.join(risk_lines(e)))

    def test_old_free_verification_in_a_new_reply_is_rejected_but_retained(self):
        def model(_i,inputs,_t,_c):
            p=expand(json.loads(inputs[0]['content']));r=reply('stop',p['evidence_refs'],p)
            value=json.loads(r['calls'][0]['arguments'])
            value['unverified_risks']=[dict(hypothesis='保留尚未证明的模型假设',basis_refs=p['evidence_refs'],
                verification_needed='这是模型原始方法说明，不能丢失也不能改成已经通过。')]
            r['calls'][0]['arguments']=json.dumps(value);return r
        client=Mock(request=Mock(side_effect=model))
        e=PlanningSession(client,'原始请求').decide('route','j',[dict(ref='j:baseline')],{})
        self.assertEqual(e['status'],'rejected');self.assertEqual(e['validation']['code'],'risk_review')
        self.assertIn('模型原始方法说明',e['proposal_excerpt']['unverified_risks'][0]['verification_needed'])
        client.request.assert_called_once()

    def test_catalog_distinguishes_missing_empty_partial_and_dynamic_evidence(self):
        p=self.evidence();before=copy.deepcopy(p)
        catalog=lambda:{r['id']:r for r in rr.evidence_catalog(p)}
        for key in ('new_neighbor_link_details','paired_core_time','paired_neighbor_limits','directed_link_state'):
            self.assertEqual(catalog()[key]['status'],'not_in_current_input')
        p['context']['previous_decisions']=[dict(neighbor_new_links=[],core_time_change_pct=-5)]
        self.assertEqual(catalog()['new_neighbor_link_details']['status'],'not_in_current_input')
        self.assertEqual(catalog()['paired_core_time']['status'],'not_in_current_input')
        p['observations'].append(dict(ref='j:zero',neighbor_new_links=[],neighbor_new_link_count=0,
            delta=dict(core=dict(running_time_change_pct=0),neighbor=dict(running_time_change_pct=0)),
            new_neighbor_oversaturated_km=0))
        for key in ('new_neighbor_link_details','paired_core_time','paired_neighbor_limits'):
            self.assertEqual(catalog()[key]['status'],'provided')
        self.assertEqual(catalog()['new_neighbor_link_details']['source_paths'],['#/observations/1/neighbor_new_links'])
        p['observations'][1]['neighbor_new_links']=[dict(from_node=1,to_node=2,vc=1.2)]
        p['observations'][1].update(neighbor_new_link_count=18,neighbor_new_links_complete=False)
        self.assertEqual(catalog()['directed_link_state']['status'],'provided')
        self.assertIn('节选',catalog()['new_neighbor_link_details']['meaning'])
        self.assertEqual(catalog()['dynamic_queue_evidence']['status'],'separate_evidence_required')
        self.assertEqual(catalog()['causal_identification']['status'],'separate_evidence_required')
        # A combined summary with only one numeric part is not a measured pair.
        p['observations'][1]['new_neighbor_oversaturated_km']=None
        self.assertEqual(catalog()['paired_neighbor_limits']['status'],'not_in_current_input')
        p['observations'][1]['delta']=dict(core=None,neighbor=None)
        self.assertEqual(catalog()['paired_core_time']['status'],'not_in_current_input')
        self.assertEqual(before['observations'],[dict(ref='j:baseline')])

    def test_new_observation_and_report_share_native_neighbor_details_without_upgrading_legacy(self):
        from traffic_agent.conclusion_review import assess_state
        links=[dict(index=i,name='实际路段',from_node=i,to_node=i+1,length_km=.2,vc=1.2,
                    speed_kmh=12,flow_pcu_h=400) for i in range(12)]
        comparison=dict(new_neighbor_oversaturated_links=links,new_neighbor_oversaturated_km=2.4,
                        delta=dict(core=dict(running_time_change_pct=-5)),eligible=False)
        run=dict(name='parking_1',changes=[dict(from_node=0,to_node=1,parking=2)],demand_factor=1,
                 metrics=dict(core=dict(running_time_pcu_h=95)),execution=dict(status='completed'),comparison=comparison)
        state=dict(id='j',runs=[run],risk_review_protocol=rr.VERSION)
        original=copy.deepcopy(state);observed=run_observations(state)[0]
        view=assess_state(state)['costs'][0]
        for k in ('neighbor_new_links','neighbor_new_link_count','neighbor_new_links_complete'):
            self.assertEqual(observed[k],view[k])
        self.assertEqual(len(observed['neighbor_new_links']),10)
        self.assertEqual(observed['neighbor_new_link_count'],12)
        self.assertIs(observed['neighbor_new_links_complete'],False)
        self.assertEqual(state,original)
        for version in (rr.LEGACY_VERSION,None):
            state['risk_review_protocol']=version
            self.assertNotIn('neighbor_new_links',run_observations(state)[0])
        state['risk_review_protocol']=rr.VERSION;run['comparison'].pop('new_neighbor_oversaturated_links')
        self.assertNotIn('neighbor_new_links',run_observations(state)[0])
        run['comparison']['new_neighbor_oversaturated_links']=[]
        zero=run_observations(state)[0]
        self.assertEqual(zero['neighbor_new_links'],[]);self.assertTrue(zero['neighbor_new_links_complete'])
        run['execution']['status']='running'
        self.assertEqual(run_observations(state),[])

    def test_availability_is_derived_even_when_the_model_selects_provided_data(self):
        p=self.evidence();p['observations'][0]['neighbor_new_links']=[]
        concern=dict(self.concern(),verification_needs=['new_neighbor_link_details','dynamic_queue_evidence'])
        e=dict(input=p,status='accepted',unverified_risks=[concern],**rr.generated_values(p));original=copy.deepcopy(e)
        shown=rr.display(e)['unverified'][0]
        self.assertIn('本轮已提供：新增过饱和路段明细',shown['verification_needed'])
        self.assertIn('需要另行取得证据：动态排队或回堵证据',shown['verification_needed'])
        self.assertEqual(e,original)
        e['unverified_risks'][0]['verification_evidence']=[dict(status='provided',id='dynamic_queue_evidence')]
        with self.assertRaises(ResponseValidationError):rr.verify(e)

    def test_public_coverage_preserves_wrong_model_words_alongside_actual_counts(self):
        p=self.evidence();o=dict(ref='j:bad',changes=[dict(parking=1)],delta=dict(core={}),
            neighbor_new_links=[dict(index=i) for i in range(4)],neighbor_new_link_count=4,neighbor_new_links_complete=True)
        p['observations'].append(o)
        concern=dict(self.concern(),hypothesis='原始错误说法：新增明细只是部分节选。')
        e=dict(input=p,status='accepted',unverified_risks=[concern],**rr.generated_values(p));original=copy.deepcopy(e)
        shown=rr.display(e);row=shown['observed_detail_coverage'][0]
        self.assertEqual((row['provided'],row['total'],row['complete']),(4,4,True))
        self.assertIn('已提供4条／总计4条（完整）',row['text'])
        report='\n'.join(risk_lines(e))
        self.assertIn(row['text'],report);self.assertIn('原始错误说法',report);self.assertEqual(e,original)
        o['neighbor_new_link_count']=12;o['neighbor_new_links_complete']=False
        self.assertIs(rr.detail_coverage(p)[0]['complete'],False)
        o.pop('neighbor_new_link_count');self.assertIsNone(rr.detail_coverage(p)[0]['complete'])
        o['neighbor_new_links']=[];o['neighbor_new_link_count']=0
        self.assertEqual(rr.detail_coverage(p)[0]['text'],'已提供0条／总计0条（完整）')
        o.pop('neighbor_new_links');self.assertIn('未提供',rr.detail_coverage(p)[0]['text'])


if __name__=='__main__':unittest.main()
