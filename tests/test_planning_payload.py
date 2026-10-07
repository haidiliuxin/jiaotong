"""Lossless wire projections, including costs and unknown data; no live calls."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from traffic_agent.planning_payload import LEGACY_VERSION,VERSION,MARKER,PRIMARY,at,canonical,expand,serialize,share,wire_stamp


class PlanningPayloadTests(unittest.TestCase):
    def payload(self):
        cost=dict(ref='j:bad',run='bad',core_time_change_pct=-80,neighbor_time_change_pct=172,
            neighbor_new_oversaturated_km=1.03054,network_new_oversaturated_km=None,
            violations=['核心改善但邻区代价超限；单向原生响应仍已生效。'*8],constraint_passed=False)
        local=dict(id='benefit',question='本组问题',status='not_supported',answer='保留负面结果与可追溯依据。'*30,
                   evidence_refs=['j:bad'])
        return dict(request='原始自然语言不变',observations=[dict(ref='j:bad',metrics=copy.deepcopy(cost))],
            allowed_actions=dict(stop=dict(title='结束研究')),context=dict(input_protocol=VERSION,programme_current_job='j',
                programme_evidence=dict(groups=[dict(job_id='j',id='study_1')],findings=[dict(local,id='study_1.benefit',question='甲组：本组问题')]),
                measured_conclusion=dict(costs=[cost],neighbor_limit=.1),
                research_evidence=dict(findings=[dict(local,measurements=[])]),
                completed=[dict(job_id='j',review=dict(conclusion_review=dict(costs=[dict(cost,
                    neighbor_new_links=[dict(from_node=1,to_node=2,vc=2)],neighbor_new_link_count=1)])))],
                programme_comparisons=[dict(core_time_decreased=True,control_passed=True,admissible=False)]))

    def test_only_secondary_copies_change_and_full_negative_data_roundtrips(self):
        p=self.payload();before=copy.deepcopy(p);wire=share(p)
        self.assertLess(len(canonical(wire)),len(canonical(p)))
        self.assertEqual(canonical(expand(wire)),canonical(p))
        self.assertEqual(p,before)
        for path in PRIMARY:self.assertEqual(at(wire,path),at(p,path))
        cost=wire['context']['completed'][0]['review']['conclusion_review']['costs'][0]
        self.assertIn(MARKER,cost)
        self.assertEqual(cost['neighbor_new_links'],p['context']['completed'][0]['review']['conclusion_review']['costs'][0]['neighbor_new_links'])
        self.assertEqual(wire['context']['research_evidence']['findings'][0]['id'],'benefit')
        self.assertEqual(expand(wire)['context']['measured_conclusion']['costs'][0]['network_new_oversaturated_km'],None)

    def test_changed_fact_is_never_replaced_by_other_groups_or_an_old_value(self):
        p=self.payload();p['context']['completed'][0]['review']['conclusion_review']['costs'][0]['constraint_passed']=True
        p['context']['research_evidence']['findings'][0]['answer']='本组的不同结果不能复用另一项。'
        wire=share(p)
        self.assertEqual(expand(wire),p)
        self.assertNotIn(MARKER,wire['context']['completed'][0]['review']['conclusion_review']['costs'][0])
        self.assertNotIn(MARKER,wire['context']['research_evidence']['findings'][0])
        p['context']['programme_evidence']['groups']=[{}]
        self.assertEqual(expand(share(p)),p)  # Missing group metadata disables aliasing.

    def test_legacy_and_reserved_user_fields_are_not_reinterpreted(self):
        p=self.payload();p['context'].pop('input_protocol');self.assertEqual(share(p),p)
        p['context']['input_protocol']=VERSION;p['observations'][0]['literal']={MARKER:'not an actual reference'}
        self.assertEqual(share(p),p)
        p['context']['input_protocol']='unknown'
        with self.assertRaisesRegex(ValueError,'未知'):share(p)

    def test_references_cannot_point_at_actions_outside_the_primary_record_or_chain(self):
        wire=share(self.payload());cost=wire['context']['completed'][0]['review']['conclusion_review']['costs'][0]
        cost[MARKER]='#/context/completed/0/review/conclusion_review/costs/0'
        with self.assertRaisesRegex(ValueError,'保留的原始'):expand(wire)
        wire=share(self.payload());wire['context']['measured_conclusion']['costs'][0]['nested']={MARKER:'#/observations/0'}
        with self.assertRaisesRegex(ValueError,'循环引用|另一替代项'):expand(wire)

    def test_actual_v2_content_preserves_every_wire_value_and_reduces_bytes(self):
        from traffic_agent.feedback_planner import wire_payload
        p=self.payload();p['request']='原话含 空格、\\n和“逗号, 冒号:”，不能因压缩文字而改变。'
        wire=wire_payload(p);text=serialize(wire)
        self.assertEqual(json.loads(text),wire)
        self.assertEqual(expand(json.loads(text)),expand(wire))
        self.assertEqual(json.loads(text)['request'],p['request'])
        self.assertLess(len(text.encode()),len(json.dumps(wire,ensure_ascii=False).encode()))
        self.assertEqual(wire_stamp(text)['bytes'],len(text.encode()))

    def test_v1_and_unversioned_content_keep_previous_request_bytes(self):
        from traffic_agent.feedback_planner import wire_payload
        for version in (None,LEGACY_VERSION):
            with self.subTest(version=version):
                p=self.payload();p['context']['input_protocol']=version
                wire=wire_payload(p)
                self.assertEqual(serialize(wire),json.dumps(wire,ensure_ascii=False))
                self.assertEqual(expand(share(p)),p)

    def test_v2_wire_metadata_must_match_its_frozen_protocol(self):
        wire=share(self.payload());wire['context']['input_protocol']=LEGACY_VERSION
        with self.assertRaisesRegex(ValueError,'冻结输入协议'):expand(wire)

    def test_existing_v1_programme_can_resume_without_migrating_its_protocol(self):
        from traffic_agent.feedback_planner import PlanningSession
        session=PlanningSession(Mock(),'原始请求');self.assertEqual(session.input_protocol,VERSION)
        agent=SimpleNamespace(job_id='j',state=dict(runs=[dict(name='baseline')],input_protocol=LEGACY_VERSION))
        session.bind_programme(dict(input_protocol=LEGACY_VERSION),[agent])
        self.assertEqual(session.input_protocol,LEGACY_VERSION)
        self.assertEqual(agent.state['input_protocol'],LEGACY_VERSION)
        with self.assertRaisesRegex(ValueError,'执行开始后'):
            session.bind_programme(dict(input_protocol=VERSION),[agent])
        agent.state['runs']=[];session.bind_programme(dict(input_protocol=VERSION),[agent])
        self.assertEqual(agent.state['input_protocol'],VERSION)


if __name__=='__main__':unittest.main()
