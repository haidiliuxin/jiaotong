"""Typed metric claims over synthetic observations; no provider/native calls."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from traffic_agent.conclusion_review import VERSION, V12_VERSION, assess_entry, contract
from traffic_agent.dispatch import Dispatcher
from traffic_agent.metric_assertions import catalog, parse
from test_conclusion_review import observation


def measured():
    parking = observation('j:S1_parking', new=0, net=0)
    parking['delta']['neighbor']['running_time_change_pct'] = .000014973412265639752
    control = observation('j:S2_oneway', new=1.03054, net=.70854, eligible=False)
    control['changes'] = [dict(kind='oneway', closed_direction=[1,2])]
    control['delta']['neighbor']['running_time_change_pct'] = 171.89852158658633
    combined = copy.deepcopy(control)
    combined.update(ref='j:S3_combined',run='S3_combined',changes=parking['changes']+control['changes'])
    return [parking,control,combined]


def trace(text, version=VERSION, observations=None):
    observations = observations or measured()
    context = dict(measured_conclusion=dict(version=version),programme_current_job='j')
    context['measured_conclusion'] = contract(observations,context)
    return dict(status='accepted',action='stop',job_id='j',ordinal=2,summary=text,
                reviewed_cost_refs=[r['ref'] for r in observations],evidence_refs=[r['ref'] for r in observations],
                input=dict(observations=observations,context=context))


class MetricAssertionTests(unittest.TestCase):
    def test_coordinated_subjects_expand_to_independent_typed_fields(self):
        claims = list(parse('停车方案邻区耗时增幅与毛新增过饱和均为0、通过门槛。'))
        self.assertEqual([(r['scope'],r['metric'],r['unit']) for r in claims],
                         [('neighbor','time_change_pct','%'),('neighbor','new_oversaturated_km','km')])
        value = trace('停车方案邻区耗时增幅与毛新增过饱和均为0、通过门槛。')
        before = copy.deepcopy(value)
        found = assess_entry(value)['issues']
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['code'],'measured_metric_assertion')
        self.assertEqual(found[0]['metric'],'neighbor_time_change_pct')
        self.assertEqual(found[0]['refs'],['j:S1_parking'])
        self.assertGreater(found[0]['observed_value'],0)
        self.assertEqual(value,before)

    def test_policy_and_scope_are_resolved_separately(self):
        for text,ref in [('单向方案邻区毛新增过饱和为0km','j:S2_oneway'),
                         ('组合方案邻区耗时变化为0%','j:S3_combined'),
                         ('S1_parking核心耗时变化与邻区耗时增幅均为0','j:S1_parking')]:
            with self.subTest(text=text):
                found = assess_entry(trace(text))['issues']
                self.assertTrue(found)
                self.assertEqual({r['refs'][0] for r in found},{ref})
        self.assertEqual(assess_entry(trace('停车方案邻区毛新增过饱和为0km'))['issues'],[])

    def test_exact_zero_is_not_rounded_zero_and_shared_units_are_checked(self):
        for text in ('停车方案邻区耗时增幅为0.000%','停车方案邻区耗时变化为零',
                     '停车方案邻区耗时增幅与毛新增过饱和均为0%'):
            self.assertTrue(assess_entry(trace(text))['issues'],text)
        for text in ('停车方案邻区耗时增幅近似为0.000%，仍有微小正增加。',
                     '停车方案邻区耗时增幅为0.00001497%。',
                     '单向方案邻区耗时变化为171.90%。',
                     '单向方案邻区毛新增过饱和为1.03054km。'):
            self.assertEqual(assess_entry(trace(text))['issues'],[],text)
        self.assertTrue(assess_entry(trace('单向方案邻区毛新增过饱和近似为0km'))['issues'])

    def test_negative_conditional_and_contrast_polarity(self):
        for text in ('不能说停车方案邻区耗时增幅与毛新增过饱和均为0。',
                     '若停车方案邻区耗时增幅为0，则再判断其他约束。',
                     '停车方案邻区耗时增幅不等于0。'):
            self.assertEqual(assess_entry(trace(text))['issues'],[],text)
        found = assess_entry(trace('不能说没有代价，但停车方案邻区耗时增幅为0。'))['issues']
        self.assertEqual(found[0]['metric'],'neighbor_time_change_pct')

    def test_interval_or_configuration_is_not_measured_equality(self):
        for text in ('停车方案邻区耗时增幅为0至0.01%。','设置停车方案邻区耗时增幅为0.5%。',
                     '停车方案邻区耗时增幅上限为0.5%。'):
            self.assertEqual(assess_entry(trace(text))['issues'],[],text)

    def test_full_decision_repair_is_bounded_and_preserves_evidence(self):
        from traffic_agent.feedback_planner import PlanningSession,verify_review_permissions
        from test_planning_response import response
        original=trace('停车方案邻区耗时增幅与毛新增过饱和均为0。')
        ctx=original['input']['context']
        ctx.update(programme_final_group=True,reserved_model_requests=0,
                   programme_evidence=dict(groups=[dict(id='study_1',job_id='j',execution_complete=True)],findings=[]))
        bad={k:original[k] for k in ('action','summary','evidence_refs','reviewed_cost_refs')}
        bad.update(reason='约定的全部对照已完成，结束本组研究。',risk='极小的邻区耗时增加仍属于代价。',
                   next_check='结论只适用于当前已测需求。')
        good=dict(bad,summary='停车方案邻区耗时增幅近似为0.000%，仍有微小正增加；邻区毛新增过饱和为0km。')
        for last,expected in ((good,'accepted'),(bad,'rejected')):
            with self.subTest(last=expected):
                client=Mock(request=Mock(side_effect=[response(bad),response(last)]))
                session=PlanningSession(client,'离线指标复核',initial_usage=dict(tokens=10),risk_review_protocol=None)
                result=session.decide('route','j',measured(),{},copy.deepcopy(ctx))
                self.assertEqual(result['status'],expected,result.get('validation'))
                self.assertEqual(client.request.call_count,2)
                first=result['review_correction']['first_attempt']
                self.assertEqual(first['validation']['code'],'measured_conclusion')
                self.assertEqual(first['conclusion_check']['issues'][0]['metric'],'neighbor_time_change_pct')
                self.assertEqual(result['input']['observations'],result['review_correction']['input']['observations'])
                self.assertEqual(session.budget()['used'],3)
                verify_review_permissions(result)

    def test_unknown_and_multiple_demands_do_not_inherit_a_different_value(self):
        values = measured()
        unknown = copy.deepcopy(values[0]);unknown.update(ref='j:other',run='other',demand_factor=.9)
        unknown['delta']['neighbor']['running_time_change_pct'] = None
        values.append(unknown)
        for text in ('停车方案邻区耗时增幅为0。','S9邻区耗时增幅为0。'):
            self.assertEqual(assess_entry(trace(text,observations=values))['issues'],[],text)
        for text in ('other邻区耗时增幅为0。','停车方案需求×0.9邻区耗时增幅为0。'):
            found=assess_entry(trace(text,observations=values))['issues']
            self.assertEqual(found[0]['code'],'unmeasured_metric_assertion')
            self.assertEqual(found[0]['refs'],['j:other'])
            self.assertIsNone(found[0]['observed_value'])
        self.assertTrue(assess_entry(trace('停车方案需求×1邻区耗时增幅为0。',observations=values))['issues'])

    def test_previous_protocol_does_not_gain_a_new_rejection(self):
        value = trace('停车方案邻区耗时增幅与毛新增过饱和均为0。',V12_VERSION)
        self.assertEqual(assess_entry(value)['issues'],[])
        self.assertNotIn('metric_assertions',value['input']['context']['measured_conclusion'])
        self.assertEqual(trace('实测仍有代价。')['input']['context']['measured_conclusion']['metric_assertions'],catalog())

    def test_old_completed_record_gets_read_only_conflict_without_rewriting_prose(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'dispatches').mkdir()
            old=trace('停车方案邻区耗时增幅与毛新增过饱和均为0。',V12_VERSION)
            record=dict(job_id='j',status='completed',planning_trace=[old],progress=[dict(job_id='j',status='completed',
                        review=dict(research_review=dict(reviewed=True)))])
            path=root/'dispatches/j.json';path.write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
            before=path.read_bytes()
            shown=Dispatcher(root,root,Mock()).listing()[0]
            self.assertEqual((shown['recorded_status'],shown['status']),('completed','incomplete'))
            review=shown['public_claim_review']
            self.assertEqual(review['status'],'needs_review')
            self.assertEqual(review['issues'][0]['refs'],['j:S1_parking'])
            self.assertEqual(shown['planning_trace'][0]['public_claim_review'],review)
            self.assertEqual((review['job_id'],review['ordinal']),('j',old['ordinal']))
            self.assertFalse(shown['progress'][0]['review']['research_review']['reviewed'])
            self.assertEqual(shown['planning_trace'][0]['summary'],old['summary'])
            self.assertEqual(path.read_bytes(),before)


if __name__ == '__main__': unittest.main()
