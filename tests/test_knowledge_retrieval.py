"""The local knowledge lane must remain bounded and separate from measured facts."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from traffic_agent.knowledge_retrieval import CATALOG, retrieve, validate_citations, verify


class KnowledgeRetrievalTests(unittest.TestCase):
    def test_initial_parking_query_does_not_receive_oneway_control_notes(self):
        request='环洲路停车拥堵治理，找到一个可用方案。最多3次仿真，需求系数：1，不复跑。'
        bundle=retrieve(request,'initial')
        self.assertTrue(bundle['hits'])
        self.assertFalse(any(hit['id'].startswith('oneway.') for hit in bundle['hits']))
        generic=retrieve('环洲路拥堵治理','initial')
        self.assertTrue(all(hit['id'] in {'contest.agent','assignment.fixed','metrics.boundary'}
                            for hit in generic['hits']))

    def test_domain_questions_retrieve_their_required_method_notes(self):
        cases=[
            ('manageL停车影响类别不能比例推算容量','parking','parking.categories'),
            ('速度如何汇总和过饱和里程V/C口径','parking','metrics.boundary'),
            ('小汽车OD需求敏感性和匹配基准','parking','demand.sensitivity'),
            ('单向禁行方向的公交保护与车型接入','oneway','oneway.vehicle1'),
            ('施工容量损失与治理收益的两个基准','construction','construction.gate'),
            ('公交优先如何核算乘客人次收益','transit','transit.gate'),
            ('信号配时相位冲突和逐秒排队证据','signal','signal.gate'),
            ('错峰必须保持时段总需求守恒','staggered','staggered.gate'),
        ]
        for query,scenario,expected in cases:
            with self.subTest(query=query):
                self.assertIn(expected,[hit['id'] for hit in retrieve(query,'initial',scenario)['hits']])

    def test_query_sensitive_source_bearing_results_are_bounded_and_replayable(self):
        parking = retrieve('环洲路停车影响类别和需求敏感性', 'parking', 'parking')
        oneway = retrieve('单向禁行方向与公交保护', 'oneway_target', 'oneway')
        self.assertEqual(parking, retrieve('环洲路停车影响类别和需求敏感性', 'parking', 'parking'))
        self.assertLessEqual(len(parking['hits']), 3)
        self.assertIn('parking.categories', [hit['id'] for hit in parking['hits']])
        self.assertIn('oneway.vehicle1', [hit['id'] for hit in oneway['hits']])
        self.assertNotEqual([hit['id'] for hit in parking['hits']], [hit['id'] for hit in oneway['hits']])
        self.assertTrue(all(hit['source'] and hit['text'] and hit['sha256'] for hit in parking['hits']))
        self.assertTrue(all('智能体实验/jobs/' not in hit['source'] for hit in parking['hits']+oneway['hits']))
        self.assertEqual(verify(parking, '环洲路停车影响类别和需求敏感性', 'parking', 'parking'), 'verified')

    def test_no_match_and_catalog_failure_are_explicit(self):
        self.assertEqual(retrieve('xyzzy', 'initial')['hits'], [])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bad.json'
            path.write_text(json.dumps([dict(id='bad',topics=['parking'],source='source',text='text'),
                                        dict(id='bad',topics=['parking'],source='source',text='text')]),encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '知识库'):
                retrieve('停车', 'parking', 'parking', catalog_path=path)

    def test_citation_must_belong_to_retrieved_set_and_tampering_fails(self):
        bundle=retrieve('停车影响类别', 'parking', 'parking')
        valid=bundle['hits'][0]['id']
        validate_citations({'reason':f'根据【K:{valid}】提出待验证思路'},bundle)
        with self.assertRaisesRegex(ValueError,'知识引用'):
            validate_citations({'reason':'根据【K:invented.source】确定收益'},bundle)
        changed=copy.deepcopy(bundle);changed['hits'][0]['text']='伪造效果'
        with self.assertRaisesRegex(ValueError,'知识检索'):
            verify(changed,'停车影响类别','parking','parking')

    def test_changed_catalog_never_claims_historical_ranking_revalidated(self):
        bundle=retrieve('停车影响类别','parking','parking')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'new.json'
            path.write_bytes(CATALOG.read_bytes()+b'\n')
            self.assertEqual(verify(bundle,'停车影响类别','parking','parking',catalog_path=path),'catalog_changed')


if __name__=='__main__':
    unittest.main()
