"""Bounded wording checks and frozen operational questions, no paid calls."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from traffic_agent import question_wording as qw,hypothesis_contract as hc
from traffic_agent.llm_client import ModelError
from traffic_agent.dispatch import Dispatcher
import test_natural_plan as helpers


def h(text='至少一个已试停车措施能降低核心耗时',test='core_time_decrease',quantifier='any',evidence='本组同需求基准和治理配对',studies=None):
    return dict(hypothesis=text,evidence_needed=evidence,criterion=dict(test=test,studies=studies or [1],policies='parking',quantifier=quantifier))


def entries():
    return [dict(source_studies=[i],task=dict(scenario='parking',road_name=road,demand_factors=[1])) for i,road in [(1,'甲路'),(2,'乙路')]]


def bind(value):return hc.bind([value],entries(),{},wording_protocol=qw.VERSION)[0]


class QuestionWordingTests(unittest.TestCase):
    def test_generated_question_preserves_metric_scope_and_quantifier(self):
        result=bind(h());w=result['evaluation']['interpretation']
        self.assertEqual(result['hypothesis'],h()['hypothesis'])
        for text in ('第1组（甲路）','需求×1','至少一个','冻结核心范围总耗时','同需求无治理基准'):self.assertIn(text,w['question'])
        self.assertIn('不是任意文字的语义认证',w['boundary'])
        self.assertEqual(w['wording_check'],'no_explicit_conflict')

    def test_compound_condition_cannot_be_answered_by_benefit_alone(self):
        for text in ('核心耗时下降且周边不恶化','降低核心耗时并满足周边约束','核心收益改善，措施可推荐'):
            with self.subTest(text=text),self.assertRaisesRegex(ModelError,'不包含推荐资格'):bind(h(text))

    def test_pure_benefit_cannot_be_replaced_by_recommendation_eligibility(self):
        with self.assertRaisesRegex(ModelError,'替代核心收益'):bind(h(test='admissible'))
        bind(h('至少一个已试措施通过全部推荐约束',test='admissible'))

    def test_eligibility_condition_before_pass_uses_frozen_admissibility(self):
        row=h('至少一个停车候选在全部原定约束与控制响应检查中通过',test='admissible')
        self.assertIn('全部原定推荐约束',bind(row)['evaluation']['interpretation']['question'])
        with self.assertRaisesRegex(ModelError,'推荐资格替代控制响应'):
            hc.bind([row],entries(),{},wording_protocol=qw.V3_VERSION)
        with self.assertRaisesRegex(ModelError,'推荐资格替代控制响应'):
            bind(h('至少一个停车候选的控制响应检查通过',test='admissible'))

    def test_known_unavailable_claims_cannot_use_a_measurable_proxy(self):
        for text in ('停车措施减少排队','消除双向冲突','判定实际拥堵成因','建立收益上限','达到全局最优','证明零风险'):
            with self.subTest(text=text),self.assertRaisesRegex(ModelError,'批次指标'):bind(h(text))
            self.assertIn('不能直接判定',bind(h(text,test='unavailable'))['evaluation']['interpretation']['question'])

    def test_unavailable_evidence_is_not_required_for_measurable_question(self):
        with self.assertRaisesRegex(ModelError,'批次指标'):bind(h(evidence='新基准与治理后的排队长度'))

    def test_explicit_limits_are_not_mistaken_for_affirmed_claims(self):
        for suffix in ('，不能证明现实因果','；不代表零风险','，不以周边不恶化作为核心收益定义','，当前批次无法观测排队长度'):
            with self.subTest(suffix=suffix):bind(h(h()['hypothesis']+suffix,evidence='新配对指标，缺少逐秒轨迹'))

    def test_independent_judgment_notes_keep_the_original_question_without_adding_a_condition(self):
        suffixes=['且判定与是否通过推荐约束分开','本项判断与是否满足全部推荐约束应分别进行',
                  '核心耗时的判断与是否可推荐独立进行','是否通过推荐约束与核心耗时是否降低分别判定',
                  '是否降低核心耗时与是否满足邻区门槛分别判断']
        for suffix in suffixes:
            with self.subTest(suffix=suffix):
                original=h(h()['hypothesis']+'，'+suffix)
                result=bind(original);wording=result['evaluation']['interpretation']
                self.assertEqual(result['hypothesis'],original['hypothesis'])
                self.assertEqual(result['criterion'],original['criterion'])
                self.assertEqual(wording['wording_notes'],[dict(source='hypothesis',kind='separate_criteria',text=suffix)])
                self.assertNotIn('通过推荐约束',wording['question'])
                self.assertEqual(wording['version'],qw.VERSION)

    def test_separation_wording_cannot_hide_a_real_second_condition_or_embedded_assertion(self):
        texts=['降低核心耗时且满足推荐约束，两者分开判定',
               '降低核心耗时，判定与是否通过推荐约束分开，但同时必须通过全部约束',
               '降低核心耗时，且判定与是否通过推荐约束分开但必须可推荐',
               '降低核心耗时，是否通过推荐约束也必须成立',
               '降低核心耗时，判定与是否通过推荐约束不分开',
               '降低核心耗时，且已通过推荐约束并独立判定']
        for text in texts:
            with self.subTest(text=text),self.assertRaisesRegex(ModelError,'不包含推荐资格'):bind(h(text))

    def test_v1_keeps_its_original_rejection_and_does_not_gain_v2_notes(self):
        original=h('至少一个停车候选降低核心耗时，且判定与是否通过推荐约束分开')
        with self.assertRaisesRegex(ModelError,'不包含推荐资格'):
            hc.bind([original],entries(),{},wording_protocol=qw.LEGACY_VERSION)
        old=hc.bind([h()],entries(),{},wording_protocol=qw.LEGACY_VERSION)[0]
        self.assertEqual(old['evaluation']['interpretation']['version'],qw.LEGACY_VERSION)
        self.assertNotIn('wording_notes',old['evaluation']['interpretation'])
        with self.assertRaisesRegex(ModelError,'未知的问题口径'):
            hc.bind([original],entries(),{},wording_protocol='invented')

    def test_evidence_notes_are_separately_attributed_without_discarding_unavailable_requirements(self):
        suffix='本项检验与是否可推荐分别判断'
        result=bind(h(evidence='读取同需求配对，'+suffix))
        self.assertEqual(result['evaluation']['interpretation']['wording_notes'][0]['source'],'evidence_needed')
        with self.assertRaisesRegex(ModelError,'批次指标'):
            bind(h(evidence='读取排队长度，'+suffix))

    def test_declared_quantifier_is_not_weakened_or_strengthened(self):
        for text,q in [('所有已试措施都降低核心耗时','any'),('至少一个已试措施降低核心耗时','all')]:
            with self.subTest(text=text),self.assertRaisesRegex(ModelError,'更换量词'):bind(h(text,quantifier=q))
        bind(h('所有已试措施都降低核心耗时',quantifier='all'))

    def test_v5_catches_existential_prose_and_evidence_against_all(self):
        row=h('在甲路上至少存在一个可通过全部原定约束的停车治理方案',
              test='admissible',quantifier='all',
              evidence='两个停车候选均需通过本组全部原定约束与原生控制响应检查')
        # Frozen v4 receipts retain their original bounded check.
        hc.bind([row],entries(),{},wording_protocol=qw.V4_VERSION)
        with self.assertRaisesRegex(ModelError,'更换量词'):
            bind(row)
        row['criterion']['quantifier']='any'
        with self.assertRaisesRegex(ModelError,'所需证据中的量词'):
            bind(row)
        row['evidence_needed']='至少一个已试停车方案通过本组全部原定约束与原生控制响应检查'
        self.assertEqual(bind(row)['evaluation']['quantifier'],'any')

    def test_whole_network_or_different_road_cannot_inherit_core_results(self):
        for text in ('降低全网总耗时','整条道路的通行代价下降','乙路核心耗时下降'):
            with self.subTest(text=text),self.assertRaises(ModelError):bind(h(text))
        bind(h('乙路核心耗时下降',studies=[2]))

    def test_unmeasured_demand_is_not_silently_answered(self):
        with self.assertRaisesRegex(ModelError,'需求系数'):bind(h('需求1.2下核心耗时下降'))
        bind(h('需求系数1下核心耗时下降'))

    def test_neighbor_threshold_does_not_prove_zero_loss(self):
        with self.assertRaisesRegex(ModelError,'严格零代价'):bind(h('所有措施使邻区完全不恶化',test='neighbor_limits',quantifier='all'))
        bind(h('所有已试措施均满足邻区两项代价门槛',test='neighbor_limits',quantifier='all'))

    def test_baseline_and_component_comparators_are_not_interchangeable(self):
        with self.assertRaisesRegex(ModelError,'无治理基准'):bind(h('组合相比仅停车核心耗时更低'))
        row=h('组合比两个单项的核心耗时都低',test='core_advantage_over_parking')
        row['criterion']['policies']='combined'
        group=entries();group[0]['task']['scenario']='oneway'
        with self.assertRaisesRegex(ModelError,'两个单项'):hc.bind([row],group,{},wording_protocol=qw.VERSION)
        row['criterion']['test']='combination_core_advantage'
        result=hc.bind([row],group,{},wording_protocol=qw.VERSION)[0]
        self.assertIn('两个单项',result['evaluation']['interpretation']['question'])

    def test_original_protocol_keeps_its_record_and_question(self):
        old=hc.bind([h('核心耗时下降且周边不恶化')],entries(),{})[0]
        self.assertNotIn('interpretation',old['evaluation'])
        f=hc.finding(dict(old,id='H1'),{})
        self.assertEqual(f['question'],old['hypothesis']);self.assertNotIn('model_question',f)

    def test_finding_attributes_original_wording_separately(self):
        original=bind(h());f=hc.finding(dict(original,id='H1'),{})
        self.assertEqual(f['question'],original['evaluation']['interpretation']['question'])
        self.assertEqual(f['model_question'],original['hypothesis'])
        self.assertEqual(f['expected_judgment'],'unresolved')

    def test_explicit_conflict_retains_failed_attempt_without_native_or_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);settings=Mock(revision=Mock(return_value=1),credentials=Mock(return_value=({},'OFFLINE_ONLY')))
            manager=Dispatcher(root/'source',root/'evidence',settings);self.addCleanup(manager.close)
            client=Mock(request=Mock(return_value=helpers.model_reply([helpers.experiment(['parking'])],assessment=[h('核心耗时下降且周边不恶化')])))
            with patch('traffic_agent.dispatch.input_fingerprints',return_value={}),patch('traffic_agent.dispatch.client_for',return_value=client),patch('traffic_agent.natural_plan.context_for',return_value=helpers.CATALOG),patch('traffic_agent.dispatch.Agent') as agent:
                with self.assertRaisesRegex(ModelError,'事前判据冲突'):manager.preview(dict(request='长江村路比较停车措施，不复跑',matcher='model'))
                agent.assert_not_called();client.request.assert_called_once()
            record=json.loads(next((manager.root/'planning_attempts').glob('*.json')).read_text('utf-8'))
            self.assertEqual(record['status'],'validation_failed');self.assertEqual(record['usage']['tokens'],100)
            self.assertEqual(record['proposal']['assessment'][0]['hypothesis'],'核心耗时下降且周边不恶化')
            self.assertEqual(manager.tickets,{})


if __name__=='__main__':unittest.main()
