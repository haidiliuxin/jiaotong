"""Real controllers over synthetic external boundaries, never native evidence.

No historical metrics, credentials, provider network or TranStar binary is used.
Both wire protocols pass through the real client/parser and Dispatcher/Agent.
"""
import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock,patch

from traffic_agent.agent import Agent
from traffic_agent.data import scoped_metrics
from traffic_agent.dispatch import Dispatcher
from traffic_agent.llm_client import ChatCompletionsClient,ResponsesClient
from traffic_agent.llm_settings import DEFAULTS
from traffic_agent.planning_audit import verify_planning
from traffic_agent.feedback_planner import digest
from traffic_agent.programme_review import verify as verify_programme
from traffic_agent.runner import now
from scripts.validate_observed_planning import PROGRAMME_REQUEST,verify_case_states
from test_agent import link
from test_natural_plan import experiment,model_reply
from test_oneway_planning import graph_check

REQUEST=PROGRAMME_REQUEST.replace('七东线','离线甲路').replace('长江村路','离线乙路')
CATALOG=dict(matched_roads=['离线甲路','离线乙路'],road_names={'离线甲路','离线乙路'},targets={})


def synthetic_roads():
    pairs=[(1,2),(2,1),(4,5),(5,4),(2,4),(4,2)]
    rows=[link(i,a,b,speed=10+i,flow=1000-i*30) for i,(a,b) in enumerate(pairs)]
    for i,row in enumerate(rows):
        row['name']='离线甲路' if i<2 else '离线乙路' if i<4 else '离线连接路'
        if i>=4:row.update(speed_kmh=35,parking=1,vc=.7)
    return rows


class MixedControllerTests(unittest.TestCase):
    def exercise(self,reverse=False,wire='chat_completions',no_gain=False,no_candidate=False,
                 fail_phase=None,bad_final_cost=False,native_failure=False,initial_correction=False,third_group=False,bad_route=False,
                 lock_accepted_snapshot=False,bad_handoff_review=False,next_study_first=False,budget_correction=False,
                 premature_programme=False,final_correction=None,component_correction=False,diagnostic_pair=None,
                 handoff_correction=None,measurement_contracts=False,wrong_contract=None,neighbor_loss=False,single_study=False,bad_choice=False,
                 hypothesis_patch=False,separation_notes=False,quantifier_patch=False):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name);settings=Mock(revision=Mock(return_value=1),credentials=Mock(return_value=({},'OFFLINE_ONLY')))
        manager=Dispatcher(root/'source',root/'evidence',settings);self.addCleanup(manager.close)
        studies=[experiment(['parking'],road='离线甲路'),experiment(['parking','oneway'],road='离线乙路')]
        if reverse:studies.reverse()
        request_text=REQUEST;catalog=copy.deepcopy(CATALOG)
        if single_study:
            studies=studies[:1]
            request_text='离线甲路研究停车治理，需求系数1，最多治理2条，总预算2次仿真，不复跑。'
            catalog['matched_roads']=['离线甲路']
        if diagnostic_pair:
            road='离线甲路' if diagnostic_pair=='same_road' else '离线乙路'
            studies=[experiment([],road='离线甲路',study='diagnosis'),experiment(['parking'],road=road)]
            request_text=('离线甲路需要独立体检，并另做停车治理，两组单独跑新基准' if diagnostic_pair=='same_road'
                          else '离线甲路只做体检；离线乙路研究停车治理')+'；最多治理1条；需求系数：1；最多3次仿真，不复跑'
            catalog['matched_roads']=sorted({s['road_name'] for s in studies})
        if third_group:
            studies.append(experiment([],road='离线连接路',study='diagnosis'))
            catalog['matched_roads'].append('离线连接路');catalog['road_names'].add('离线连接路')
            request_text=request_text.replace('总预算6次仿真','总预算7次仿真')+' 另对离线连接路进行现状分析。'
        requests=[];executed=[];created=[];held={};storage_conflicts=[]
        original_replace=Path.replace
        def replace(source,target):
            try:return original_replace(source,target)
            except OSError as exc:
                if held.get('path')==target and held.get('reader'):
                    storage_conflicts.append(getattr(exc,'winerror',None))
                    held.pop('reader').close()  # The concurrent reader finishes after the first real sharing conflict.
                raise
        def release_reader():
            if held.get('reader'):held.pop('reader').close()
        self.addCleanup(release_reader)

        def transport(request,**kwargs):
            # Actual request serialization is exercised; no real network call.
            self.assertTrue(request.full_url.startswith('https://offline.invalid/'))
            body=json.loads(request.data)
            tool=body['tools'][0]['function'] if wire=='chat_completions' else body['tools'][0]
            messages=body['messages'][1:] if wire=='chat_completions' else body['input']
            payload=json.loads(messages[0]['content']);requests.append(payload)
            if tool['name']=='compose_traffic_plan':
                self.assertEqual(payload['decision_permissions']['clarification_needs'],[])
                reply=model_reply([],missing_information=[dict(need='unresolved_location',source_quote='离线乙路',
                    question='请提供离线乙路的具体节点。')]) if initial_correction and len(requests)==1 else model_reply(studies)
                if budget_correction and len(requests)==1:
                    reply=model_reply([s for study in studies for s in (experiment([],road=study['road_name'],study='diagnosis'),study)])
                if component_correction and len(requests)==1:
                    reply=model_reply([studies[0],experiment(['parking'],road='离线乙路'),studies[1]])
                value=json.loads(reply['calls'][0]['arguments'])
                if measurement_contracts and value['decision']['intent']=='execute':
                    parking=next(i+1 for i,s in enumerate(studies) if s['strategies']==['parking'])
                    value['decision']['assessment']=[dict(hypothesis=question,evidence_needed='读取本组全部需求的新基准与治理配对，保留约束和负结果。',
                        criterion=dict(test=test,studies=ids,policies=policies,quantifier=quantifier))
                        for question,test,ids,policies,quantifier in [
                            ('停车组至少一个已试措施在全部需求下降低冻结核心耗时','core_time_decrease',[parking],'parking','any'),
                            ('停车组至少一个已试措施通过全部原定推荐约束','admissible',[parking],'parking','any'),
                            ('各组所有已试措施均满足邻区两项代价门槛','neighbor_limits',list(range(1,len(studies)+1)),'all','all')]]
                    if hypothesis_patch:value['decision']['assessment'][0]['criterion']['policies']='combined'
                    if quantifier_patch:value['decision']['assessment'][0]['criterion']['quantifier']='all'
                    if separation_notes:value['decision']['assessment'][0]['hypothesis']+='，且判定与是否通过推荐约束分开'
            elif tool['name']=='correct_hypothesis_policies':
                self.assertTrue(hypothesis_patch and measurement_contracts)
                feedback=json.loads(messages[-1]['content' if wire=='chat_completions' else 'output'])
                self.assertEqual(feedback['code'],'invalid_hypothesis_criterion')
                self.assertEqual(tool['parameters']['required'],['H1'])
                self.assertFalse(tool['parameters']['additionalProperties'])
                self.assertIn('parking',tool['parameters']['properties']['H1']['enum'])
                value=dict(H1='parking')
            elif tool['name']=='correct_hypothesis_fields':
                self.assertTrue(quantifier_patch and measurement_contracts)
                self.assertEqual(tool['parameters']['required'],['H1'])
                fields=tool['parameters']['properties']['H1']
                self.assertEqual(fields['properties']['quantifier']['enum'],['any'])
                self.assertFalse(fields['additionalProperties'])
                value=dict(H1=dict(quantifier='any',**(dict(policies='parking') if hypothesis_patch else {})))
            else:
                from traffic_agent.planning_payload import expand
                payload=expand(payload)
                phase=payload['phase'];options=payload['allowed_actions'];context=payload['context']
                self.assertEqual(tool['parameters']['properties']['action']['enum'],list(options))
                if phase=='oneway_target' or (phase=='parking' and 'stop' not in options):
                    self.assertNotIn('programme_updates',tool['parameters']['properties'])
                    self.assertNotIn('programme_summary',tool['parameters']['required'])
                    self.assertEqual(context['review_permissions']['programme_review_actions'],[])
                if phase==fail_phase:raise TimeoutError('offline transport interruption')
                if phase=='parking':
                    action=next((k for k in options if k.startswith('study_')),None)
                    if action is None:
                        action='stop' if list(options)==['stop'] else next(k for k,v in options.items()
                            if k!='stop' and len(v['changes'])==1 and v['changes'][0]['parking']==1)
                elif phase=='oneway_target':
                    action=list(options)[-1]  # Non-first candidate must really control execution.
                else:
                    ordering=options if next_study_first else reversed(options)
                    action='study_99' if bad_route else next((k for k in ordering if k!='stop'),'stop')
                value=dict(action=action,summary='离线观测边界：已读取本组实际提供的合成配对记录。',
                    reason='依照当前合法菜单完成约定比较，保留收益和代价。',risk='合成数据只检验控制流程，不是交通效果证据。',
                    next_check='复核约定实验与未解决的证据缺口。',evidence_refs=payload['evidence_refs'][-1:])
                if 'next_check' not in tool['parameters']['properties']:value.pop('next_check')
                if 'summary' not in tool['parameters']['properties']:value.pop('summary')
                if 'unverified_risks' in tool['parameters']['properties']:
                    value.pop('risk');value['unverified_risks']=[]
                if 'selection_basis' in tool['parameters']['properties']:
                    from test_feedback_planner import choice_basis
                    value['selection_basis']=choice_basis(action,payload)
                    if context.get('choice_rationale_protocol') and value['selection_basis'] is not None:value['reason']=''
                    if bad_choice and phase=='oneway_target':value['selection_basis']['observed_ids']=['future:not_measured']
                def updates(hypotheses,evidence):
                    measured=next((f for f in evidence['findings'] if f['status']!='unmeasured' and f['evidence_refs']),None)
                    facts={f['hypothesis_id']:f for f in evidence['findings'] if f.get('evaluation')}
                    # Even an unavailable question has a frozen criterion. Its
                    # unknown finding cannot borrow another question's evidence.
                    return [dict(hypothesis_id=h['id'],judgment=facts[h['id']]['expected_judgment'],
                                 finding_ids=[facts[h['id']]['id']] if facts[h['id']]['status']!='unmeasured' and facts[h['id']]['evidence_refs'] else [],
                                 explanation='离线结构检查：分别保留核心收益、约束和邻区判据，不把它们互换。') if h['id'] in facts else
                            dict(hypothesis_id=h['id'],judgment='unresolved',finding_ids=[measured['id']] if measured else [],
                                 explanation='本地合成检验保留证据边界，不将配对数据外推为实际因果。') for h in hypotheses]
                if 'hypothesis_updates' in tool['parameters']['properties']:
                    value['hypothesis_updates']=updates(context['initial_hypotheses'],context['research_evidence']) if action=='stop' or context.get('require_hypothesis_review') or action in context.get('study_handoff',{}).get('actions',[]) else []
                    if bad_handoff_review and context.get('study_handoff'):value['hypothesis_updates']=[]
                if 'programme_updates' in tool['parameters']['properties']:
                    value.update(programme_updates=updates(context['programme_hypotheses'],context['programme_evidence']) if action=='stop' else [],
                                 programme_summary='两组分别复核各自基准；不混合收益或推断共同实施效果。' if action=='stop' else '')
                if 'cost_acknowledgments' in tool['parameters']['properties']:
                    value['cost_acknowledgments']=[{k:r[k] for k in ('ref','constraint_passed','has_measured_cost','neighbor_new_oversaturated_km')}
                        for r in context['measured_conclusion']['costs']] if action=='stop' or context.get('require_hypothesis_review') or action in context.get('study_handoff',{}).get('actions',[]) else []
                    if bad_final_cost and action=='stop' and context.get('programme_final_group'):
                        value['cost_acknowledgments'][0]['neighbor_new_oversaturated_km']=999
                    if bad_handoff_review and context.get('study_handoff'):
                        value['cost_acknowledgments']=[]
                if 'reviewed_cost_refs' in tool['parameters']['properties']:
                    value['reviewed_cost_refs']=[r['ref'] for r in context['measured_conclusion']['costs']] if action=='stop' or context.get('require_hypothesis_review') or action in context.get('study_handoff',{}).get('actions',[]) else []
                    if bad_final_cost and action=='stop' and context.get('programme_final_group'):
                        value['reviewed_cost_refs'][0]='not:measured'
                    if bad_handoff_review and context.get('study_handoff'):
                        value['reviewed_cost_refs']=[]
                if premature_programme and phase=='oneway_target':
                    value.update(programme_updates=[],programme_summary='过早形成整个任务结论，必须被拒绝。')
                if final_correction and action=='stop' and context.get('programme_final_group'):
                    if not payload.get('review_feedback') or final_correction=='twice':
                        value['programme_updates'][0]['finding_ids'].append('study_1.strength')
                    elif final_correction=='timeout':raise TimeoutError('offline correction transport interruption')
                if wrong_contract and action=='stop' and context.get('programme_final_group') and (not payload.get('review_feedback') or wrong_contract=='twice'):
                    value['programme_updates'][0]['judgment']='unresolved'
                if handoff_correction and (context.get('study_handoff') or (phase=='route' and action.startswith('study_'))):
                    if not payload.get('review_feedback') or handoff_correction=='twice':
                        if 'reviewed_cost_refs' in value:value['reviewed_cost_refs'][0]='not:measured'
                        else:value['cost_acknowledgments'][0]['neighbor_new_oversaturated_km']=999
                    elif handoff_correction=='timeout':raise TimeoutError('offline handoff correction interruption')
                    elif handoff_correction=='change_action':value['action']='stop'
                if lock_accepted_snapshot and phase=='parking' and action!='stop' and not held:
                    held['path']=manager.root/'dispatches'/f'{created[0].job_id}.json'
                    held['reader']=held['path'].open('rb')
            if wire=='chat_completions':
                response=dict(id=f'OFFLINE_{len(requests)}',model='offline-fixture',usage=dict(total_tokens=100,prompt_tokens=70,completion_tokens=30),
                    choices=[dict(finish_reason='tool_calls',message=dict(role='assistant',tool_calls=[dict(
                        id=f'offline_call_{len(requests)}',type='function',function=dict(name=tool['name'],arguments=json.dumps(value,ensure_ascii=False)))]))])
            else:
                response=dict(id=f'OFFLINE_{len(requests)}',model='offline-fixture',status='completed',usage=dict(input_tokens=70,output_tokens=30,total_tokens=100),
                    output=[dict(type='function_call',call_id=f'offline_call_{len(requests)}',name=tool['name'],arguments=json.dumps(value,ensure_ascii=False))])
            return io.BytesIO(json.dumps(response).encode('utf-8'))

        cls=ChatCompletionsClient if wire=='chat_completions' else ResponsesClient
        client=cls(dict(DEFAULTS,base_url='https://offline.invalid/v1',wire_api=wire),'OFFLINE_ONLY',Mock(open=Mock(side_effect=transport)))
        def create(*args,**kwargs):
            agent=Agent(*args,**kwargs);created.append(agent);return agent
        def simulate(agent,name,changes,factor=1):
            rows=synthetic_roads();started=now()
            if no_candidate and agent.task.scenario=='parking':
                for row in rows[:2]:row['parking']=1
            for row in rows:row['flow_pcu_h']*=factor
            if changes and not no_gain:
                for row in rows:
                    if row['index'] in agent.scope['core']:row['speed_kmh']+=6
            if changes and neighbor_loss:
                for row in rows:
                    if row['index'] in agent.scope['neighbor']:row['speed_kmh']=2
            agent.state['run_count']+=1
            executed.append(dict(job_id=agent.job_id,scenario=agent.task.scenario,name=name,changes=copy.deepcopy(changes),started=started))
            if native_failure and changes and agent.task.scenario=='oneway':
                raise RuntimeError('offline native boundary failure')
            record=dict(name=name,changes=copy.deepcopy(changes),demand_factor=factor,scenario=name,
                        execution=dict(status='completed',started=started,finished=now(),assigned_car_od_sum=100*factor,
                                       modules=[dict(name='OFFLINE_BOUNDARY_ONLY')]),
                        metrics=scoped_metrics(rows,agent.scope) if agent.scope else None)
            if agent.scope:record['core_rows']=[rows[i] for i in agent.scope['core']]
            if agent.state.get('regions'):
                from traffic_agent.spatial_planning import observe
                record['spatial_observation']=observe(rows,agent.state['regions'])
            agent.state['runs'].append(record);agent.persist()
            return rows,record
        with ExitStack() as stack:
            if lock_accepted_snapshot:stack.enter_context(patch.object(Path,'replace',replace))
            for target,kwargs in [
                ('traffic_agent.dispatch.input_fingerprints',dict(return_value={})),
                ('traffic_agent.preflight.check',dict(return_value=dict(errors=[]))),
                ('traffic_agent.dispatch.client_for',dict(return_value=client)),
                ('traffic_agent.dispatch.Agent',dict(side_effect=create)),
                ('traffic_agent.natural_plan.context_for',dict(return_value=catalog)),
                ('traffic_agent.agent.validate_source',dict(return_value={})),
                ('traffic_agent.agent.input_fingerprints',dict(return_value={})),
                ('traffic_agent.agent.setup_runtime',dict(return_value=root/'runtime')),
                ('traffic_agent.hard_access.install',dict(return_value=(root/'runtime',dict(version='offline-fixture')))),
                ('traffic_agent.oneway_agent.OneWayNetwork.load',dict(return_value=Mock(validate=Mock(side_effect=graph_check)))),
                ('traffic_agent.oneway_agent.numeric',dict(return_value=[400]*6)),
                ('traffic_agent.oneway_agent.response',dict(return_value=dict(passed=True,flows=dict(car=dict(before=400))))),
                ('traffic_agent.report.write_report',dict()),('traffic_agent.agent.Agent.event',dict())]:
                stack.enter_context(patch(target,**kwargs))
            stack.enter_context(patch.object(Agent,'simulation',simulate))
            preview=manager.preview(dict(matcher='model',request=request_text))
            self.assertEqual(preview['status'],'ready',preview.get('missing'))
            launched=manager.launch(preview['ticket']);manager.active.join(10)
            self.assertFalse(manager.active.is_alive(),'Synthetic controller workflow did not terminate')
            record=json.loads((manager.root/'dispatches'/f"{launched['id']}.json").read_text('utf-8'))
            frozen=json.loads((manager.root/'programmes'/launched['id']/'plan.json').read_text('utf-8'))
            self.assertTrue(manager.launch(preview['ticket'])['reused'])
        return dict(record=record,plan=frozen,agents=created,requests=requests,executed=executed,root=manager.root,
                    storage_conflicts=storage_conflicts)

    def test_mixed_real_controllers_complete_both_orders_and_wire_protocols(self):
        for reverse in (False,True):
            for wire in ('chat_completions','responses'):
                with self.subTest(reverse=reverse,wire=wire):
                    result=self.exercise(reverse=reverse,wire=wire);record=result['record']
                    self.assertEqual(record['status'],'completed',record.get('error'))
                    self.assertEqual(len(result['executed']),6)
                    self.assertEqual(record['model_budget']['used'],5)
                    self.assertTrue(record['programme_review']['complete'])
                    for t in record['planning_trace']:
                        self.assertNotIn('hypothesis_updates',t)
                        self.assertEqual(t['input']['context']['initial_hypotheses'],[])
                        self.assertEqual(t['input']['context']['review_scope']['mode'],'programme_final_only')
                        if t is not record['planning_trace'][-1]:self.assertNotIn('programme_updates',t)
                    self.assertEqual(len(record['planning_trace'][-1]['programme_updates']),len(result['plan']['hypotheses']))
                    states={a.job_id:a.state for a in result['agents']}
                    acceptance=verify_case_states(states,'programme')
                    self.assertEqual(acceptance['run_count'],6)
                    self.assertEqual(acceptance['model_selected_parking_policies'],1)
                    self.assertEqual(acceptance['model_selected_oneway_targets'],1)
                    self.assertTrue(verify_programme(result['plan'],record,states)['passed'])
                    for a in result['agents']:
                        diagnosis=json.loads((a.job/'diagnosis.json').read_text('utf-8'))
                        self.assertTrue(verify_planning(a.state,diagnosis)['passed'])
                        self.assertEqual(a.state['choice_basis_protocol'],result['plan']['choice_basis_protocol'])
                        self.assertEqual(json.loads((a.job/'plan.json').read_text('utf-8'))['choice_basis_protocol'],a.state['choice_basis_protocol'])
                        self.assertEqual(a.state['finding_reference_schema'],result['plan']['finding_reference_schema'])
                        self.assertEqual(json.loads((a.job/'plan.json').read_text('utf-8'))['finding_reference_schema'],a.state['finding_reference_schema'])
                        for key in ('input_protocol','choice_rationale_protocol','risk_review_protocol'):
                            self.assertEqual(a.state[key],result['plan'][key])
                            self.assertEqual(json.loads((a.job/'plan.json').read_text('utf-8'))[key],a.state[key])
                    self.assertEqual(result['plan']['study_comparison_protocol'],'study-comparison-v2')
                    self.assertEqual(len(record['programme_review']['comparisons']),2)
                    one=next(a for a in result['agents'] if a.task.scenario=='oneway')
                    target=next(t for t in record['planning_trace'] if t['kind']=='oneway_target')
                    self.assertEqual(one.state['factorial_design']['closed_direction'],target['selected']['closed_direction'])
                    self.assertNotEqual(target['action'],next(iter(target['input']['allowed_actions'])))
                    self.assertEqual(len(record['planning_trace'][-1]['reviewed_cost_refs']),4)
                    if not reverse:
                        first=result['agents'][0]
                        handoff=next(t for t in record['planning_trace'] if t['input']['context'].get('study_handoff'))
                        self.assertEqual(handoff['action'],'study_2')
                        self.assertNotIn('stop',handoff['input']['allowed_actions'])
                        self.assertTrue(first.state['research_review']['reviewed'])
                        self.assertTrue(first.state['goal_review']['complete'])
                        self.assertEqual(first.state['decisions'][-1]['execution_gate'],'study_handoff')
                        self.assertFalse(any(t['kind']=='route' and t['job_id']==first.job_id for t in record['planning_trace']))

    def test_unobserved_choice_stops_before_native_policy_and_retains_failed_reply(self):
        for wire in ('chat_completions','responses'):
            with self.subTest(wire=wire):
                result=self.exercise(wire=wire,bad_choice=True);record=result['record']
                self.assertEqual(record['status'],'incomplete')
                self.assertEqual(record['model_budget']['used'],4)
                self.assertEqual(len(result['executed']),3)
                entry=record['planning_trace'][-1]
                self.assertEqual(entry['validation']['code'],'choice_basis')
                self.assertEqual(entry['proposal_excerpt']['selection_basis']['observed_ids'],['future:not_measured'])
                self.assertNotIn('review_correction',entry)
                one=next(a for a in result['agents'] if a.task.scenario=='oneway')
                self.assertTrue(all(not r['changes'] for r in one.state['runs']))
                self.assertTrue(verify_programme(result['plan'],record,{a.job_id:a.state for a in result['agents']})['passed'])

    def test_choice_protocol_and_sources_cannot_be_rewritten_after_execution(self):
        from traffic_agent.choice_evidence import verify as verify_choice
        result=self.exercise();record=result['record'];plan=result['plan']
        for mode in ('state_protocol','trace_protocol','source_units','choice'):
            states={a.job_id:copy.deepcopy(a.state) for a in result['agents']}
            bad=copy.deepcopy(record);entry=bad['planning_trace'][0]
            if mode=='state_protocol':states[entry['job_id']].pop('choice_basis_protocol')
            elif mode=='trace_protocol':entry['input']['context'].pop('choice_basis_protocol')
            elif mode=='source_units':entry['input']['context']['choice_observations']['units']['baseline_car_flow']='pcu/h'
            else:entry['selection_basis']['alternative_action']=entry['action']
            entry['observation_digest']=digest(entry['input'])
            if mode!='state_protocol':
                with self.subTest(mode=mode),self.assertRaises(ValueError):verify_choice(entry)
                states[entry['job_id']]['planner_trace'][0]=copy.deepcopy(entry)
            with self.subTest(mode=mode),self.assertRaises(ValueError):verify_programme(plan,bad,states)

    def test_measurement_contracts_survive_both_wires_orders_and_initial_audit(self):
        from traffic_agent.initial_planner import verify_initial
        for wire in ('chat_completions','responses'):
            for reverse in (False,True):
                with self.subTest(wire=wire,reverse=reverse):
                    result=self.exercise(wire=wire,reverse=reverse,measurement_contracts=True)
                    record=result['record'];states={a.job_id:a.state for a in result['agents']}
                    self.assertEqual(record['status'],'completed',record.get('error'))
                    self.assertEqual(record['model_budget']['used'],5)
                    self.assertEqual(verify_initial(record,result['plan']),1)
                    self.assertTrue(verify_programme(result['plan'],record,states)['passed'])
                    self.assertEqual(len(record['programme_review']['updates']),3)
                    self.assertTrue(all('hypothesis.' in u['finding_ids'][0] for u in record['programme_review']['updates']))
                    # The other road has not produced any observations at the
                    # first choice; a frozen criterion must not read its future.
                    first=record['planning_trace'][0]['input']['context']['programme_evidence']
                    self.assertEqual([f['expected_judgment'] for f in first['findings'] if f.get('evaluation')],['unresolved']*3)

    def test_new_reference_and_comparison_contract_cannot_be_dropped_after_execution(self):
        from traffic_agent.research_evidence import LEGACY_REFERENCE_SCHEMA
        result=self.exercise(measurement_contracts=True)
        for mode in ('state','trace','entry','coverage','checks','state_input','trace_input','state_rationale','trace_rationale','state_risk','trace_risk'):
            states={a.job_id:copy.deepcopy(a.state) for a in result['agents']}
            record=copy.deepcopy(result['record']);entry=record['planning_trace'][-1]
            context=entry['input']['context']
            if mode=='state':states[entry['job_id']].pop('finding_reference_schema')
            elif mode=='trace':context.pop('finding_reference_schema')
            elif mode=='entry':entry['finding_reference_schema']=LEGACY_REFERENCE_SCHEMA
            elif mode=='coverage':context['programme_comparisons'][0]['completed_scenes'].pop()
            elif mode=='checks':context['programme_comparisons'][1]['factors'][0]['policies'][-1]['checks']['control_passed']=False
            else:
                target=states[entry['job_id']] if mode.startswith('state_') else context
                target.pop('input_protocol' if mode.endswith('_input') else 'risk_review_protocol' if mode.endswith('_risk') else 'choice_rationale_protocol')
            entry['observation_digest']=digest(entry['input'])
            with self.subTest(mode=mode),self.assertRaises(ValueError):verify_programme(result['plan'],record,states)

    def test_wrong_frozen_judgment_repairs_once_without_more_simulations(self):
        for mode in ('once','twice'):
            result=self.exercise(measurement_contracts=True,wrong_contract=mode);record=result['record']
            self.assertEqual(record['model_budget']['used'],6)
            self.assertEqual(len(result['executed']),6)
            self.assertEqual(record['status'],'completed' if mode=='once' else 'incomplete')
            correction=record['planning_trace'][-1]['review_correction']
            self.assertEqual(correction['first_attempt']['validation']['code'],'hypothesis_contract')
            self.assertFalse(correction['first_attempt']['proposal_capture']['excerpted'])
            self.assertTrue(verify_programme(result['plan'],record,{a.job_id:a.state for a in result['agents']})['passed'])

    def test_field_patch_survives_both_studies_final_counterexamples_and_both_wire_orders(self):
        from traffic_agent.initial_planner import verify_initial
        from traffic_agent.hypothesis_policy_patch import ORIGIN
        for wire in ('chat_completions','responses'):
            for reverse in (False,True):
                with self.subTest(wire=wire,reverse=reverse):
                    result=self.exercise(wire=wire,reverse=reverse,measurement_contracts=True,hypothesis_patch=True,neighbor_loss=True,separation_notes=True)
                    record=result['record'];states={a.job_id:a.state for a in result['agents']}
                    self.assertEqual(record['status'],'completed',record.get('error'))
                    self.assertEqual((len(result['requests']),len(result['executed']),record['model_budget']['used']),(6,6,6))
                    self.assertEqual(sum(r['name']=='baseline' for r in result['executed']),2)
                    first,last=record['initial_planning_attempts']
                    self.assertEqual(last['policy_patch']['selections'],{'H1':'parking'})
                    self.assertEqual(last['proposal_origin'],ORIGIN)
                    from traffic_agent.question_wording import VERSION as wording_version
                    self.assertEqual(first['hypothesis_check']['wording_protocol'],wording_version)
                    self.assertEqual([h['hypothesis'] for h in first['proposal']['assessment']],
                                     [h['hypothesis'] for h in result['plan']['hypotheses']])
                    self.assertEqual([u['judgment'] for u in record['programme_review']['updates']],['supported','contradicted','contradicted'])
                    self.assertTrue(all(a.state['recommendation']=='baseline' for a in result['agents']))
                    self.assertEqual(result['plan']['hypotheses'][0]['evaluation']['interpretation']['wording_notes'][0]['text'],
                                     '且判定与是否通过推荐约束分开')
                    self.assertEqual(verify_initial(record,result['plan']),2)
                    self.assertTrue(verify_programme(result['plan'],record,states)['passed'])
                    self.assertEqual(verify_case_states(states,'programme')['run_count'],6)
                    for a in result['agents']:
                        self.assertTrue(verify_planning(a.state,json.loads((a.job/'diagnosis.json').read_text('utf-8')))['passed'])

    def test_field_patch_uses_seventh_request_for_one_final_review_repair(self):
        for wire in ('chat_completions','responses'):
            with self.subTest(wire=wire):
                result=self.exercise(wire=wire,measurement_contracts=True,hypothesis_patch=True,wrong_contract='once')
                record=result['record'];states={a.job_id:a.state for a in result['agents']}
                self.assertEqual((len(result['requests']),len(result['executed']),record['model_budget']['used']),(7,6,7))
                self.assertEqual(record['status'],'completed')
                self.assertTrue(record['programme_review']['complete'])
                final=record['planning_trace'][-1]
                self.assertEqual(final['review_correction']['first_attempt']['validation']['code'],'hypothesis_contract')
                self.assertEqual(final['status'],'accepted')
                self.assertTrue(verify_programme(result['plan'],record,states)['passed'])

    def test_quantifier_field_patch_keeps_original_meaning_through_two_groups_and_negative_results(self):
        from traffic_agent.initial_planner import verify_initial
        for wire in ('chat_completions','responses'):
            for reverse in (False,True):
                with self.subTest(wire=wire,reverse=reverse):
                    result=self.exercise(wire=wire,reverse=reverse,measurement_contracts=True,quantifier_patch=True,
                                         hypothesis_patch=True,neighbor_loss=True,separation_notes=True)
                    record=result['record'];states={a.job_id:a.state for a in result['agents']}
                    self.assertEqual(record['status'],'completed');self.assertEqual(record['model_budget']['used'],6)
                    first,last=record['initial_planning_attempts']
                    self.assertEqual(first['proposal']['assessment'][0]['criterion']['quantifier'],'all')
                    self.assertEqual(last['policy_patch']['selections'],dict(H1=dict(policies='parking',quantifier='any')))
                    self.assertEqual(first['proposal']['assessment'][0]['hypothesis'],last['proposal']['assessment'][0]['hypothesis'])
                    self.assertEqual(last['proposal']['assessment'][0]['criterion']['quantifier'],'any')
                    self.assertEqual(len(result['executed']),6);self.assertEqual(len(result['requests']),6)
                    self.assertEqual(verify_initial(record,result['plan']),2)
                    self.assertTrue(verify_programme(result['plan'],record,states)['passed'])
                    for agent in result['agents']:
                        self.assertTrue(verify_planning(agent.state,json.loads((agent.job/'diagnosis.json').read_text('utf-8')))['passed'])

    def test_core_benefit_and_admissibility_diverge_without_hiding_loss(self):
        for wire in ('chat_completions','responses'):
            result=self.exercise(wire=wire,measurement_contracts=True,neighbor_loss=True)
            record=result['record']
            self.assertEqual(record['status'],'completed')
            self.assertEqual([u['judgment'] for u in record['programme_review']['updates']],['supported','contradicted','contradicted'])
            self.assertTrue(all(a.state['recommendation']=='baseline' for a in result['agents']))
            self.assertEqual(record['model_budget']['used'],5)

    def test_single_study_final_review_keeps_local_criterion_ids_in_programme(self):
        from traffic_agent.initial_planner import verify_initial
        for wire in ('chat_completions','responses'):
            result=self.exercise(wire=wire,measurement_contracts=True,neighbor_loss=True,single_study=True)
            record=result['record'];states={a.job_id:a.state for a in result['agents']}
            self.assertEqual(record['status'],'completed')
            self.assertEqual(record['model_budget']['used'],3)
            self.assertEqual(len(result['executed']),2)
            self.assertEqual([u['finding_ids'] for u in record['programme_review']['updates']],
                             [['hypothesis.H1'],['hypothesis.H2'],['hypothesis.H3']])
            self.assertEqual(verify_initial(record,result['plan']),1)
            self.assertTrue(verify_programme(result['plan'],record,states)['passed'])
            agent=result['agents'][0]
            self.assertTrue(verify_planning(agent.state,json.loads((agent.job/'diagnosis.json').read_text('utf-8')))['passed'])

    def test_initial_criterion_cannot_change_after_results_even_with_rehashed_summary(self):
        from traffic_agent.initial_planner import verify_initial,evidence
        result=self.exercise(measurement_contracts=True)
        for changed in ('predicate','scope','missing_receipt','wording','wording_version'):
            record=copy.deepcopy(result['record']);plan=copy.deepcopy(result['plan'])
            if changed=='predicate':record['assessment'][0]['evaluation']['test']='admissible'
            elif changed=='scope':record['assessment'][0]['criterion']['studies']=[2]
            elif changed=='wording':record['assessment'][0]['evaluation']['interpretation']['question']='已回答所有现实问题'
            elif changed=='wording_version':record.pop('question_wording_protocol')
            else:record['initial_planning_attempts'][-1]['proposal'].pop('assessment')
            plan['initial_planning_digest']=digest(evidence(record))
            with self.subTest(changed=changed),self.assertRaises(ValueError):verify_initial(record,plan)

    def test_tampered_evaluation_cannot_hide_in_model_context(self):
        result=self.exercise(measurement_contracts=True);record=copy.deepcopy(result['record'])
        entry=record['planning_trace'][-1]
        criterion=next(f for f in entry['input']['context']['programme_evidence']['findings'] if f.get('evaluation'))
        criterion['expected_judgment']='unresolved';entry['observation_digest']=digest(entry['input'])
        entry['programme_updates'][0]['judgment']='unresolved'
        with self.assertRaisesRegex(ValueError,'跨组复核'):
            verify_programme(result['plan'],record,{a.job_id:a.state for a in result['agents']})

    def test_handoff_without_complete_review_never_starts_the_next_group(self):
        result=self.exercise(bad_handoff_review=True)
        record=result['record']
        self.assertEqual(record['status'],'incomplete')
        self.assertEqual(len(result['executed']),2)
        self.assertEqual(record['model_budget']['used'],4)  # One bounded repair; the incomplete review still blocks handoff.
        self.assertEqual(record['planning_trace'][-1]['validation']['code'],'measured_conclusion')
        self.assertEqual(record['progress'][1]['status'],'skipped')
        self.assertNotIn('selected',record['planning_trace'][-1])

    def test_premature_final_fields_are_rejected_before_target_policies_in_both_wires(self):
        for wire in ('chat_completions','responses'):
            with self.subTest(wire=wire):
                result=self.exercise(wire=wire,premature_programme=True)
                record=result['record'];last=record['planning_trace'][-1]
                self.assertEqual((record['status'],last['kind']),('incomplete','oneway_target'))
                self.assertEqual(last['validation']['code'],'argument_fields')
                self.assertEqual(record['model_budget']['used'],4)
                self.assertEqual(len(result['executed']),3)
                self.assertFalse(record['programme_review']['complete'])
                self.assertNotIn('selected',last)
                self.assertEqual(result['agents'][1].state['run_count'],1)
                one=result['agents'][1]
                diagnosis=json.loads((one.job/'diagnosis.json').read_text('utf-8'))
                self.assertTrue(verify_planning(one.state,diagnosis)['passed'])
                self.assertFalse(one.state['research_review']['reviewed'])

    def test_handoff_audit_rejects_menu_budget_or_study_order_tampering(self):
        from traffic_agent.feedback_planner import digest
        result=self.exercise();states={a.job_id:a.state for a in result['agents']}
        for changed in ('menu','contract','budget','early'):
            record=copy.deepcopy(result['record']);case_states=copy.deepcopy(states)
            turn=next(t for t in record['planning_trace'] if t['input']['context'].get('study_handoff'))
            if changed=='menu':turn['input']['allowed_actions']['study_99']=turn['input']['allowed_actions'].pop('study_2')
            elif changed=='contract':turn['input']['context']['study_handoff']['actions']=[]
            elif changed=='budget':turn['input']['context']['route_budget']['requests_after_this_decision']+=1
            else:case_states[result['agents'][1].job_id]['runs'][0]['execution']['started']=turn['time']
            turn['observation_digest']=digest(turn['input'])
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                verify_programme(result['plan'],record,case_states)

    def test_zero_gain_preserves_both_baselines_and_negative_results(self):
        result=self.exercise(no_gain=True)
        self.assertEqual(result['record']['status'],'completed')
        self.assertTrue(all(a.state['recommendation']=='baseline' for a in result['agents']))
        self.assertTrue(all(not r['comparison']['eligible'] for a in result['agents'] for r in a.state['runs'] if r['changes']))

    @unittest.skipUnless(os.name=='nt','Actual Windows reader handle')
    def test_locked_accepted_snapshot_does_not_repeat_a_model_or_native_call(self):
        result=self.exercise(lock_accepted_snapshot=True)
        self.assertEqual(result['record']['status'],'completed',result['record'].get('error'))
        self.assertEqual(len(result['storage_conflicts']),1)
        self.assertIn(result['storage_conflicts'][0],(5,32,33))
        self.assertEqual(result['record']['model_budget']['used'],5)
        self.assertEqual(len(result['requests']),5)
        self.assertEqual(len(result['executed']),6)
        self.assertTrue(verify_programme(result['plan'],result['record'],{a.job_id:a.state for a in result['agents']})['passed'])

    def test_initial_correction_and_mixed_studies_still_fit_six_requests(self):
        for wire in ('chat_completions','responses'):
            with self.subTest(wire=wire):
                result=self.exercise(reverse=True,wire=wire,initial_correction=True)
                record=result['record']
                self.assertEqual(record['status'],'completed',record.get('error'))
                self.assertEqual(record['model_budget']['used'],6)
                self.assertEqual(len(record['initial_planning_attempts']),2)
                self.assertEqual(len(result['executed']),6)
                self.assertTrue(verify_programme(result['plan'],record,{a.job_id:a.state for a in result['agents']})['passed'])

    def test_next_study_menu_preserves_all_groups_and_final_review_budget(self):
        result=self.exercise(reverse=True,third_group=True);record=result['record']
        self.assertEqual(record['status'],'completed',record.get('error'))
        self.assertEqual(record['model_budget']['used'],6)
        self.assertEqual(len(result['executed']),7)
        self.assertEqual([s['task']['scenario'] for s in result['plan']['studies']],['oneway','parking','diagnosis'])
        self.assertEqual(list(dict.fromkeys(r['scenario'] for r in result['executed'])),['oneway','diagnosis','parking'])
        route=next(t for t in record['planning_trace'] if t['kind']=='route')
        self.assertEqual(route['action'],'study_3')
        self.assertEqual(list(route['input']['allowed_actions']),['study_2','study_3'])
        self.assertTrue(route['input']['context']['route_budget']['choices']['study_2']['feasible'])
        self.assertTrue(verify_programme(result['plan'],record,{a.job_id:a.state for a in result['agents']})['passed'])

    def test_budget_correction_completes_both_studies_in_six_calls_and_six_scenes(self):
        for wire in ('chat_completions','responses'):
            with self.subTest(wire=wire):
                result=self.exercise(wire=wire,budget_correction=True);record=result['record']
                self.assertEqual(record['status'],'completed',record.get('error'))
                self.assertEqual(record['model_budget']['used'],6)
                self.assertEqual(len(result['requests']),6)
                self.assertEqual(len(result['executed']),6)
                self.assertEqual(record['initial_planning_attempts'][0]['validation_code'],'draft_over_budget')
                states={a.job_id:a.state for a in result['agents']}
                self.assertTrue(verify_programme(result['plan'],record,states)['passed'])
                self.assertEqual(verify_case_states(states,'programme')['run_count'],6)

    def test_programme_audit_rejects_changed_reservations_routes_and_call_ledger(self):
        result=self.exercise(reverse=True,third_group=True)
        states={a.job_id:a.state for a in result['agents']}
        for changed in ('reservation','route','ledger','final_ledger'):
            record=copy.deepcopy(result['record'])
            if changed=='reservation':
                entry=next(t for t in record['planning_trace'] if t['kind']=='oneway_target')
                entry['input']['context']['reserved_model_requests']=0
            elif changed=='route':
                entry=next(t for t in record['planning_trace'] if t['kind']=='route')
                entry['input']['context']['route_budget']['choices']['study_2']['feasible']=False
            elif changed=='ledger':record['planning_trace'][0]['input']['model_budget']['remaining']+=1
            else:record['model_budget']['used']-=1
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                verify_programme(result['plan'],record,states)

    def test_parking_in_the_middle_transfers_to_final_diagnosis_and_order_is_audited(self):
        result=self.exercise(reverse=True,third_group=True,next_study_first=True);record=result['record']
        self.assertEqual(record['status'],'completed',record.get('error'))
        self.assertEqual(record['model_budget']['used'],6)
        self.assertEqual(len(result['executed']),7)
        self.assertEqual(list(dict.fromkeys(r['scenario'] for r in result['executed'])),['oneway','parking','diagnosis'])
        handoff=next(t for t in record['planning_trace'] if t['input']['context'].get('study_handoff'))
        self.assertEqual(handoff['action'],'study_3')
        self.assertTrue(record['programme_review']['complete'])
        states={a.job_id:a.state for a in result['agents']}
        self.assertTrue(verify_programme(result['plan'],record,states)['passed'])
        tampered=copy.deepcopy(record)
        turn=next(t for t in tampered['planning_trace'] if t['kind']=='route')
        turn['action']='study_3';turn['selected']=turn['input']['allowed_actions']['study_3']
        from traffic_agent.action_execution import generated_values
        turn.update(generated_values(turn))
        with self.assertRaisesRegex(ValueError,'实际下一组没有遵循模型选择'):
            verify_programme(result['plan'],tampered,states)

    def test_actual_policy_facts_cannot_be_changed_or_imported_from_future_groups(self):
        from traffic_agent.feedback_planner import digest
        from traffic_agent.conclusion_review import contract
        result=self.exercise();states={a.job_id:a.state for a in result['agents']}
        self.assertTrue(verify_programme(result['plan'],result['record'],states)['passed'])
        for changed in ('changed','future','missing'):
            record=copy.deepcopy(result['record'])
            final=record['planning_trace'][-1]
            turn=record['planning_trace'][0] if changed=='future' else final
            ctx=turn['input']['context']
            if changed=='future':
                ctx['programme_policy_facts']=copy.deepcopy(final['input']['context']['programme_policy_facts'])
            elif changed=='missing':
                del ctx['programme_policy_facts'];del ctx['programme_cost_facts']
            else:
                ctx['programme_policy_facts'][0]['parking_levels']=[3]
            ctx['measured_conclusion']=contract(turn['input']['observations'],ctx)
            turn['observation_digest']=digest(turn['input'])
            with self.subTest(changed=changed),self.assertRaisesRegex(ValueError,'实际措施|代价清单|阶段提示|实测代价说明'):
                verify_programme(result['plan'],record,states)

    def test_scope_and_within_study_comparisons_are_frozen_at_decision_time(self):
        result=self.exercise()
        states={a.job_id:a.state for a in result['agents']}
        for mutation in ('scope','missing_comparison','future_comparison','comparison_value','premature_hypothesis'):
            record=copy.deepcopy(result['record'])
            entry=record['planning_trace'][0]
            context=entry['input']['context']
            if mutation=='scope':context['review_scope']['mode']='single_study'
            elif mutation=='missing_comparison':context.pop('programme_comparisons')
            elif mutation=='future_comparison':context['programme_comparisons']=copy.deepcopy(record['planning_trace'][-1]['input']['context']['programme_comparisons'])
            elif mutation=='comparison_value':
                entry=record['planning_trace'][-1];context=entry['input']['context']
                context['programme_comparisons'][1]['factors'][0]['combination_contrasts'][0]['core_time_difference_pcu_h']=12345
            else:entry['hypothesis_updates']=[]
            entry['observation_digest']=digest(entry['input'])
            with self.subTest(mutation=mutation),self.assertRaisesRegex(ValueError,'复核范围|分组实测对照|本组引用|阶段提示'):
                verify_programme(result['plan'],record,states)

    def test_model_cannot_select_a_study_outside_the_remaining_programme(self):
        result=self.exercise(reverse=True,third_group=True,bad_route=True)
        self.assertEqual(result['record']['status'],'incomplete')
        self.assertEqual(len(result['executed']),4)  # Only the first study's factorial scenes.
        self.assertEqual(result['record']['planning_trace'][-1]['status'],'rejected')
        self.assertEqual(result['record']['model_budget']['used'],3)
        self.assertFalse(result['record']['programme_review']['complete'])

    def test_model_failure_never_replaces_choice_with_rules_or_continues_policies(self):
        result=self.exercise(fail_phase='oneway_target')
        self.assertEqual(result['record']['status'],'incomplete')
        one=next(a for a in result['agents'] if a.task.scenario=='oneway')
        self.assertEqual(one.state['run_count'],1)
        self.assertEqual(len(result['requests']),4)
        self.assertFalse(any(r['changes'] for r in one.state['runs']))

    def test_no_candidate_is_retained_and_other_required_group_is_still_researched(self):
        result=self.exercise(no_candidate=True)
        self.assertEqual(result['record']['progress'][0]['status'],'no_candidate')
        self.assertEqual(result['record']['progress'][1]['run_count'],4)
        self.assertEqual(result['record']['status'],'incomplete')
        self.assertFalse(result['record']['programme_review']['complete'])

    def test_bad_final_cost_holds_completion_without_erasing_six_finished_scenes(self):
        result=self.exercise(bad_final_cost=True)
        self.assertEqual(len(result['executed']),6)
        self.assertEqual(result['record']['status'],'incomplete')
        self.assertEqual(result['record']['planning_trace'][-1]['status'],'rejected')
        self.assertIn('proposed_decision',result['record']['planning_trace'][-1])

    def test_native_boundary_failure_stops_without_repeating_or_final_claim(self):
        result=self.exercise(native_failure=True)
        self.assertEqual(result['record']['status'],'failed')
        one=next(a for a in result['agents'] if a.task.scenario=='oneway')
        self.assertEqual(one.state['status'],'failed')
        self.assertEqual([r['name'] for r in result['executed'] if r['scenario']=='oneway'],['baseline','S1_parking'])
        self.assertFalse(result['record']['programme_review']['complete'])

    def test_final_correction_uses_six_calls_without_another_scene_in_both_protocols(self):
        for wire in ('chat_completions','responses'):
            with self.subTest(wire=wire):
                result=self.exercise(wire=wire,final_correction='valid');record=result['record']
                self.assertEqual(record['status'],'completed',record.get('error'))
                self.assertEqual((len(result['requests']),len(result['executed'])),(6,6))
                self.assertEqual(record['model_budget']['known_tokens'],600)
                self.assertEqual(record['model_budget']['used'],6)
                final=record['planning_trace'][-1]
                self.assertEqual(final['review_correction']['first_attempt']['validation']['code'],'unmeasured_finding')
                states={a.job_id:a.state for a in result['agents']}
                self.assertTrue(verify_programme(result['plan'],record,states)['passed'])
                for a in result['agents']:
                    diagnosis=json.loads((a.job/'diagnosis.json').read_text('utf-8'))
                    self.assertTrue(verify_planning(a.state,diagnosis)['passed'])
                for field in ('count','tokens','unknown','first_tokens','duplicate'):
                    broken=copy.deepcopy(record)
                    if field=='count':broken['model_budget']['used']-=1
                    elif field=='tokens':broken['model_budget']['known_tokens']-=100
                    elif field=='unknown':broken['model_budget']['unknown_usage']+=1
                    elif field=='first_tokens':broken['planning_trace'][-1]['review_correction']['first_attempt']['usage']['tokens']+=1
                    else:broken['planning_trace'].append(copy.deepcopy(final))
                    with self.subTest(field=field),self.assertRaises(ValueError):
                        verify_programme(result['plan'],broken,states)

    def test_handoff_repair_preserves_six_scenes_and_every_later_study_in_both_wires(self):
        from traffic_agent.planning_response import HANDOFF_CORRECTION_VERSION
        for wire in ('chat_completions','responses'):
            with self.subTest(wire=wire):
                result=self.exercise(wire=wire,handoff_correction='valid');record=result['record']
                self.assertEqual((record['status'],len(result['requests']),len(result['executed'])),('completed',6,6))
                self.assertEqual(record['model_budget']['known_tokens'],600)
                handoff=next(t for t in record['planning_trace'] if t.get('review_correction'))
                self.assertEqual(handoff['review_correction']['version'],HANDOFF_CORRECTION_VERSION)
                self.assertEqual(handoff['action'],handoff['review_correction']['first_attempt']['proposal_excerpt']['action'])
                self.assertEqual(handoff['review_correction']['input']['review_feedback']['reserved_after_correction'],2)
                self.assertEqual(handoff['input']['model_budget']['remaining'],5)
                target=next(t for t in record['planning_trace'] if t['kind']=='oneway_target')
                self.assertEqual(target['input']['model_budget']['remaining'],3)
                self.assertNotIn('review_correction',record['planning_trace'][-1])
                states={a.job_id:a.state for a in result['agents']}
                self.assertTrue(verify_programme(result['plan'],record,states)['passed'])
                for a in result['agents']:
                    self.assertTrue(verify_planning(a.state,json.loads((a.job/'diagnosis.json').read_text('utf-8')))['passed'])
                for field in ('protocol','reserve','ledger','first_tokens','duplicate','action'):
                    broken=copy.deepcopy(record)
                    t=next(t for t in broken['planning_trace'] if t.get('review_correction'))
                    if field=='protocol':t['input']['context'].pop('study_review_correction_protocol')
                    elif field=='reserve':t['input']['context']['route_budget']['choices'][t['action']]['required_requests']=0
                    elif field=='ledger':broken['planning_trace'][-1]['input']['model_budget']['used']-=1
                    elif field=='first_tokens':t['review_correction']['first_attempt']['usage']['tokens']+=1
                    elif field=='action':t['action']='stop'
                    else:broken['planning_trace'][-1]['review_correction']=copy.deepcopy(t['review_correction'])
                    with self.subTest(field=field),self.assertRaises(ValueError):verify_programme(result['plan'],broken,states)

    def test_handoff_repair_cannot_spend_reserve_retry_twice_or_change_action(self):
        for mode,initial in [('twice',False),('timeout',False),('change_action',False),('valid',True)]:
            with self.subTest(mode=mode,initial=initial):
                result=self.exercise(handoff_correction=mode,initial_correction=initial);record=result['record']
                expected=('completed',6,7) if mode=='valid' and initial else ('incomplete',2,4)
                self.assertEqual((record['status'],len(result['executed']),len(result['requests'])),expected)
                if mode=='valid' and initial:
                    self.assertTrue(verify_programme(result['plan'],record,{a.job_id:a.state for a in result['agents']})['passed'])
                    continue
                last=record['planning_trace'][-1]
                self.assertEqual('review_correction' in last,not initial)
                self.assertNotIn('selected',last)
                self.assertEqual(record['progress'][1]['status'],'skipped')
                self.assertEqual(record['model_budget']['unknown_usage'],int(mode=='timeout'))
                if mode=='change_action':self.assertEqual(last['validation']['code'],'correction_action')
                states={a.job_id:a.state for a in result['agents']}
                self.assertTrue(verify_programme(result['plan'],record,states)['passed'])
                for a in result['agents']:
                    if (a.job/'diagnosis.json').exists():
                        self.assertTrue(verify_planning(a.state,json.loads((a.job/'diagnosis.json').read_text('utf-8')))['passed'])

    def test_handoff_and_final_review_share_one_repair_and_original_total_budget(self):
        result=self.exercise(handoff_correction='valid',final_correction='valid');record=result['record']
        self.assertEqual((record['status'],len(result['requests']),len(result['executed'])),('incomplete',6,6))
        self.assertEqual(sum(bool(t.get('review_correction')) for t in record['planning_trace']),1)
        self.assertEqual(record['planning_trace'][-1]['status'],'rejected')
        self.assertNotIn('review_correction',record['planning_trace'][-1])
        self.assertTrue(verify_programme(result['plan'],record,{a.job_id:a.state for a in result['agents']})['passed'])

    def test_complete_oneway_group_can_repair_before_routing_to_fresh_parking_baseline(self):
        for wire in ('chat_completions','responses'):
            with self.subTest(wire=wire):
                result=self.exercise(reverse=True,wire=wire,handoff_correction='valid');record=result['record']
                self.assertEqual((record['status'],len(result['requests']),len(result['executed'])),('completed',6,6))
                turn=next(t for t in record['planning_trace'] if t.get('review_correction'))
                self.assertEqual((turn['kind'],turn['action']),('route','study_2'))
                self.assertTrue(verify_programme(result['plan'],record,{a.job_id:a.state for a in result['agents']})['passed'])
                for a in result['agents']:
                    self.assertTrue(verify_planning(a.state,json.loads((a.job/'diagnosis.json').read_text('utf-8')))['passed'])

    def test_component_budget_correction_preserves_six_independent_scenes(self):
        for wire in ('chat_completions','responses'):
            with self.subTest(wire=wire):
                result=self.exercise(wire=wire,component_correction=True);record=result['record']
                self.assertEqual((record['status'],len(result['requests']),len(result['executed'])),('completed',6,6))
                self.assertEqual(record['initial_planning_attempts'][0]['budget_check']['native']['required'],8)
                self.assertEqual([a.task.scenario for a in result['agents']],['parking','oneway'])
                self.assertEqual(sum(r['name']=='baseline' for r in result['executed']),2)
                states={a.job_id:a.state for a in result['agents']}
                self.assertTrue(verify_programme(result['plan'],record,states)['passed'])
                for a in result['agents']:
                    self.assertTrue(verify_planning(a.state,json.loads((a.job/'diagnosis.json').read_text('utf-8')))['passed'])

    def test_failed_correction_and_exhausted_budget_never_complete_the_programme(self):
        for mode,initial in [('twice',False),('timeout',False),('valid',True)]:
            with self.subTest(mode=mode,initial=initial):
                result=self.exercise(final_correction=mode,initial_correction=initial);record=result['record']
                if mode=='valid' and initial:
                    self.assertEqual((record['status'],len(result['executed'])),('completed',6))
                    self.assertEqual((record['model_budget']['used'],len(result['requests'])),(7,7))
                    continue
                self.assertEqual((record['status'],len(result['executed'])),('incomplete',6))
                self.assertEqual((record['model_budget']['used'],len(result['requests'])),(6,6))
                self.assertEqual('review_correction' in record['planning_trace'][-1],not initial)
                self.assertEqual(record['model_budget']['unknown_usage'],int(mode=='timeout'))
                self.assertFalse(record['programme_review']['complete'])
                self.assertTrue(verify_programme(result['plan'],record,{a.job_id:a.state for a in result['agents']})['passed'])
                for a in result['agents']:
                    diagnosis=json.loads((a.job/'diagnosis.json').read_text('utf-8'))
                    self.assertTrue(verify_planning(a.state,diagnosis)['passed'])

    def test_independent_diagnosis_and_governance_really_execute_separate_fresh_baselines(self):
        for mode in ('same_road','different_roads'):
            for wire in ('chat_completions','responses'):
                with self.subTest(mode=mode,wire=wire):
                    result=self.exercise(wire=wire,diagnostic_pair=mode);record=result['record']
                    self.assertEqual(record['status'],'completed',record.get('error'))
                    self.assertEqual((len(result['executed']),len(result['requests'])),(3,4))
                    self.assertEqual([a.task.scenario for a in result['agents']],['diagnosis','parking'])
                    self.assertEqual(sum(r['name']=='baseline' for r in result['executed']),2)
                    diagnostic=result['agents'][0]
                    self.assertTrue(all(not r['changes'] for r in result['executed'] if r['job_id']==diagnostic.job_id))
                    states={a.job_id:a.state for a in result['agents']}
                    self.assertTrue(verify_programme(result['plan'],record,states)['passed'])
                    for a in result['agents']:
                        self.assertTrue(verify_planning(a.state,json.loads((a.job/'diagnosis.json').read_text('utf-8')))['passed'])


if __name__=='__main__':unittest.main()
