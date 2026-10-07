import json
import tempfile
import unittest
from pathlib import Path

from traffic_agent.hard_access import digest
from traffic_agent.turn_access import verify_transform
from traffic_agent.turn_graph import transform
from tests import test_turn_graph


class TurnAccessTests(unittest.TestCase):
    def fixture(self, folder, lost_origin_flow=0):
        raw, ft, _ = test_turn_graph.TurnGraphTests().fixture()
        raw['cache/NodeInZone.dat'] = b'2 1 2\n1 3\n'
        bans = [(1,2,1),(3,2,3),(4,2,4)]
        native, graph = transform(raw,'car',2,bans)
        reference = [50,50,100,0,50,0,0,0]
        physical = list(reference)
        physical[2] -= lost_origin_flow
        values = []
        turns = []
        for edge in graph['edges']:
            if edge['physical_index'] is not None:
                q = physical[edge['physical_index']]
            elif edge['turn']:
                q = 50 if edge['turn'] in ([1,2,3],[3,2,1]) else 0
                turns.append(dict(turn=edge['turn'],flow=q))
            else:
                q = 50-lost_origin_flow if edge['source_exit']==3 else 0
            values.append(q)
        def vector(v):
            return ''.join(f'{q}\n' for q in v).encode()
        def write(name,data):
            path = folder/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(data)
        def save(name,obj):
            write(name,json.dumps(obj).encode())
        for name,data in raw.items():
            write('original_inputs/'+name,data)
            write('reference_scenario/'+name,data)
        for name,data in native.items():
            write('scenario/'+name,data)
        output,expanded = vector(values),vector(physical)
        write('reference_scenario/cache/volume_car.dat',vector(reference))
        write('scenario/cache/volume_car.dat',output)
        write('expanded_volume.dat',expanded)
        save('graph_mapping.json',graph)
        save('turn_flows.json',turns)
        save('native_inputs.json',{name:digest(data) for name,data in native.items()})
        save('manifest.json',dict(schema_version=2,mode='car',via=2,no_uturn=True,base_turns=[],added_turns=[],
                                 input_hashes={name:digest(data) for name,data in raw.items()},
                                 native_output_sha256=digest(output),expanded_output_sha256=digest(expanded),
                                 reference_returncode=0,reference_output_sha256=digest(vector(reference))))

    def test_reference_accepts_preserved_assignment(self):
        with tempfile.TemporaryDirectory() as name:
            folder=Path(name)
            self.fixture(folder)
            evidence=verify_transform(folder)[-1]
            self.assertTrue(evidence['original_node_balances_preserved'])
            self.assertEqual(evidence['source_flow'],50)

    def test_source_loss_cannot_hide_behind_balanced_state_nodes(self):
        with tempfile.TemporaryDirectory() as name:
            folder=Path(name)
            # Both the source connector and its downstream physical edge lose
            # ten units: every new port still balances, but OD service changed.
            self.fixture(folder,lost_origin_flow=10)
            with self.assertRaisesRegex(ValueError,'原节点'):
                verify_transform(folder)


if __name__=='__main__':
    unittest.main()
