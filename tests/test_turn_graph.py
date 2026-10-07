import heapq
import unittest

from traffic_agent.turn_graph import transform


def shortest(edges, costs, start, finish):
    adjacency = {}
    for (u, v), cost in zip(edges, costs):
        adjacency.setdefault(u, []).append((v, cost))
    queue, best = [(0, start)], {start: 0}
    while queue:
        distance, node = heapq.heappop(queue)
        if node == finish:
            return distance
        if distance != best[node]:
            continue
        for neighbor, cost in adjacency.get(node, []):
            candidate = distance+cost
            if candidate < best.get(neighbor, float('inf')):
                best[neighbor] = candidate
                heapq.heappush(queue, (candidate, neighbor))
    return float('inf')


class TurnGraphTests(unittest.TestCase):
    def fixture(self):
        ft = [(1,2),(2,1),(2,3),(2,4),(3,2),(3,4),(4,2),(4,3)]
        costs = [10,10,20,15,20,15,15,15]
        raw = {'data/number.txt': b'8\n4\n9\n', 'cache/rnode.dat': b'1\n2\n3\n4\n',
               'cache/n_r.dat': b'4\n1\n3\n2\n2\n',
               'cache/tv.dat': b'2\n1\n3\n4\n2\n4\n2\n3\n',
               'cache/FT.dat': ''.join(f'{u} {v}\n' for u,v in ft).encode(),
               'cache/Rwm_car.dat': ''.join(f'{v}\n' for v in costs).encode(),
               'cache/szn.dat': b'2\n1\n3\n', 'cache/NodeInZone.dat': b'2 1 2\n2 3 4\n',
               'cache/Assign_car.od': b'0\n100\n50\n0\n'}
        return raw, ft, costs

    def test_unrestricted_path_costs_and_zone_inputs_are_preserved(self):
        raw, ft, costs = self.fixture()
        changed, _ = transform(raw, 'car', 2, [])
        nft = [tuple(map(int,l.split())) for l in changed['cache/FT.dat'].splitlines()]
        nc = list(map(float,changed['cache/Rwm_car.dat'].split()))
        for start in (1,2,3,4):
            for finish in (1,3,4):
                self.assertEqual(shortest(ft,costs,start,finish),shortest(nft,nc,start,finish))
        for path in ('cache/Assign_car.od','cache/NodeInZone.dat','cache/szn.dat'):
            self.assertEqual(raw[path],changed[path])

    def test_banned_turn_cannot_escape_through_origin_terminal(self):
        raw, _, _ = self.fixture()
        changed, _ = transform(raw, 'car', 2, [(1,2,3)])
        ft = [tuple(map(int,l.split())) for l in changed['cache/FT.dat'].splitlines()]
        costs = list(map(float,changed['cache/Rwm_car.dat'].split()))
        self.assertEqual(shortest(ft,costs,1,3),40)
        self.assertEqual(shortest(ft,costs,2,3),20)
        self.assertFalse(any(v == 2 for u,v in ft))

    def test_centroid_and_disconnected_incoming_are_rejected(self):
        raw, _, _ = self.fixture()
        raw['cache/szn.dat'] = b'2\n2\n3\n'
        with self.assertRaises(ValueError):
            transform(raw,'car',2,[])
        raw['cache/szn.dat'] = b'2\n1\n3\n'
        with self.assertRaises(ValueError):
            transform(raw,'car',2,[(1,2,v) for v in (1,3,4)])


if __name__ == '__main__':
    unittest.main()
