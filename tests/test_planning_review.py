import copy
import unittest
from traffic_agent.planning_review import coverage,decision_notes,parking_gate,measured_policies,PREVIOUS_GATE_VERSION


class PlanningReviewTests(unittest.TestCase):
    def test_explicit_candidate_comparison_blocks_early_stop_and_counts_full_pairs(self):
        task=dict(scenario='parking',request='比较两个停车备选方案',demand_factors=[0.9,1.0],
                  comparison_policies=2,max_runs=7,repeat_baseline=False)
        def run(level,pair,factor):
            return dict(execution=dict(status='completed'),changes=[dict(from_node=pair[0],to_node=pair[1],parking=level)],demand_factor=factor)
        first=[run(2,(1,2),f) for f in task['demand_factors']]
        state=dict(task=task,runs=first,run_count=4,planning_protocol='observed-planning-v3')
        options={'other':dict(changes=[dict(from_node=3,to_node=4,parking=2)])}
        _,guard=parking_gate(state,options,available=3,cost=2,model_remaining=3)
        self.assertFalse(guard['stop_allowed'])
        self.assertEqual(guard['measured_candidate_comparisons'],1)
        self.assertEqual(guard['required_candidate_comparisons'],2)
        self.assertFalse(next(c for c in coverage(state)['requirements'] if c['id']=='alternatives')['fulfilled'])
        state['runs'].append(run(2,(3,4),1.0))
        self.assertFalse(next(c for c in coverage(state)['requirements'] if c['id']=='alternatives')['fulfilled'])
        state['runs'].append(run(2,(3,4),0.9))
        self.assertTrue(next(c for c in coverage(state)['requirements'] if c['id']=='alternatives')['fulfilled'])
        _,guard=parking_gate(state,{},available=0,cost=2,model_remaining=1)
        self.assertTrue(guard['stop_allowed'])
        legacy=dict(state,task=dict(task,comparison_policies=None))
        _,old_guard=parking_gate(legacy,{},available=0,cost=2,model_remaining=1,version=PREVIOUS_GATE_VERSION)
        self.assertEqual(old_guard['version'],PREVIOUS_GATE_VERSION)
        self.assertNotIn('required_candidate_comparisons',old_guard)

    def test_candidate_order_does_not_create_a_second_policy(self):
        one=[dict(from_node=1,to_node=2,parking=2),dict(from_node=3,to_node=4,parking=2)]
        state=dict(task=dict(demand_factors=[1.0]),runs=[
            dict(changes=one,demand_factor=1.0,execution=dict(status='completed')),
            dict(changes=list(reversed(one)),demand_factor=1.0,execution=dict(status='completed'))])
        self.assertEqual(measured_policies(state),1)

    def state(self,levels,pairs=None):
        pairs=pairs or [(1,2)]*len(levels)
        return dict(task=dict(scenario='parking',request='比较停车治理强度',max_runs=5),run_count=len(levels)+1,
                    runs=[dict(execution=dict(status='completed'),changes=[dict(from_node=a,to_node=b,parking=level)],demand_factor=1)
                          for level,(a,b) in zip(levels,pairs)])

    def test_one_strength_is_not_a_completed_strength_comparison(self):
        r=coverage(self.state([1]))
        self.assertFalse(r['complete'])
        self.assertEqual(r['measured_levels'],[1])
        self.assertTrue(r['unanswered'])
        self.assertTrue(coverage(self.state([1,2]))['complete'])

    def test_different_directions_or_demands_do_not_prove_a_strength_comparison(self):
        self.assertFalse(coverage(self.state([1,2],[(1,2),(2,3)]))['complete'])
        s=self.state([1,2]);s['runs'][1]['demand_factor']=1.1
        self.assertFalse(coverage(s)['complete'])

    def test_stop_with_available_options_is_not_budget_exhaustion(self):
        t=dict(action='stop',reason='2级必然更差，停车0级待测，预算不足',input=dict(
            context=dict(available_policy_runs=1,policy_cost=1),allowed_actions=dict(stop={},parking_x={})))
        notes=decision_notes(t)
        self.assertTrue(any('并非预算耗尽' in n for n in notes))
        self.assertTrue(any('不能断言必然更差' in n for n in notes))
        self.assertTrue(any('不在可执行目录' in n for n in notes))
        # Derived annotations never alter the sealed decision.
        self.assertNotIn('verification_notes',t)

    def test_final_model_language_does_not_expand_actual_tool_or_recommendation_scope(self):
        t=dict(version='observed-planning-v2',kind='parking',action='stop',summary='1级为最优',risk='未测0级')
        notes=decision_notes(t)
        self.assertTrue(any('不在可执行目录' in n for n in notes))
        self.assertTrue(any('不能把' in n for n in notes))

    def test_programme_stop_uses_actual_per_group_coverage_retains_model_original(self):
        from traffic_agent.planning_review import public_stop_basis,display_trace
        from traffic_agent.study_comparison import VERSION
        groups=[dict(id='study_1',execution_complete=True),dict(id='study_2',execution_complete=True)]
        comparisons=[dict(version=VERSION,study_id=g['id'],title=g['id'],completed_scenes=[
            dict(ref=g['id']+':baseline',kind='baseline',demand_factor=1),
            dict(ref=g['id']+':policy',kind=k,demand_factor=1)]) for g,k in zip(groups,('parking','traffic_control'))]
        entry=dict(action='stop',status='accepted',reason='原错误解释：两组均完成全部组合对照。',
                   input=dict(context=dict(programme_evidence=dict(groups=groups),programme_comparisons=comparisons)))
        before=copy.deepcopy(entry)
        rendered=display_trace([entry])[0]
        self.assertEqual(rendered['stop_basis_origin'],'completed-study-comparisons')
        self.assertIn('study_1（新基准1场、仅停车1场',rendered['stop_basis'])
        self.assertIn('study_2（新基准1场、仅交通组织1场',rendered['stop_basis'])
        self.assertEqual(rendered['reason'],entry['reason'])
        self.assertEqual(entry,before)
        for mutation in ('unfinished','missing','old_version','not_stop','rejected'):
            bad=copy.deepcopy(entry);ctx=bad['input']['context']
            if mutation=='unfinished':ctx['programme_evidence']['groups'][0]['execution_complete']=False
            elif mutation=='missing':ctx['programme_comparisons'].pop()
            elif mutation=='old_version':ctx['programme_comparisons'][0]['version']='study-comparison-v1'
            elif mutation=='not_stop':bad['action']='study_2'
            else:bad['status']='rejected'
            with self.subTest(mutation=mutation):self.assertIsNone(public_stop_basis(bad))


if __name__=='__main__':unittest.main()
