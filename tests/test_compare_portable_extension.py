import copy
import unittest
from scripts.compare_portable_extension import align, array_difference


class PortableComparisonTests(unittest.TestCase):
    def state(self):
        return dict(task={'max_runs':4,'demand_factors':[1.0]},runs=[dict(name='baseline',changes=[],demand_factor=1.0)])

    def test_extra_rows_are_not_hidden_by_zip(self):
        value=array_difference([1,2],[1,2,3])
        self.assertFalse(value['exact']);self.assertEqual(value['differing_rows'],1)

    def test_small_numeric_difference_is_not_rounded_away(self):
        value=array_difference([1,2],[1,2.00000001])
        self.assertFalse(value['exact']);self.assertGreater(value['max_paired_absolute_difference'],0)

    def test_version_change_allowed_but_control_change_rejected(self):
        left=self.state();right=copy.deepcopy(left)
        right['task']['recommendation_version']='tolerance-v2'
        self.assertEqual(len(align(left,right)),1)
        right['task']['neighbor_hops']=1
        with self.assertRaisesRegex(ValueError,'任务参数'):align(left,right)

    def test_policy_change_rejected(self):
        left=self.state();right=copy.deepcopy(left)
        right['runs'][0]['changes']=[{'kind':'oneway','closed_direction':[1,2]}]
        with self.assertRaisesRegex(ValueError,'实际措施'):align(left,right)


if __name__=='__main__':unittest.main()
