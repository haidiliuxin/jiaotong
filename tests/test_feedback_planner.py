import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from traffic_agent.agent import Agent
from traffic_agent.data import scoped_metrics
from traffic_agent.feedback_planner import PlanningSession, parking_options, run_observations, digest
from traffic_agent.planning_audit import verify_planning
from traffic_agent.llm_client import ModelError
from traffic_agent.runner import now
from traffic_agent.task import Task
from test_agent import link


def choice_basis(action,payload):
    """Synthetic public rationale over actual controller inputs, not a model."""
    from traffic_agent.choice_evidence import actions
    offered=actions(payload)
    if action not in offered:return None
    facts=payload['context']['choice_observations']
    alternative=next((a for a in offered if a!=action),None)
    refs=[next(s['id'] for s in facts['sources'] if a in s['actions']) for a in (action,alternative) if a]
    if facts['recent_trial']:refs.append(facts['recent_trial'])
    from traffic_agent.choice_evidence import VERSION as CHOICE_VERSION,V3_VERSION,V4_VERSION,V5_VERSION,V6_VERSION
    basis=dict(observed_ids=list(dict.fromkeys(refs[:1]+([facts['recent_trial']] if facts['recent_trial'] else [])))
               if facts['version']==V3_VERSION else list(dict.fromkeys(refs[:1]))
               if facts['version'] in (V4_VERSION,V5_VERSION,V6_VERSION,CHOICE_VERSION) else list(dict.fromkeys(refs)),alternative_action=alternative,
        working_hypothesis='合成流程假设：所选措施的核心收益与周边代价仍需配对试验。',
        disconfirming_result='若同需求核心耗时不降低或周边代价超过原阈值，则不推荐该措施。',
        alternative_tradeoff='仅比较本轮可见条件，备选未测效果仍未知。' if alternative else '当前菜单只有一个合法候选，仍需后续验证。')
    if facts['version'] in (V3_VERSION,V4_VERSION,V5_VERSION,V6_VERSION,CHOICE_VERSION):basis['alternative_observed_id']=refs[1] if alternative else None
    return basis


def reply(action, refs, payload=None):
    args = dict(action=action, summary='实测联合治理增加邻区耗时', reason='比较更小范围，避免扩散',
                risk='单方向收益尚待验证', next_check='核对核心和邻区的实际变化', evidence_refs=refs)
    if payload and payload.get('context',{}).get('action_execution'):args.pop('next_check')
    if payload and payload.get('context',{}).get('action_execution',{}).get('version')=='action-execution-v2':args.pop('summary')
    if payload and payload.get('context',{}).get('risk_review_protocol'):
        args.pop('risk');args['unverified_risks']=[]
    if payload and payload.get('context',{}).get('choice_basis_protocol'):
        from traffic_agent.choice_evidence import actions
        if actions(payload):args['selection_basis']=choice_basis(action,payload)
        if payload['context'].get('choice_rationale_protocol') and action in actions(payload):args['reason']=''
    if payload and payload.get('context',{}).get('measured_conclusion',{}).get('costs'):
        facts=payload['context']['measured_conclusion']['costs']
        final=action=='stop' or payload['context'].get('require_hypothesis_review')
        from traffic_agent.conclusion_review import VERSION as CONCLUSION_VERSION
        if payload['context']['measured_conclusion']['version']==CONCLUSION_VERSION:
            args['reviewed_cost_refs']=[r['ref'] for r in facts] if final else []
        else:
            args['cost_acknowledgments']=[{k:r[k] for k in ('ref','constraint_passed','has_measured_cost','neighbor_new_oversaturated_km')} for r in facts] if final else []
    return dict(calls=[dict(name='choose_next_experiment', arguments=json.dumps(args))], metadata=dict(tokens=80))


class FeedbackPlannerTests(unittest.TestCase):
    def test_new_programme_feedback_retrieves_sources_and_rejects_fake_citation(self):
        from traffic_agent.knowledge_retrieval import VERSION,verify
        def choose(_instructions, inputs, _tools, _choice):
            payload=json.loads(inputs[0]['content'])
            bundle=payload['context']['knowledge_retrieval']
            self.assertEqual(verify(bundle,payload['request'],payload['phase']),'verified')
            return reply('stop',['j:baseline'],payload)
        client=Mock(request=Mock(side_effect=choose))
        session=PlanningSession(client,'长江村路停车影响类别',initial_usage={})
        session.programme_plan={'knowledge_retrieval_protocol':VERSION}
        entry=session.decide('route','j',[dict(ref='j:baseline')],{})
        self.assertEqual(entry['status'],'accepted')
        self.assertEqual(entry['input']['context']['knowledge_retrieval']['version'],VERSION)
        from traffic_agent.feedback_planner import verify_input_wire
        verify_input_wire(entry)

        bad=reply('stop',['j:baseline'])
        value=json.loads(bad['calls'][0]['arguments'])
        value['reason']='根据【K:invented.source】决定停止'
        bad['calls'][0]['arguments']=json.dumps(value)
        rejected=PlanningSession(Mock(request=Mock(return_value=bad)),'长江村路停车治理',initial_usage={})
        rejected.programme_plan={'knowledge_retrieval_protocol':VERSION}
        outcome=rejected.decide('route','j',[dict(ref='j:baseline')],{})
        self.assertEqual(outcome['status'],'rejected')
        self.assertEqual(outcome['validation']['code'],'knowledge_citation')

    def test_parking_selection_prompt_omits_unavailable_review_phases(self):
        from traffic_agent.feedback_planner import INSTRUCTIONS,instructions_for
        from traffic_agent.conclusion_review import VERSION as CONCLUSION_VERSION
        from traffic_agent.choice_evidence import VERSION as CHOICE_VERSION
        payload=dict(phase='parking',allowed_actions={'parking_one':{}},context=dict(
            measured_conclusion=dict(version=CONCLUSION_VERSION),choice_basis_protocol=CHOICE_VERSION,
            initial_hypotheses=[],review_permissions=dict(study_review_actions=[],programme_review_actions=[])))
        selection=instructions_for(payload)
        self.assertLess(len(selection),len(INSTRUCTIONS))
        for required in ('selection_basis','measured_conclusion','programme_evidence','review_permissions','未测'):
            self.assertIn(required,selection)
        for absent in ('oneway_target阶段','study_handoff','route_budget','若收到review_feedback'):
            self.assertFalse(any(line.startswith(absent) for line in selection.splitlines()))
        corrected=copy.deepcopy(payload);corrected['context']['review_feedback']={'code':'choice_basis'}
        self.assertIn('若收到review_feedback',instructions_for(corrected))
        route=copy.deepcopy(payload);route.update(phase='route',allowed_actions={'stop':{}})
        self.assertIn('oneway_target阶段',instructions_for(route))

    def test_snapshot_does_not_gain_future_mutations_from_completed_group_objects(self):
        client=Mock(request=Mock(return_value=reply('stop',['j:baseline'])))
        context=dict(completed=[dict(status='running')])
        session=PlanningSession(client,'检查快照')
        entry=session.decide('route','j',[dict(ref='j:baseline')],{},context)
        context['completed'][0]['status']='completed'
        self.assertEqual(entry['input']['context']['completed'][0]['status'],'running')
        self.assertEqual(digest(entry['input']),entry['observation_digest'])

    def test_placeholder_explanation_cannot_finish_a_real_observation_turn(self):
        data=reply('stop',['j:baseline'])
        value=json.loads(data['calls'][0]['arguments']);value['reason']='test'
        data['calls'][0]['arguments']=json.dumps(value)
        client=Mock(request=Mock(return_value=data))
        entry=PlanningSession(client,'不能假装复核').decide('route','j',[dict(ref='j:baseline')],{})
        self.assertEqual(entry['status'],'rejected')
        self.assertEqual(entry['usage']['tokens'],80)
        client.request.assert_called_once()

    def test_native_feedback_changes_the_next_actual_experiment_and_is_auditable(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            def choose(instructions, inputs, tools, choice):
                p = json.loads(inputs[0]['content'])
                self.assertNotIn('api_key', p)
                options = p['allowed_actions']
                if len(p['observations']) == 1:
                    action = next(k for k,v in options.items() if len(v.get('changes',[]))==2 and v['changes'][0]['parking']==2)
                elif len(p['observations']) == 2:
                    self.assertFalse(p['observations'][-1]['eligible'])
                    self.assertTrue(p['observations'][-1]['violations'])
                    action = next(k for k,v in options.items() if len(v.get('changes',[]))==1 and v['changes'][0]['parking']==2)
                else:
                    self.assertEqual(list(options), ['stop'])
                    action = 'stop'
                calls.append(p)
                return reply(action, p['evidence_refs'][-1:], p)
            session = PlanningSession(Mock(request=Mock(side_effect=choose)), '测试路拥堵', {'tokens':40})
            task = Task.from_dict(dict(request='缩小范围缓解拥堵',max_runs=3, max_feedback=2, repeat_baseline=False, demand_factors=[1], recommendation_version='tolerance-v2'))
            a = Agent(task, Path(tmp)/'source', Path(tmp)/'output', planner=session)
            a.job.mkdir(parents=True)
            rows = [link(0,1,2),link(1,2,3),link(2,3,4,speed=20,vc=.9)]
            base = dict(name='baseline',changes=[],demand_factor=1,execution=dict(status='completed',finished=now(),assigned_car_od_sum=100))
            a.state.update(run_count=1,runs=[base])
            executed=[]
            def simulate(name, changes, factor=1):
                new=copy.deepcopy(rows)
                for change in changes:
                    next(r for r in new if r['from_node']==change['from_node'])['speed_kmh']=25
                if len(changes)==2:new[2]['speed_kmh']=5
                r=dict(name=name,changes=changes,demand_factor=factor,metrics=scoped_metrics(new,a.scope),
                       core_rows=[new[i] for i in a.scope['core']],execution=dict(status='completed',started=now(),finished=now(),assigned_car_od_sum=100))
                a.state['runs'].append(r);a.state['run_count']+=1;executed.append(changes)
                return new,r
            diagnosis={'selected':rows[:2]}
            with patch.object(a,'simulation',side_effect=simulate),patch.object(a,'event'):
                a.analyze(rows,base,diagnosis)
            self.assertEqual([len(x) for x in executed],[2,1])
            self.assertEqual(a.state['recommendation'],'parking_2')
            self.assertEqual(session.used,4)
            self.assertEqual(len(calls),3)
            self.assertTrue(verify_planning(a.state,diagnosis)['passed'])
            corrupted=copy.deepcopy(a.state)
            corrupted['planner_trace'][1]['input']['observations'][0]['metrics']['network']['weighted_speed_kmh']=999
            with self.assertRaises(ValueError):verify_planning(corrupted,diagnosis)
            corrupted=copy.deepcopy(a.state)
            corrupted['runs'][1]['changes'][0]['parking']=1
            with self.assertRaises(ValueError):verify_planning(corrupted,diagnosis)
            corrupted=copy.deepcopy(a.state)
            turn=corrupted['planner_trace'][2]
            turn['input']['context']['usable_candidate_found']=False
            turn['observation_digest']=digest(turn['input'])
            with self.assertRaisesRegex(ValueError,'已有可用方案状态'):verify_planning(corrupted,diagnosis)

    def test_only_completed_observations_enter_model_context(self):
        state=dict(id='new',runs=[dict(name='pending',metrics={},execution={'status':'running'}),
                                dict(name='failed',metrics={'network':{}},execution={'status':'failed'})])
        self.assertEqual(run_observations(state),[])

    def test_budget_timeout_and_missing_observations_never_retry(self):
        client=Mock(request=Mock(side_effect=ModelError('模型网络连接失败或超时')))
        s=PlanningSession(client,'用户需求',limit=2)
        observed=[dict(ref='j:baseline')]
        first=s.decide('parking','j',observed,{'x':{'title':'候选'}})
        self.assertEqual(first['status'],'rejected')
        self.assertTrue(s.halted)
        self.assertEqual(s.used,2)
        self.assertEqual(s.decide('parking','j',observed,{})['status'],'stopped')
        client.request.assert_called_once()
        self.assertEqual(s.budget()['unknown_usage'],2)
        s=PlanningSession(client,'缺少观测')
        self.assertFalse(s.decide('route','j',[],{})['requested'])
        self.assertEqual(s.used,1)

    def test_hallucinated_tool_or_evidence_cannot_execute(self):
        for result in (reply('invented',['j:baseline']),reply('allowed',['old:history']),
                       dict(calls=[dict(name='shell',arguments='{}')],metadata={'tokens':2})):
            client=Mock(request=Mock(return_value=result))
            s=PlanningSession(client,'x')
            r=s.decide('parking','j',[dict(ref='j:baseline')],{'allowed':{'title':'合法动作'}})
            self.assertEqual((r['status'],r['action']),('rejected','stop'))
            self.assertNotIn('selected',r)

    def test_menu_cannot_reuse_measured_candidates_or_leave_core(self):
        selected=[link(0,1,2),link(1,2,3)]
        options=parking_options(selected,set())
        chosen=next(iter(options.values()))['changes']
        other=parking_options(selected,{json.dumps(chosen,sort_keys=True)})
        self.assertEqual(len(other),len(options)-1)
        self.assertTrue(all(c['parking'] in (1,2) and c['from_node'] in (1,2) for v in options.values() for c in v['changes']))

    def test_model_can_stop_even_with_native_budget_and_options_left(self):
        client=Mock(request=Mock(side_effect=lambda _i,inputs,_t,_c:reply('stop',['j:baseline'],json.loads(inputs[0]['content']))))
        s=PlanningSession(client,'x')
        r=s.decide('parking','j',[dict(ref='j:baseline')],{'candidate':{'title':'待测'}})
        self.assertEqual(r['action'],'stop')
        self.assertEqual(r['status'],'accepted')
        self.assertEqual(r['observation_digest'],digest(r['input']))

    def test_robust_model_choice_receives_full_grid_and_cannot_skip_failed_demand(self):
        from traffic_agent.model_search import analyze
        with tempfile.TemporaryDirectory() as tmp:
            seen=[]
            def choose(instructions,inputs,tools,choice):
                p=json.loads(inputs[0]['content']);seen.append(p)
                policies=[r for r in p['observations'] if r['changes']]
                if not policies:
                    self.assertEqual({r['demand_factor'] for r in p['observations']},{1,1.1})
                    count=2
                elif len(policies)==2:
                    self.assertEqual({r['demand_factor'] for r in policies},{1,1.1})
                    self.assertTrue(any(not r['eligible'] for r in policies))
                    count=1
                else:
                    return reply('stop',p['evidence_refs'][-1:],p)
                action=next(k for k,v in p['allowed_actions'].items() if len(v.get('changes',[]))==count and v['changes'][0]['parking']==2)
                return reply(action,p['evidence_refs'][-1:],p)
            session=PlanningSession(Mock(request=Mock(side_effect=choose)),'稳健停车研究')
            task=Task.from_dict(dict(request='缩小范围缓解拥堵',search_mode='robust',max_runs=6,max_feedback=2,repeat_baseline=False,demand_factors=[1,1.1],recommendation_version='tolerance-v2'))
            a=Agent(task,Path(tmp)/'source',Path(tmp)/'output',planner=session);a.job.mkdir(parents=True)
            rows=[link(0,1,2),link(1,2,3),link(2,3,4,speed=20,vc=.9)]
            base=dict(name='baseline',changes=[],demand_factor=1,execution=dict(status='completed',finished=now(),assigned_car_od_sum=100))
            a.state.update(run_count=1,runs=[base])
            def simulate(name,changes,factor=1):
                new=copy.deepcopy(rows)
                for row in new:row['flow_pcu_h']*=factor
                for change in changes:next(r for r in new if r['from_node']==change['from_node'])['speed_kmh']=25
                if changes and len(changes)==2 and factor>1:new[2]['speed_kmh']=5
                r=dict(name=name,changes=changes,demand_factor=factor,metrics=scoped_metrics(new,a.scope),core_rows=[new[i] for i in a.scope['core']],
                       execution=dict(status='completed',started=now(),finished=now(),assigned_car_od_sum=100*factor))
                a.state['runs'].append(r);a.state['run_count']+=1
                return new,r
            diagnosis={'selected':rows[:2]}
            with patch.object(a,'simulation',side_effect=simulate),patch.object(a,'event'):analyze(a,rows,base,diagnosis)
            self.assertEqual(a.state['run_count'],6)
            self.assertEqual(len(a.state['model_policies']),2)
            self.assertTrue(a.state['robust_across_tested_demand'])
            self.assertTrue(verify_planning(a.state,diagnosis)['passed'])
            self.assertEqual(list(seen[-1]['allowed_actions']),['stop'])
            bad=copy.deepcopy(a.state);bad['runs']=[r for r in bad['runs'] if r['name']!='model_2_demand_1p100']
            with self.assertRaises(ValueError):verify_planning(bad,diagnosis)


if __name__=='__main__':unittest.main()
