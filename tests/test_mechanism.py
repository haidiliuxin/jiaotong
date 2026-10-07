import unittest
from test_agent import link
from traffic_agent.mechanism import parking_response


class MechanismTests(unittest.TestCase):
    def test_capacity_response_is_not_mistaken_for_travel_benefit(self):
        before = [link(0, 1, 2, speed=9)]
        after = [dict(before[0], parking=2, adjusted_capacity_pcu_h=400)]
        change = [{'from_node': 1, 'to_node': 2, 'old_parking': 3, 'parking': 2}]
        result = parking_response(before, after, change)
        self.assertTrue(result['no_positive_joint_link_benefit'])
        self.assertIn('速度没有提高', result['roads'][0]['note'])
        self.assertEqual(result['roads'][0]['joint_saved_time_pcu_h'], 0)
        after[0]['flow_pcu_h'] = 900  # Even a faster road can have more total qL/v.
        after[0]['speed_kmh'] = 12
        result = parking_response(before, after, change)
        self.assertLess(result['roads'][0]['joint_saved_time_pcu_h'], 0)
        self.assertIn('容量与速度均上升', result['roads'][0]['note'])


if __name__ == '__main__':
    unittest.main()
