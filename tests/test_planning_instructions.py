import copy
import json
import unittest
from unittest.mock import Mock

from traffic_agent.choice_evidence import VERSION as CHOICE_VERSION,observations as choice_observations
from traffic_agent.conclusion_review import contract
from traffic_agent.feedback_planner import (INSTRUCTIONS,PlanningSession,decision_tool,
    instructions_for,review_permissions,verify_review_permissions,verify_input_wire,wire_payload)
from traffic_agent.planning_instructions import VERSION,selected_blocks
from test_feedback_planner import reply


class PlanningInstructionTests(unittest.TestCase):
    def target_payload(self):
        p=dict(phase='oneway_target',request='原始交通问题',evidence_refs=['j:baseline'],
               model_budget=dict(remaining=2),observations=[dict(ref='j:baseline',run='baseline',changes=[])],
               allowed_actions={'oneway_1_2':dict(closed_direction=[1,2])},
               context=dict(instruction_protocol=VERSION,choice_basis_protocol=CHOICE_VERSION,
                            measured_conclusion=contract([],{}),initial_hypotheses=[],
                            programme_final_group=True))
        p['context']['review_permissions']=review_permissions(p['allowed_actions'],p['context'])
        p['context']['choice_observations']=choice_observations(p)
        return p

    def test_target_prompt_is_selected_by_permissions_and_keeps_frozen_evidence(self):
        p=self.target_payload();before=copy.deepcopy(p)
        blocks=selected_blocks(p,decision_tool(p));text=instructions_for(p)
        self.assertIn('oneway_target',blocks);self.assertIn('selection_current',blocks)
        for unused in ('parking','hypotheses','programme','handoff','cost_refs'):
            self.assertNotIn(unused,blocks)
        self.assertLess(len(text),len(INSTRUCTIONS)//2)
        for required in ('closed_direction','反向保留','未执行','阈值','独立研究组','有限JSON'):
            self.assertIn(required,text)
        self.assertEqual(p,before)
        wire=wire_payload(p)
        self.assertEqual(wire['observations'],p['observations'])
        self.assertEqual(wire['allowed_actions'],p['allowed_actions'])
        self.assertEqual(wire['context']['measured_conclusion'],p['context']['measured_conclusion'])

    def test_correction_block_requires_actual_feedback_not_final_group_flag(self):
        p=self.target_payload()
        self.assertNotIn('correction',selected_blocks(p,decision_tool(p)))
        p['review_feedback']=dict(retained_action='oneway_1_2')
        self.assertIn('correction',selected_blocks(p,decision_tool(p)))
        self.assertNotIn('programme',selected_blocks(p,decision_tool(p)))

    def test_actual_stage_prompt_hash_is_audited(self):
        client=Mock(request=Mock(side_effect=lambda _i,inputs,_t,_c:reply('stop',['j:baseline'],json.loads(inputs[0]['content']))))
        entry=PlanningSession(client,'原始请求').decide('route','j',[dict(ref='j:baseline')],{})
        self.assertEqual(entry['status'],'accepted')
        self.assertEqual(entry['input']['context']['instruction_protocol'],VERSION)
        from traffic_agent.planning_payload import serialize,wire_stamp
        sent=client.request.call_args.args[1][0]['content']
        self.assertEqual(sent,serialize(wire_payload(entry['input'])))
        self.assertEqual(entry['input_wire'],wire_stamp(sent))
        verify_review_permissions(entry)
        verify_input_wire(entry)
        tampered=copy.deepcopy(entry);tampered['input_wire']['bytes']+=1
        with self.assertRaisesRegex(ValueError,'实际输入'):verify_input_wire(tampered)
        entry['instructions_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'阶段提示'):verify_review_permissions(entry)


if __name__=='__main__':unittest.main()
