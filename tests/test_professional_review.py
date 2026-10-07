"""Paired local-harm facts must survive aggregate improvements and missing data."""
import copy
import unittest

from traffic_agent.professional_review import review
from traffic_agent.report import professional_impact_lines


def link(index, flow, speed):
    return dict(index=index, from_node=index, to_node=index+1, name=f'路段{index}',
                included=True, length_km=1.0, flow_pcu_h=flow, speed_kmh=speed)


def run(name, rows, changes=()):
    return dict(name=name, demand_factor=1, changes=list(changes), core_rows=rows,
                execution=dict(status='completed'),
                metrics=dict(core=dict(running_time_pcu_h=sum(r['flow_pcu_h']*r['length_km']/r['speed_kmh'] for r in rows))))


def state():
    base=run('baseline',[link(1,100,10),link(3,200,10)])
    candidate=run('parking_1',[link(1,100,5),link(3,200,100)],
                  [dict(from_node=1,to_node=2,parking=1)])
    return dict(id='synthetic',task=dict(scenario='parking',demand_factors=[1],repeat_baseline=False),
                status='completed',runs=[base,candidate],recommendation='parking_1')


class ProfessionalReviewTests(unittest.TestCase):
    def test_gross_local_loss_is_visible_despite_net_gain_and_eligibility(self):
        source=state();saved=copy.deepcopy(source)
        result=review(source)
        self.assertEqual(source,saved)
        row=result['policies'][0]
        self.assertEqual(row['status'],'measured')
        self.assertEqual(row['gross_added_pcu_h'],10)
        self.assertEqual(row['gross_saved_pcu_h'],18)
        self.assertEqual(row['net_change_pcu_h'],-8)
        self.assertEqual(row['worsened_link_count'],1)
        self.assertEqual(row['largest_increases'][0]['index'],1)
        self.assertEqual(result['evidence_scope']['field_validation'],'not_established_from_job_record')
        self.assertEqual(result['evidence_scope']['demand_levels'],[1])
        self.assertFalse(result['evidence_scope']['baseline_repeated'])
        self.assertEqual([c['id'] for c in result['next_checks']],
                         ['local_loss','demand_sensitivity','repeatability','field_validation'])
        report='\n'.join(professional_impact_lines(source))
        self.assertIn('10.00000',report)
        self.assertIn('18.00000',report)
        self.assertIn('净变化',report)
        self.assertIn('现场标定／独立验证未由本任务记录证明',report)

    def test_conflicting_baselines_and_missing_link_measurements_remain_unknown(self):
        source=state();source['runs'].append(run('baseline_repeat',[link(1,100,9),link(3,200,10)]))
        source['task']['repeat_baseline']=True
        source['repeatability']=dict(exact_match=True)
        result=review(source)
        row=result['policies'][0]
        self.assertEqual(row['status'],'unverified')
        self.assertIn('重复基准',row['gap'])
        self.assertEqual(result['next_checks'][0]['id'],'repair_pair')
        self.assertEqual(result['evidence_scope']['repeat_status'],'inconsistent_or_unverified')
        source=state();source['runs'][1]['core_rows'][0]['speed_kmh']=0
        row=review(source)['policies'][0]
        self.assertEqual(row['status'],'unverified')
        self.assertIsNone(row['gross_added_pcu_h'])

    def test_topology_change_and_incomplete_run_do_not_create_false_local_result(self):
        source=state();source['runs'][1]['core_rows'][0]['from_node']=99
        self.assertEqual(review(source)['policies'][0]['status'],'unverified')
        source=state();source['runs'][1]['execution']['status']='running'
        self.assertEqual(review(source)['policies'],[])


if __name__=='__main__':
    unittest.main()
