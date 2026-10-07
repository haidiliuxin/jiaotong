import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from review_experiments import warnings, reassess, offline_html
from traffic_agent.agent import compare
from traffic_agent.task import Task
from test_agent import link


class ReviewAnalysisTests(unittest.TestCase):
    def test_gross_overload_and_losses_cannot_be_hidden_by_gains(self):
        before=[link(0, 1, 2), link(1, 2, 3)]
        after=[dict(r) for r in before]
        before[0].update(vc=.8, flow_pcu_h=100, speed_kmh=10, length_km=1)
        after[0].update(vc=1.2, flow_pcu_h=200, speed_kmh=10, length_km=1)
        before[1].update(vc=1.3, flow_pcu_h=300, speed_kmh=10, length_km=1)
        after[1].update(vc=.9, flow_pcu_h=50, speed_kmh=10, length_km=1)
        result=warnings(before, after, [0, 1])
        self.assertEqual(result['new_network_overloaded_km'], 1)
        self.assertEqual(result['added_time_pcu_h'], 10)
        self.assertEqual(result['saved_time_pcu_h'], 25)
        self.assertEqual(result['net_time_pcu_h'], -15)

    def test_relaxed_numeric_limits_do_not_erase_model_response_failure(self):
        before=[link(0, 1, 2), link(1, 2, 3)]
        after=[dict(r, speed_kmh=30) for r in before]
        task=Task.from_dict({})
        c=compare(before, after, dict(core=[0], neighbor=[1], network=[0, 1]), task)
        self.assertTrue(c['eligible'])
        result=reassess(c, task, ['实际禁转未生效'])
        self.assertFalse(result['eligible'])
        self.assertEqual(result['violations'], ['实际禁转未生效'])
        self.assertTrue(c['eligible'])

    def test_offline_report_has_escaped_real_tables(self):
        output=offline_html('# 标题\n\n| 候选 | 结果 |\n| --- | ---: |\n| <script> | 1 |\n')
        self.assertIn('<table>', output)
        self.assertIn('&lt;script&gt;', output)
        self.assertNotIn('<script>', output)
