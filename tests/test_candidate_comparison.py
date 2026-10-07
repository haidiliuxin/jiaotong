"""Changing multiple experimental dimensions must not imply attribution."""
import copy,unittest
from traffic_agent import candidate_comparison as cc,choice_evidence as ce
from traffic_agent.feedback_planner import decision_tool,instructions_for
from traffic_agent.report import candidate_comparison_lines
from test_choice_evidence import payload


def changes(pairs,level):
    return [dict(from_node=a,to_node=b,parking=level) for a,b in pairs]


class CandidateComparisonTests(unittest.TestCase):
    def prepared(self,version=ce.V6_VERSION):
        old=changes([(1,2),(3,4),(5,6)],1)
        p=payload(trial=True,version=version)
        p['observations'][-1]['changes']=old
        definitions=dict(weaker=changes([(1,2),(3,4),(5,6)],2),narrow=changes([(1,2)],1),
            wider=changes([(1,2),(3,4),(5,6),(7,8)],1),relocate=changes([(7,8)],1),
            compound=changes([(7,8)],2),overlap=changes([(5,6),(7,8)],1),duplicate=old)
        p['allowed_actions']={k:dict(title=k,changes=v) for k,v in definitions.items()}
        from traffic_agent.action_execution import VERSION as EXECUTION_VERSION
        p['context']['action_execution']=dict(version=EXECUTION_VERSION,actions={k:dict(demand_factors=[1]) for k in definitions})
        p['context']['choice_observations']=ce.observations(p)
        return p

    def group(self,p,action):
        r=p['context']['choice_observations']['recent_comparisons']
        return next(g for g in r['groups'] if action in g['actions'])

    def design(self,p,action):
        relation=p['context']['choice_observations']['recent_comparisons']
        return next(d for d in relation['demand_designs'] if d['id']==self.group(p,action)['demand_design_id'])

    def test_direction_scope_and_strength_are_separate_from_measured_effect(self):
        p=self.prepared()
        cases=dict(weaker=('same','weaker'),narrow=('narrower','same'),wider=('broader','same'),
                   relocate=('relocated','same'),compound=('relocated','weaker'),overlap=('partly_changed','same'),
                   duplicate=('same','same'))
        for action,expected in cases.items():
            g=self.group(p,action)
            self.assertEqual((g['direction_relation'],g['level_relation']),expected)
            self.assertNotIn('predicted_effect',g);self.assertNotIn('score',g)
        self.assertIn('不能单独归因',self.group(p,'compound')['interpretation'])
        self.assertIn('不能分离其中单一路段',self.group(p,'relocate')['interpretation'])
        self.assertIn('不能当成另一个不同候选',self.group(p,'duplicate')['interpretation'])
        self.assertIn('仍需实跑',self.group(p,'weaker')['interpretation'])

    def test_actual_demand_pair_is_required_and_missing_is_not_false(self):
        p=self.prepared();execution=p['context']['action_execution']['actions']
        execution['compound']['demand_factors']=[1.2];execution['narrow'].pop('demand_factors')
        execution['weaker']['demand_factors']=[1,1.2]
        p['context']['choice_observations']=ce.observations(p)
        self.assertIs(self.group(p,'compound')['matching_demand'],False)
        self.assertIn('不能作同需求',self.group(p,'compound')['interpretation'])
        self.assertIsNone(self.group(p,'narrow')['matching_demand'])
        self.assertIn('仍未知',self.group(p,'narrow')['interpretation'])
        self.assertIs(self.group(p,'weaker')['matching_demand'],True)

    def test_latest_completed_reference_is_used_and_baseline_only_has_no_comparison(self):
        p=self.prepared();old=copy.deepcopy(p)
        newer=dict(p['observations'][-1],ref='job:trial_2',changes=changes([(7,8)],2),demand_factor=1.2)
        p['observations'].append(newer);p['context']['choice_observations']=ce.observations(p)
        r=p['context']['choice_observations']['recent_comparisons']
        self.assertEqual(r['reference_ref'],'job:trial_2');self.assertEqual(r['reference_demand_factor'],1.2)
        self.assertEqual(self.group(p,'compound')['level_relation'],'same')
        for version in (ce.V4_VERSION,ce.V5_VERSION):
            prior=copy.deepcopy(old);prior['context']['choice_basis_protocol']=version
            self.assertNotIn('recent_comparisons',ce.observations(prior))
        self.assertNotIn('recent_comparisons',payload(version=ce.VERSION)['context']['choice_observations'])
        self.assertNotIn('recent_comparisons',payload('oneway_target',trial=True,version=ce.VERSION)['context']['choice_observations'])

    def test_incomplete_or_mixed_parameters_do_not_invent_single_factor_attribution(self):
        p=self.prepared();p['observations'][-1]['changes']=[dict(parking=1)]
        p['context']['choice_observations']=ce.observations(p)
        self.assertEqual(self.group(p,'weaker')['direction_relation'],'unknown')
        self.assertIn('资料不足',self.group(p,'weaker')['interpretation'])
        old=cc.signature(changes([(1,2)],1)+changes([(3,4)],2))
        new=cc.signature(changes([(1,2)],2)+changes([(3,4)],1))
        self.assertEqual(cc.relations(old,new),('same','mixed'))
        self.assertIsNone(cc.signature(changes([(1,2),(1,2)],1)))

    def test_grouped_facts_preserve_all_actions_and_display_is_shared_with_report(self):
        p=self.prepared();p['allowed_actions']['another_narrow']=dict(title='another',changes=changes([(3,4)],1))
        p['context']['action_execution']['actions']['another_narrow']=dict(demand_factors=[1])
        before=copy.deepcopy(p);facts=ce.observations(p)
        self.assertEqual(p,before)
        p['context']['choice_observations']=facts
        groups=facts['recent_comparisons']['groups'];listed=[a for g in groups for a in g['actions']]
        self.assertEqual(set(listed),set(p['allowed_actions']));self.assertEqual(len(listed),len(set(listed)))
        self.assertEqual(self.group(p,'narrow')['actions'],['narrow','another_narrow'])
        entry=dict(input=p,status='accepted',action='compound',selection_basis=dict(alternative_action='weaker'))
        shown=cc.selected(entry);report='\n'.join(candidate_comparison_lines(entry))
        self.assertEqual(shown['reference_direction_count'],3)
        self.assertEqual(shown['chosen']['direction_count'],1)
        self.assertEqual(shown['alternative']['direction_count'],3)
        self.assertIn('1条治理方向',report)
        self.assertIn(shown['chosen']['interpretation'],report)
        self.assertIn(shown['alternative']['interpretation'],report)
        self.assertIn('由参数生成',report)
        self.assertEqual(candidate_comparison_lines(dict(entry,status='rejected')),[])

    def test_v6_adds_comparison_context_without_extra_model_output_and_tampering_is_detected(self):
        p=payload(trial=True,version=ce.V6_VERSION)
        from traffic_agent.planning_instructions import VERSION as INSTRUCTION_VERSION
        p['context']['instruction_protocol']=INSTRUCTION_VERSION
        p['observations'][-1]['changes']=changes([(1,2)],1)
        p['context']['choice_observations']=ce.observations(p)
        old=copy.deepcopy(p);old['context']['choice_basis_protocol']=ce.V5_VERSION
        old['context']['choice_observations']=ce.observations(old)
        self.assertEqual(decision_tool(p),decision_tool(old))
        self.assertIn('recent_comparisons',instructions_for(p))
        self.assertNotIn('recent_comparisons',instructions_for(old))
        ce.verify(dict(input=p,status='pending'))
        p['context']['choice_observations']['recent_comparisons']['groups'][0]['interpretation']='这一方案已经最优'
        with self.assertRaisesRegex(ValueError,'选点依据索引'):ce.verify(dict(input=p,status='pending'))

    def test_v7_separates_planned_demand_baseline_and_completed_candidate_results(self):
        p=self.prepared(ce.VERSION);new=self.group(p,'relocate');measured=self.group(p,'duplicate')
        d=self.design(p,'relocate');c=d['conditions'][0]
        self.assertEqual(d['planned_factors'],[1]);self.assertEqual(d['reference_factor'],1)
        self.assertIs(d['includes_reference_factor'],True)
        self.assertEqual(c['baseline_refs'],['job:baseline']);self.assertEqual(c['candidate_result_refs'],[])
        self.assertEqual(c['result_status'],'no_completed_result')
        self.assertIn('计划包含参照需求',d['summary'])
        self.assertIn('尚无该候选的已完成结果',d['summary'])
        self.assertEqual(self.design(p,'duplicate')['conditions'][0]['paired_metric_refs'],['job:trial'])
        self.assertIn('不代表收益或约束通过',self.design(p,'duplicate')['summary'])
        self.assertNotIn('matching_demand',new)
        self.assertEqual(self.group(p,'relocate')['demand_design_id'],self.group(p,'weaker')['demand_design_id'])
        self.assertNotEqual(self.group(p,'relocate')['demand_design_id'],measured['demand_design_id'])

    def test_v7_groups_full_demand_conditions_and_does_not_pool_other_jobs(self):
        p=self.prepared(ce.VERSION)
        p['allowed_actions']['second_relocate']=dict(changes=changes([(9,10)],1))
        p['context']['action_execution']['actions']['second_relocate']=dict(demand_factors=[1,1.2])
        # Keep job:trial as the latest comparison reference; other groups' prior
        # results cannot supply this candidate's baseline or completed trial.
        p['observations'][0:0] = [dict(ref='other:baseline',run='baseline',demand_factor=1.2,changes=[]),
            dict(ref='other:trial',run='trial',demand_factor=1.2,changes=changes([(9,10)],1),delta=dict(core={}))]
        p['context']['choice_observations']=ce.observations(p)
        a=self.group(p,'relocate');b=self.group(p,'second_relocate')
        self.assertIsNot(a,b);self.assertEqual(self.design(p,'second_relocate')['planned_factors'],[1,1.2])
        self.assertEqual(self.design(p,'second_relocate')['conditions'][1]['baseline_refs'],[])
        self.assertEqual(self.design(p,'second_relocate')['conditions'][1]['candidate_result_refs'],[])
        self.assertIn('本轮未提供同需求基准',self.design(p,'second_relocate')['summary'])

    def test_v7_known_different_demand_and_missing_parameters_remain_distinct(self):
        for factors,expected in (([1.2],False),([1.0000001],False),([],None),([True],None),([float('nan')],None),(None,None)):
            with self.subTest(factors=factors):
                p=self.prepared(ce.VERSION);p['context']['action_execution']['actions']['relocate']['demand_factors']=factors
                p['context']['choice_observations']=ce.observations(p)
                self.assertIs(self.design(p,'relocate')['includes_reference_factor'],expected)
                if factors==[1.0000001]:self.assertIn('1.0000001',self.design(p,'relocate')['summary'])
        p=self.prepared(ce.VERSION);p['allowed_actions']['relocate']['changes']=[dict(from_node=7,to_node=8)]
        p['context']['choice_observations']=ce.observations(p)
        g=self.design(p,'relocate');self.assertFalse(g['candidate_parameters_known'])
        self.assertEqual(g['conditions'][0]['result_status'],'candidate_parameters_unknown')
        self.assertIn('无法核对已有结果',g['summary'])

    def test_v7_completed_result_without_pair_is_not_promoted_to_paired_metrics(self):
        p=self.prepared(ce.VERSION)
        p['observations'].append(dict(ref='job:other',run='other',demand_factor=1,
            changes=changes([(7,8)],1),delta=None))
        p['context']['choice_observations']=ce.observations(p)
        c=self.design(p,'relocate')['conditions'][0]
        self.assertEqual(c['candidate_result_refs'],['job:other']);self.assertEqual(c['paired_metric_refs'],[])
        self.assertEqual(c['result_status'],'observed_without_pair')

    def test_v7_schema_unchanged_and_displayed_demand_definition_is_audited(self):
        from traffic_agent.planning_instructions import VERSION as INSTRUCTION_VERSION
        p=self.prepared(ce.VERSION);p['context']['instruction_protocol']=INSTRUCTION_VERSION
        old=copy.deepcopy(p);old['context']['choice_basis_protocol']=ce.V6_VERSION
        old['context']['choice_observations']=ce.observations(old)
        self.assertEqual(decision_tool(p),decision_tool(old))
        self.assertIn('demand_design',instructions_for(p));self.assertNotIn('demand_design',instructions_for(old))
        entry=dict(input=p,status='accepted',action='relocate',selection_basis=dict(alternative_action='duplicate'))
        summary=self.design(p,'relocate')['summary']
        self.assertIn(summary,'\n'.join(candidate_comparison_lines(entry)))
        ce.verify(dict(input=p,status='pending'))
        self.design(p,'relocate')['conditions'][0]['candidate_result_refs']=['future:result']
        with self.assertRaisesRegex(ValueError,'选点依据索引'):ce.verify(dict(input=p,status='pending'))


if __name__=='__main__':unittest.main()
