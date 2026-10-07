import copy
import unittest

from traffic_agent.research_evidence import findings
from traffic_agent.runner import now
from traffic_agent.task import Task


def study_state(id,task,bad_combination=False,bad_response=False):
    """Synthetic paired arrays for orchestration checks, never native evidence."""
    scenario=task['scenario']
    parking=[dict(from_node=1,to_node=2,old_parking=3,parking=2)]
    control=[dict(kind='oneway',closed_direction=[1,2])] if scenario=='oneway' else [dict(kind='turn',turn=[1,2,3])]
    labels=['S1_parking','S2_oneway' if scenario=='oneway' else 'S2_turn','S3_combined']
    variants=dict(zip(labels,[parking,control,parking+control])) if scenario in ('oneway','circulation') else {'parking_1':parking} if scenario=='parking' else {}
    state=dict(id=id,task=copy.deepcopy(task),status='completed',runs=[],recommendation='baseline')
    def metrics(time):return {k:dict(running_time_pcu_h=time,weighted_speed_kmh=30,directed_links=3,oversaturated_km=0) for k in ('network','core','neighbor')}
    for factor in task['demand_factors']:
        prefix='' if factor==1 else f'demand_{factor}_'
        base=dict(name=prefix+'baseline',changes=[],demand_factor=factor,metrics=metrics(100*factor),
                  execution=dict(status='completed',started=now(),finished=now()))
        state['runs'].append(base)
        for index,(label,changes) in enumerate(variants.items()):
            network=(90-index*5)*factor
            rejected=bad_combination and label=='S3_combined'
            fail_control=bad_response and any('kind' in c for c in changes)
            check=dict(eligible=not (rejected or fail_control),violations=['合成邻区代价超限'] if rejected else ['合成原生控制未生效'] if fail_control else [],
                       delta={k:dict(running_time_change_pct=(network/(100*factor)-1)*100) for k in ('network','core','neighbor')},new_neighbor_oversaturated_km=0)
            r=dict(name=prefix+label,changes=copy.deepcopy(changes),demand_factor=factor,policy_label=label,
                   metrics=metrics(network),comparison=check,execution=dict(status='completed',started=now(),finished=now()))
            if any('kind' in c for c in changes):r['oneway_response' if scenario=='oneway' else 'turn_response']=dict(passed=not fail_control)
            state['runs'].append(r)
        if factor==1 and variants:state['recommendation']=labels[0] if scenario!='parking' else 'parking_1'
    if task['repeat_baseline']:
        repeat=copy.deepcopy(next(r for r in state['runs'] if r['name']=='baseline'))
        repeat.update(name='baseline_repeat',execution=dict(status='completed',started=now(),finished=now()))
        state['runs'].append(repeat);state['repeatability']=dict(exact_match=True)
    state['run_count']=len(state['runs'])
    if scenario in ('oneway','circulation'):state['factorial_design']=dict(policies=variants)
    return state


class StrategyEvidenceTests(unittest.TestCase):
    def state(self,scenario='oneway',**kwargs):
        task=Task.from_dict(dict(scenario=scenario,turn=[1,2,3] if scenario=='circulation' else None,
                                demand_factors=[1],repeat_baseline=False,max_runs=4,recommendation_version='tolerance-v2'))
        return study_state('synthetic',task.as_dict(),**kwargs)

    def test_four_policy_contrast_keeps_failed_combination_and_nonadditivity(self):
        evidence=findings(self.state(bad_combination=True));facts={f['id']:f for f in evidence['findings']}
        self.assertEqual(facts['factorial']['status'],'measured')
        self.assertEqual(facts['combination']['status'],'observed')
        row=facts['factorial']['measurements'][0]
        self.assertEqual(row['interaction_pcu_h'],5)
        self.assertFalse(row['cells'][2]['eligible'])
        self.assertNotEqual(evidence['preferred_tested_policy'],'S3_combined')
        self.assertEqual(facts['robustness']['status'],'unmeasured')

    def test_missing_or_false_native_response_cannot_prove_control_effect(self):
        state=self.state('circulation',bad_response=True)
        evidence=findings(state);facts={f['id']:f for f in evidence['findings']}
        self.assertEqual(facts['response']['status'],'not_supported')
        self.assertEqual(evidence['preferred_tested_policy'],'S1_parking')
        for r in state['runs']:
            r.pop('turn_response',None)
            if r.get('comparison'):r['comparison'].update(eligible=True,violations=[])
        evidence=findings(state);facts={f['id']:f for f in evidence['findings']}
        self.assertEqual(facts['response']['status'],'unmeasured')
        self.assertEqual(evidence['preferred_tested_policy'],'S1_parking')

    def test_missing_policy_is_not_full_factorial_and_failed_outputs_are_excluded(self):
        state=self.state();state['runs'][-1]['execution']['status']='failed'
        result=findings(state)
        self.assertFalse(result['execution_complete'])
        self.assertEqual(result['findings'][0]['status'],'unmeasured')
        self.assertNotIn('synthetic:S3_combined',str(result))

    def test_diagnosis_can_only_support_observations_not_treatment_effect(self):
        task=Task.from_dict(dict(scenario='diagnosis',demand_factors=[1],repeat_baseline=False,max_runs=2))
        facts={f['id']:f for f in findings(study_state('diagnosis',task.as_dict()))['findings']}
        self.assertEqual(facts['diagnosis']['status'],'measured')
        self.assertEqual(facts['benefit']['status'],'unmeasured')


if __name__=='__main__':unittest.main()
