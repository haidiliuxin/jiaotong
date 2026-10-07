"""Local direction binding; no provider or native requests."""
import unittest
from traffic_agent.direction_claims import claims,match
from traffic_agent.feedback_planner import direction_claims_match_v3

SELECTED=dict(closed_direction=[3237,3236])


class DirectionClaimTests(unittest.TestCase):
    def test_parenthesized_reverse_has_its_own_subject(self):
        good=dict(summary='本轮选取3237→3236禁行方向（反向保留）作为单向试验。')
        self.assertFalse(direction_claims_match_v3(good,SELECTED))
        self.assertTrue(match(good,SELECTED))
        self.assertEqual([(c['direction'],c['role']) for c in claims(good,SELECTED)],
                         [([3237,3236],'closed'),([3236,3237],'retained')])

    def test_introductory_operation_does_not_bind_through_a_colon(self):
        for separator in ('：','\n'):
            good=dict(next_check='单向禁行后需核对'+separator+'反向3236→3237保留通行。')
            self.assertFalse(direction_claims_match_v3(good,SELECTED))
            self.assertTrue(match(good,SELECTED))
            self.assertEqual([c['direction'] for c in claims(good,SELECTED)],[[3236,3237]])

    def test_direct_reversals_still_fail_in_each_public_field(self):
        wrong=['3237→3236保留通行，反向禁行','3236→3237禁行','3237→3236单向通行',
               '保留3237→3236方向通行','3237→3236保留通行但反向另议',
               '3237→3236禁行方向（反向禁行）','单向禁行后需核对：3236→3237关闭',
               '关闭：3236→3237','保留方向：3237→3236']
        for text in wrong:
            for field in ('summary','reason','risk','next_check'):
                with self.subTest(text=text,field=field):self.assertFalse(match({field:text},SELECTED))
            self.assertFalse(match(dict(selection_basis=dict(working_hypothesis=text)),SELECTED))

    def test_reverse_objects_and_diversion_destinations_do_not_reopen_the_closed_edge(self):
        good=['关闭3237→3236，保留3236→3237','关闭3237→3236方向以实施单向通行',
              '禁行拥堵方向的3237→3236单向，保留反向通行。','禁行3237→3236保留相对畅通反向通行',
              '关闭：3237→3236，保留方向：3236→3237','关闭3237→3236后反向保留通行',
              '禁行高拥堵方向3237→3236可将车流导向保留方向，随后核对绕行代价。']
        for text in good:
            with self.subTest(text=text):self.assertTrue(match(dict(reason=text),SELECTED))
        wrong=good[-1]+'且3237→3236保留通行'
        self.assertFalse(match(dict(reason=wrong),SELECTED))

    def test_arrows_and_reversed_selected_pair_use_the_actual_action(self):
        self.assertTrue(match(dict(reason='关闭 3237 -> 3236，保留 3236 —> 3237'),SELECTED))
        self.assertFalse(match(dict(reason='关闭3237→3236，保留3236→3237'),dict(closed_direction=[3236,3237])))
        self.assertTrue(match(dict(reason='未提到明确方向'),SELECTED))


if __name__=='__main__':unittest.main()
