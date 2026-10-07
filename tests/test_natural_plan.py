import json
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from traffic_agent.natural_plan import compose, limits
from traffic_agent.dispatch import Dispatcher
from traffic_agent.task import Task
from traffic_agent.llm_client import ModelError
from traffic_agent.planning_scope import PrerequisiteError

CATALOG = dict(matched_roads=["长江村路"], road_names={"长江村路", "红景路"}, targets={
    "L_3237_3236": dict(kind="oneway", nodes=[3237, 3236], road="长江村路"),
    "T_1_2_3": dict(kind="turn", nodes=[1, 2, 3], road="长江村路", topology_checked=True)})


def experiment(strategies=None, target="auto", road="", study="standard", demand_profile="base"):
    return dict(strategies=["parking", "oneway"] if strategies is None else strategies, study=study, demand_profile=demand_profile, target_ref=target, road_name=road,
                rationale="比较停车、交通组织单项及组合，检查邻区影响")


def model_reply(experiments, **kwargs):
    proposed = dict(goal="缓解所述道路拥堵", reasoning="用组合对照检验不同机制", experiments=experiments,
                    assessment=[dict(hypothesis="停车干扰可能影响通行成因，仍需独立因果证据", evidence_needed="当前批次对照不能证明现实成因",
                        criterion=dict(test='unavailable',studies=[1],policies='all',quantifier='all'))],
                    alternatives=[dict(approach="停车与交通组织", tradeoff="完整对照，保留负结果", decision="selected")],
                    missing_information=[], unsupported_requests=[])
    proposed.update(kwargs)
    if proposed['unsupported_requests']:
        decision=dict(intent='unsupported',capabilities=proposed['unsupported_requests'])
    elif proposed['missing_information']:
        decision=dict(intent='needs_input',questions=[x if isinstance(x,dict) else
            dict(need='unresolved_location',source_quote='学校门口',question=x) for x in proposed['missing_information']])
    else:
        decision=dict(intent='execute',assessment=proposed['assessment'],alternatives=proposed['alternatives'],experiments=proposed['experiments'])
    wire=dict(goal=proposed['goal'],reasoning=proposed['reasoning'],decision=decision)
    return dict(calls=[dict(name="compose_traffic_plan", arguments=json.dumps(wire))], metadata=dict(tokens=100))


class NaturalPlanTests(unittest.TestCase):
    def test_invalid_knowledge_catalog_stops_before_paid_model_request(self):
        client=Mock()
        with patch('traffic_agent.knowledge_retrieval.read_catalog',side_effect=ValueError('知识库无效')):
            with self.assertRaisesRegex(ValueError,'知识库'):
                compose('长江村路停车治理',Path('source'),client,CATALOG)
        client.request.assert_not_called()

    def test_initial_model_receives_bounded_curated_sources(self):
        result=self.compose([experiment(['parking'])],text='长江村路停车治理，请比较候选')
        sent=json.loads(self.client.request.call_args.args[1][0]['content'])
        self.assertEqual(sent['knowledge_retrieval'],result['knowledge_retrieval'])
        self.assertLessEqual(len(sent['knowledge_retrieval']['hits']),3)
        self.assertTrue(all(hit['source'] for hit in sent['knowledge_retrieval']['hits']))
        from traffic_agent.knowledge_retrieval import VERSION
        self.assertEqual(result['knowledge_retrieval_protocol'],VERSION)

    def test_initial_model_cannot_cite_unretrieved_knowledge(self):
        self.client=Mock(request=Mock(return_value=model_reply([experiment(['parking'])],
            reasoning='根据【K:invented.source】必然改善')))
        with self.assertRaisesRegex(ModelError,'知识引用'):
            compose('长江村路停车治理',Path('source'),self.client,CATALOG)

    def test_requested_research_depth_changes_real_task_budget(self):
        result=self.compose([experiment(['parking'])],text='长江村路很堵，请比较至少两个停车备选方案')
        self.assertEqual(result['research_depth']['minimum_candidates'],2)
        self.assertEqual(result['experiments'][0]['task']['comparison_policies'],2)
        self.assertEqual(result['experiments'][0]['minimum_runs'],4)
        self.assertIn('research_depth',json.loads(self.client.request.call_args.args[1][0]['content']))
        with self.assertRaisesRegex(PrerequisiteError,'至少两个需求'):
            self.compose([experiment(['parking'])],text='长江村路停车治理，请检验需求变化下是否仍有效')
        result=self.compose([experiment(['parking'],demand_profile='variation')],
                            text='长江村路停车治理，请检验需求变化下是否仍有效')
        self.assertEqual(result['experiments'][0]['task']['demand_factors'],[0.9,1.0,1.1])

    def test_parking_alternatives_are_not_satisfied_by_oneway_cells(self):
        request='长江村路很堵，比较至少两个停车备选方案，再评估单向组织'
        with self.assertRaisesRegex(PrerequisiteError,'停车备选'):
            self.compose([experiment(['parking','oneway'])],text=request)
        result=self.compose([experiment(['parking']),experiment(['parking','oneway'])],text=request)
        parking=next(e for e in result['experiments'] if e['task']['scenario']=='parking')
        self.assertEqual(result['research_depth']['candidate_domain'],'parking')
        self.assertEqual(parking['task']['comparison_policies'],2)
        self.assertEqual(parking['minimum_runs'],4)

    def test_identical_parking_proposals_compile_to_measured_candidates(self):
        request='长江村路停车拥堵。请至少比较两个不同停车备选方案，并检验单向组织及其组合的独立效果。最多7次仿真，不复跑基准；保护邻区。'
        result=self.compose([experiment(['parking']),experiment(['parking']),experiment(['parking','oneway'])],text=request)
        self.assertEqual(result['status'],'ready')
        self.assertEqual([e['task']['scenario'] for e in result['experiments']],['parking','oneway'])
        self.assertEqual(result['experiments'][0]['source_studies'],[1,2])
        self.assertEqual(result['compilation']['actions'][0]['kind'],'candidate_parking')
        self.assertEqual(result['experiments'][0]['task']['comparison_policies'],2)
        self.assertEqual([e['minimum_runs'] for e in result['experiments']],[3,4])

    def test_explicit_separate_measurements_block_missing_plan_criterion(self):
        request=('独立研究长江村路的停车治理、单向组织及两者组合。'
                 '分别检验原生禁行控制是否生效、冻结核心耗时是否下降、邻区代价是否在原定门槛内。')
        with self.assertRaisesRegex(PrerequisiteError,'原生控制响应'):
            self.compose([experiment()],text=request)
        sent=json.loads(self.client.request.call_args.args[1][0]['content'])
        self.assertEqual(sent['request'],request)
        self.assertEqual(sent['required_criteria']['tests'],
                         ['control_response','core_time_decrease','neighbor_limits'])

    def test_placeholder_is_model_failure_and_not_a_user_information_request(self):
        # Reproduce the observed placeholder reply locally; no paid retry.
        with tempfile.TemporaryDirectory() as tmp:
            settings=Mock(revision=Mock(return_value=1),credentials=Mock(return_value=({},'fake')))
            manager=Dispatcher(Path(tmp),Path(tmp),settings)
            client=Mock(request=Mock(return_value=model_reply([],goal='test',reasoning='test',missing_information=['test'])))
            with patch('traffic_agent.dispatch.input_fingerprints',return_value={}), \
                    patch('traffic_agent.dispatch.client_for',return_value=client), \
                    patch('traffic_agent.natural_plan.context_for',return_value=CATALOG), \
                    patch('traffic_agent.dispatch.Agent') as agent:
                with self.assertRaisesRegex(ModelError,'占位内容'):
                    manager.preview(dict(request='比较不同区域治理，最多3次仿真，不复跑',matcher='model'))
                client.request.assert_called_once()
                agent.assert_not_called()
            record=json.loads(next((Path(tmp)/'planning_attempts').glob('*.json')).read_text('utf-8'))
            self.assertEqual(record['status'],'validation_failed')
            self.assertEqual(record['usage']['tokens'],100)
            self.assertEqual(manager.tickets,{})

    def test_placeholder_detection_does_not_reject_legitimate_test_word_or_missing_direction(self):
        result=self.compose([experiment(['parking'])],goal='测试停车治理',reasoning='测试不同强度能否改善道路')
        self.assertEqual(result['status'],'ready')
        result=self.compose([],text='长江村路东向准备改为单向，请先核对方向。',missing_information=[
            dict(need='unresolved_location',source_quote='长江村路东向准备改为单向',question='请说明东向对应的通行方向。')])
        self.assertEqual(result['status'],'needs_input')
        with self.assertRaisesRegex(ModelError,'未提出实验'):
            self.compose([])

    def compose(self, experiments, text="长江村路很堵，请自动比较治理策略", catalog=None, **kwargs):
        self.client = Mock(request=Mock(return_value=model_reply(experiments, **kwargs)))
        return compose(text, Path("source"), self.client, catalog or CATALOG)

    def test_general_congestion_can_compose_without_manual_scenario_or_nodes(self):
        result = self.compose([experiment()])
        self.assertEqual(result['version'],'natural-strategy-plan-v11')
        self.assertEqual(result["status"], "ready")
        task = result["experiments"][0]["task"]
        self.assertEqual(task["scenario"], "oneway")
        self.assertEqual(task["links"], [])
        self.assertEqual(task["road_name"], "长江村路")
        self.assertEqual(result["experiments"][0]["runs"], 5)
        self.client.request.assert_called_once()
        sent = json.loads(self.client.request.call_args.args[1][0]["content"])
        self.assertIn("targets", sent)
        self.assertEqual(sent['hypothesis_policy_contract']['families']['traffic_control']['members'],['traffic_control'])
        self.assertEqual(sent['hypothesis_policy_contract']['families']['control_and_combined']['members'],['traffic_control','combined'])
        self.assertNotIn("historical_results", sent)

    def test_two_combinations_are_separate_baselines_with_total_budget(self):
        result = self.compose([experiment(), experiment(["parking", "turn"], "T_1_2_3")], text="长江村路，比较多种策略，最多10次仿真")
        self.assertEqual(result["allocated_runs"], 10)
        self.assertEqual([x["task"]["scenario"] for x in result["experiments"]], ["oneway", "circulation"])
        self.assertEqual(result["experiments"][1]["task"]["turn"], [1, 2, 3])
        self.assertIn("跨组不混算", result["assumptions"][0])
        self.assertEqual(result['model_budget']['minimum_required'], 4)

    def test_oneway_selection_reserves_request_before_native_launch(self):
        result = self.compose([experiment()])
        self.assertEqual(result['model_budget']['minimum_required'], 3)
        self.assertIn('模型依据新基准', result['experiments'][0]['design']['stages'][1]['title'])
        result = self.compose([experiment(), experiment(['parking','turn'],'T_1_2_3'), experiment(['parking'])],
                              text='长江村路比较多种策略，最多15次仿真')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['model_budget']['minimum_required'], 6)
        self.assertEqual(len(result['experiments']), 3)

    def test_parking_first_can_review_and_route_without_reordering_or_dropping_groups(self):
        result = self.compose([experiment(['parking']), experiment(), experiment(['parking','turn'],'T_1_2_3')],
                              text='长江村路比较多种策略，最多15次仿真')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['model_budget']['minimum_required'],6)
        self.assertEqual([x['task']['scenario'] for x in result['experiments']],['parking','oneway','circulation'])

    def test_hidden_form_overrides_do_not_enter_model_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Mock()
            manager = Dispatcher(Path(tmp), Path(tmp), settings)
            with self.assertRaisesRegex(ValueError, "仅接收自然语言"):
                manager.preview(dict(matcher="model", request="拥堵", overrides={"max_runs": 30}))
            settings.credentials.assert_not_called()

    def test_busy_programme_never_spends_another_model_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Mock()
            manager = Dispatcher(Path(tmp), Path(tmp), settings)
            manager.active = Mock(is_alive=Mock(return_value=True))
            with self.assertRaisesRegex(ValueError, "未请求模型"):
                manager.preview(dict(matcher="model", request="换个策略"))
            settings.credentials.assert_not_called()

    def test_budget_is_grounded_and_never_silently_increased(self):
        result = self.compose([experiment()], text="长江村路，最多3次仿真")
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["experiments"], [])
        result = self.compose([experiment(["parking"])], text="长江村路，预算：2；不复跑")
        self.assertEqual(result["allocated_runs"], 2)
        with self.assertRaises(ValueError): limits("最多31次仿真")
        with self.assertRaises(ValueError): limits("预算：3；不超过5次仿真")
        self.assertEqual(limits("最多十次仿真，不要复跑")["total_runs"], 10)
        self.assertFalse(limits("最多十次仿真，不要复跑")["repeat_baseline"])
        self.assertEqual(limits("预算控制在二十五次")["total_runs"], 25)
        with self.assertRaises(ValueError): limits("最多一百次仿真")

    def test_explicit_total_simulation_count_does_not_fall_back_to_default(self):
        from traffic_agent.natural_plan import BUDGET_PARSE,LEGACY_BUDGET_PARSE
        for text in ('总计3次仿真','总共三场模拟','合计：3轮实验'):
            with self.subTest(text=text):
                current=limits(text);self.assertEqual(current['total_runs'],3)
                self.assertEqual(current['budget_parse_protocol'],BUDGET_PARSE)
                previous=limits(text,budget_protocol=LEGACY_BUDGET_PARSE)
                self.assertEqual(previous['total_runs'],15);self.assertNotIn('budget_parse_protocol',previous)
        self.assertEqual(limits('上周总计3场事故')['total_runs'],15)
        self.assertEqual(limits('总计3次仿真，最多3次')['total_runs'],3)
        for text in ('总计3次仿真，最多4次','合计31次模拟'):
            with self.subTest(text=text),self.assertRaises(ValueError):limits(text)

    def test_total_budget_parser_is_frozen_in_initial_cost_audit(self):
        from traffic_agent.natural_plan import BUDGET_PARSE,LEGACY_BUDGET_PARSE
        from traffic_agent.planning_budget import initial_costs,verify_initial_costs
        request='总计3次仿真，不复跑'
        tasks=[Task(request=request,max_runs=3,demand_factors=[1.0],repeat_baseline=False).as_dict()]
        entries=[dict(study='standard',title='本次停车研究',target_ref='auto')]
        for version in (LEGACY_BUDGET_PARSE,BUDGET_PARSE):
            check=initial_costs(tasks,entries,limits(request,budget_protocol=version),1,request)
            verify_initial_costs(check,request)
            self.assertEqual(check['native']['limit'],15 if version==LEGACY_BUDGET_PARSE else 3)
        bad=copy.deepcopy(check);bad.pop('budget_parse_protocol')
        with self.assertRaisesRegex(ValueError,'成本'):verify_initial_costs(bad,request)

    def test_unsupported_and_missing_do_not_silently_fall_back(self):
        r = self.compose([],text='请优化长江村路信号配时',unsupported_requests=["signal"])
        self.assertEqual(r["status"], "needs_validation")
        self.assertNotIn("task", r)
        with self.assertRaisesRegex(PrerequisiteError,'原问题没有要求'):
            self.compose([],unsupported_requests=['transit'])
        r = self.compose([],text='学校门口停车干扰很重，想治理这里',catalog=dict(CATALOG,matched_roads=[]), missing_information=[
            dict(need='unresolved_location',source_quote='学校门口停车干扰很重',question='请提供学校门口对应的道路')])
        self.assertEqual(r["status"], "needs_input")
        with self.assertRaisesRegex(ValueError, "合规策略编排"):
            self.compose([], unsupported_requests=["本次不执行治理"])

    def test_hallucinated_targets_triples_duplicates_and_schema_rejected(self):
        for experiments in ([experiment(["parking", "turn"], "T_9_8_7")],
                            [experiment(["parking", "oneway", "turn"])],
                            [experiment(), experiment()], [experiment(["signal"])],
                            [dict(experiment(), execute_shell="bad")]):
            with self.subTest(experiments=experiments), self.assertRaises(ValueError):
                self.compose(experiments)

    def test_explicit_targets_and_road_cannot_be_ignored(self):
        with self.assertRaisesRegex(ValueError, "指定关闭方向"):
            self.compose([experiment()], text="长江村路；方向：3237→3236")
        with self.assertRaisesRegex(ValueError, "未安排"):
            self.compose([experiment(["parking"])], text="长江村路；方向：3237→3236")
        with self.assertRaisesRegex(ValueError, "道路名称"):
            self.compose([experiment(road="红景路")])
        r = self.compose([experiment(target="L_3237_3236")], text="长江村路；方向：3237→3236")
        self.assertEqual(r["task"]["links"], [[3237, 3236]])

    def test_robust_search_and_backtracking_have_real_parameters_and_reserves(self):
        r = self.compose([experiment(["parking"], study="robust", demand_profile="variation")])
        t = r["task"]
        self.assertEqual(t["search_mode"], "robust")
        self.assertTrue(t["backtrack_on_singleton_failure"])
        self.assertEqual(t["demand_factors"], [0.9, 1.0, 1.1])
        self.assertEqual(t["max_runs"], 13)
        self.assertEqual(r["experiments"][0]["feedback_reserve"], 6)
        self.assertEqual(len(r["experiments"][0]["design"]["stages"]), 6)
        self.assertEqual(r["unallocated_runs"], 2)

    def test_redundant_diagnosis_does_not_take_parking_feedback_budget(self):
        result=self.compose([experiment([],study='diagnosis'),experiment(['parking'])],text='长江村路停车研究，最多3次仿真，不复跑')
        self.assertEqual(len(result['experiments']),1)
        self.assertEqual(result['task']['scenario'],'parking')
        self.assertEqual(result['task']['max_runs'],3)
        self.assertEqual(result['experiments'][0]['feedback_reserve'],1)
        self.assertTrue(result['normalization_notes'])
        # Vehicle1 and ordinary assignment baselines are never merged.
        result=self.compose([experiment([],study='diagnosis'),experiment()],text='长江村路，最多8次仿真，不复跑')
        self.assertEqual(len(result['experiments']),2)

    def test_spillover_and_demand_stress_are_different_executions(self):
        spill = self.compose([experiment(["parking"], study="spillover")])["task"]
        self.assertEqual(spill["neighbor_hops"], 3)
        self.assertEqual(spill["max_new_network_oversaturated_km"], 0)
        self.assertEqual(spill["max_feedback"], 3)
        stress = self.compose([experiment(["parking"], study="demand_stress", demand_profile="surge")])["task"]
        self.assertEqual(stress["demand_factors"], [1.0, 1.2])
        self.assertEqual(stress["search_mode"], "standard")

    def test_no_sensitivity_and_diagnostic_only_cannot_be_overruled(self):
        with self.assertRaisesRegex(ValueError, "不做敏感性"):
            self.compose([experiment(["parking"], study="robust", demand_profile="surge")], text="长江村路，不做敏感性")
        with self.assertRaisesRegex(ValueError, "只要求诊断"):
            self.compose([experiment()], text="长江村路，只做诊断，不要治理")
        r = self.compose([experiment([], study="diagnosis")], text="长江村路，只做诊断，不复跑，最多2次")
        self.assertEqual(r["task"]["scenario"], "diagnosis")
        self.assertEqual(r["allocated_runs"], 1)

    def test_multi_road_coverage_and_total_reserve(self):
        cat = dict(CATALOG, matched_roads=["长江村路", "红景路"])
        r = self.compose([experiment([], road="长江村路", study="diagnosis"), experiment([], road="红景路", study="diagnosis")],
                         text="长江村路和红景路，只做诊断，不复跑，最多2次", catalog=cat)
        self.assertEqual(r["allocated_runs"], 2)
        with self.assertRaisesRegex(ValueError, "漏掉"):
            self.compose([experiment([], road="长江村路", study="diagnosis")], text="长江村路和红景路，只做诊断", catalog=cat)

    def test_schema_and_budget_stop_before_execution(self):
        with self.assertRaises(ValueError):
            self.compose([experiment(study="robust", demand_profile="surge")])
        with self.assertRaises(ValueError):
            self.compose([experiment()], assessment=[dict(hypothesis="x", evidence_needed="y", shell="not allowed")])
        r = self.compose([experiment(["parking"], study="robust", demand_profile="variation")], text="长江村路最多3次")
        self.assertEqual(r["status"], "needs_input")
        self.assertIn("至少需要7次", r["missing"][0])

    def test_strength_comparison_reserves_native_and_model_completion_cost(self):
        with self.assertRaisesRegex(ValueError,'不能替代'):
            self.compose([experiment()],text='长江村路比较停车治理强度和单向组织')
        r=self.compose([experiment(['parking'])],text='长江村路比较停车治理强度，最多2次，不复跑')
        self.assertEqual(r['status'],'needs_input')
        self.assertIn('至少需要3次',r['missing'][0])
        r=self.compose([experiment(['parking'])],text='长江村路比较停车治理强度，最多3次，不复跑')
        self.assertEqual(r['status'],'ready')
        self.assertEqual(r['experiments'][0]['minimum_runs'],3)
        self.assertEqual(r['model_budget']['minimum_required'],4)
        r=self.compose([experiment(['parking'],study='demand_stress',demand_profile='surge')],text='长江村路比较停车治理强度，最多6次，不复跑')
        self.assertEqual(r['experiments'][0]['minimum_runs'],6)
        cat=dict(CATALOG,matched_roads=['长江村路','红景路'])
        r=self.compose([experiment(['parking'],road='长江村路'),experiment(['parking'],road='红景路')],
                       text='长江村路和红景路分别比较停车治理强度，不复跑，最多10次',catalog=cat)
        self.assertEqual(r['status'],'ready')
        self.assertEqual(r['model_budget']['minimum_required'],7)
        self.assertEqual(r['model_budget']['limit'],7)


    def test_region_comparison_native_model_reserves_and_named_road_scopes(self):
        cat=dict(CATALOG,matched_roads=[])
        with self.assertRaisesRegex(ValueError,'不能用一次体检'):
            self.compose([experiment([],study='diagnosis')],text='比较两个区域的停车治理',catalog=cat)
        for strength,budget,minimum,calls in [(False,3,3,4),(True,5,5,6)]:
            text='比较两个不同区域的停车治理'+('并比较治理强度' if strength else '')+f'，最多{budget}次，不复跑'
            r=self.compose([experiment(['parking'])],text=text,catalog=cat)
            self.assertEqual(r['status'],'ready')
            self.assertEqual(r['task']['comparison_regions'],2)
            self.assertEqual(r['experiments'][0]['minimum_runs'],minimum)
            self.assertEqual(r['model_budget']['minimum_required'],calls)
            self.assertIn('2个不同候选区',r['experiments'][0]['design']['completion'])
        r=self.compose([experiment(['parking'])],text='比较两个区域的停车治理，最多2次，不复跑',catalog=cat)
        self.assertEqual(r['status'],'needs_input')
        self.assertIn('至少需要3次',r['missing'][0])
        r=self.compose([experiment(['parking'])],text='比较三个区域并比较停车治理强度，最多10次，不复跑',catalog=cat)
        self.assertEqual(r['status'],'needs_input')
        self.assertIn('模型请求',r['missing'][0])
        cat=dict(CATALOG,matched_roads=['长江村路','红景路'])
        r=self.compose([experiment(['parking'],road='长江村路'),experiment(['parking'],road='红景路')],
                       text='比较两个区域长江村路和红景路的停车治理，最多4次，不复跑',catalog=cat)
        self.assertEqual(r['status'],'ready')
        self.assertEqual([e['task']['comparison_regions'] for e in r['experiments']],[1,1])
        self.assertEqual(r['model_budget']['minimum_required'],5)

    def test_identical_regional_parking_proposals_share_one_new_baseline(self):
        cat=dict(CATALOG,matched_roads=[])
        for areas,budget,calls in [(2,3,4),(3,4,5)]:
            with self.subTest(areas=areas):
                studies=[dict(experiment(['parking']),rationale=f'本地样例：比较第{i+1}个区域') for i in range(areas)]
                result=self.compose(studies,text=f'比较{areas}个不同区域的停车治理，不比较强度，最多{budget}次，不复跑',catalog=cat)
                self.client.request.assert_called_once()
                sent=json.loads(self.client.request.call_args.args[1][0]['content'])
                self.assertEqual(sent['required_comparisons']['regions_per_parking_group'],areas)
                self.assertEqual(len(result['experiments']),1)
                self.assertEqual(result['allocated_runs'],budget)
                self.assertEqual(result['task']['comparison_regions'],areas)
                self.assertEqual(result['experiments'][0]['title'],'多区域停车治理比较')
                self.assertEqual(result['model_budget']['minimum_required'],calls)
                self.assertEqual(result['experiments'][0]['source_studies'],list(range(1,areas+1)))
                self.assertEqual(len(result['compilation']['source_studies']),areas)
                self.assertEqual(result['compilation']['actions'][0]['kind'],'regional_parking')
                self.assertIn(f'实测{areas}个不同候选区',result['normalization_notes'][0])

    def test_regional_consolidation_cannot_drop_strength_or_budget_requirement(self):
        cat=dict(CATALOG,matched_roads=[]);studies=[experiment(['parking']),experiment(['parking'])]
        result=self.compose(studies,text='比较两个区域停车治理强度，最多5次，不复跑',catalog=cat)
        self.assertEqual(result['task']['comparison_regions'],2)
        self.assertEqual(result['allocated_runs'],5)
        self.assertEqual(result['experiments'][0]['title'],'多区域停车强度比较')
        self.assertEqual(result['model_budget']['minimum_required'],6)
        result=self.compose(studies,text='比较两个区域停车治理强度，最多3次，不复跑',catalog=cat)
        self.assertEqual(result['status'],'needs_input')
        self.assertEqual(result['experiments'],[])
        self.assertIn('至少需要5次',result['missing'][0])

    def test_duplicate_diagnoses_only_merge_into_matching_parking_baseline(self):
        studies=[experiment([],study='diagnosis'),experiment([],study='diagnosis'),experiment(['parking']),experiment(['parking'])]
        result=self.compose(studies,text='长江村路比较两个区域的停车治理，最多3次，不复跑')
        self.assertEqual(result['status'],'ready')
        self.assertEqual(len(result['experiments']),1)
        self.assertEqual(result['experiments'][0]['source_studies'],[3,4])
        self.assertEqual([x['kind'] for x in result['compilation']['actions']],['diagnosis_baseline','diagnosis_baseline','regional_parking'])
        self.assertEqual(len(result['normalization_notes']),2)
        with self.assertRaisesRegex(ModelError,'第1、2组：路网体检'):
            self.compose(studies[:2],text='长江村路只做诊断，不复跑')

    def test_ordinary_duplicates_still_fail_and_distinct_road_scopes_do_not_merge(self):
        with self.assertRaisesRegex(ModelError,'第1、2组：停车研究'):
            self.compose([experiment(['parking']),experiment(['parking'])])
        cat=dict(CATALOG,matched_roads=['长江村路','红景路'])
        result=self.compose([experiment(['parking'],road='长江村路'),experiment(['parking'],road='红景路')],
                            text='比较长江村路和红景路两个区域，不复跑，最多4次',catalog=cat)
        self.assertEqual(len(result['experiments']),2)
        self.assertEqual(result['compilation']['actions'],[])
        self.assertEqual([e['source_studies'] for e in result['experiments']],[[1],[2]])
        with self.assertRaisesRegex(ModelError,'第1、2组：单向对照'):
            self.compose([experiment(),experiment()])


class ProgrammeExecutionTests(unittest.TestCase):
    def run_programme(self, failure=False, no_candidate=False, reorder=False, stop_route=False, omit_programme=False):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        manager = Dispatcher(root / "source", root, Mock(revision=Mock(return_value=1), credentials=Mock(return_value=({}, "test-only"))))
        studies=[experiment(), experiment(["parking", "turn"], "T_1_2_3")]
        if reorder:studies.append(experiment([], study='diagnosis'))
        response = compose("长江村路，自动比较", root, Mock(request=Mock(return_value=model_reply(studies))), CATALOG)
        response["ticket"] = "test-ticket"
        order = []
        agents = []
        for i in range(len(studies)):
            agent = Mock(job_id=f"20260927_12000{i}_abcdef", job=root / "jobs" / str(i),
                         task=Task.from_dict(response['experiments'][i]['task']),
                         state=dict(id=f"20260927_12000{i}_abcdef", status="created", run_count=0, runs=[],
                                    task=copy.deepcopy(response['experiments'][i]['task'])))
            def run(a=agent, index=i):
                order.append(index)
                a.job.mkdir(parents=True)
                if failure and index == 0:
                    a.state.update(status="failed")
                    raise RuntimeError("test-only execution failure")
                from test_strategy_evidence import study_state
                a.state.update(study_state(a.job_id,a.task.as_dict()))
                if no_candidate and index==0:
                    a.state.update(status='no_candidate',runs=a.state['runs'][:1],run_count=1,recommendation='baseline',factorial_design={})
            agent.run.side_effect = run
            agents.append(agent)
        def decide(instructions, inputs, tools, choice):
            from traffic_agent.planning_payload import expand
            payload=expand(json.loads(inputs[0]['content']))
            remaining=[x for x in payload['allowed_actions'] if x!='stop']
            action=(remaining[-1] if reorder else remaining[0]) if remaining else 'stop'
            if stop_route:action='stop'
            value=dict(action=action,summary='已读取新观测',reason='先比较另一组',risk='独立基准',next_check='核对约束',evidence_refs=payload['evidence_refs'][:1])
            if 'next_check' not in tools[0]['parameters']['properties']:value.pop('next_check')
            if 'summary' not in tools[0]['parameters']['properties']:value.pop('summary')
            if 'unverified_risks' in tools[0]['parameters']['properties']:
                value.pop('risk');value['unverified_risks']=[]
            context=payload['context']
            def updates(evidence,hypotheses):
                fact=next((f for f in evidence['findings'] if f['status']!='unmeasured' and f['evidence_refs']),None)
                typed={f['hypothesis_id']:f for f in evidence['findings'] if f.get('evaluation')}
                return [dict(hypothesis_id=h['id'],judgment=typed[h['id']]['expected_judgment'] if h['id'] in typed else 'unresolved',
                    finding_ids=([typed[h['id']]['id']] if typed[h['id']]['status']!='unmeasured' and typed[h['id']]['evidence_refs'] else [])
                                if h['id'] in typed else [fact['id']] if fact else [],
                    explanation='合成调度检查：仅引用本问题的已测结果，不推断未执行内容。') for h in hypotheses]
            if context.get('initial_hypotheses'):
                value['hypothesis_updates']=updates(context['research_evidence'],context['initial_hypotheses'])
            if 'programme_updates' in tools[0]['parameters']['properties'] and not omit_programme:
                value.update(programme_updates=updates(context['programme_evidence'],context['programme_hypotheses']),programme_summary='各组采用自己的基准；合成检查保留证据不足，不能合并收益。')
            if 'cost_acknowledgments' in tools[0]['parameters']['properties']:
                facts=context['measured_conclusion']['costs']
                value['cost_acknowledgments']=[{k:r[k] for k in ('ref','constraint_passed','has_measured_cost','neighbor_new_oversaturated_km')} for r in facts] if value['action']=='stop' or context.get('require_hypothesis_review') else []
            if 'reviewed_cost_refs' in tools[0]['parameters']['properties']:
                facts=context['measured_conclusion']['costs']
                value['reviewed_cost_refs']=[r['ref'] for r in facts] if value['action']=='stop' or context.get('require_hypothesis_review') else []
            return dict(calls=[dict(name='choose_next_experiment',arguments=json.dumps(value))],metadata={'tokens':50})
        with patch("traffic_agent.dispatch.input_fingerprints", return_value={}), \
                patch("traffic_agent.preflight.check", return_value=dict(errors=[])), \
                patch("traffic_agent.dispatch.client_for", side_effect=[Mock(request=Mock(return_value=model_reply(studies))),Mock(request=Mock(side_effect=decide))]), \
                patch('traffic_agent.report.write_report'), \
                patch("traffic_agent.natural_plan.context_for", return_value=CATALOG), \
                patch("traffic_agent.dispatch.Agent", side_effect=agents):
            preview = manager.preview(dict(matcher="model", request="长江村路，自动比较"))
            launched = manager.launch(preview["ticket"])
            manager.active.join(5)
            self.assertFalse(manager.active.is_alive())
            self.assertTrue(manager.launch(preview["ticket"])["reused"])
            record = json.loads((root / "dispatches" / (launched["id"] + ".json")).read_text("utf-8"))
            self.manifest=json.loads((root/'programmes'/launched['id']/'plan.json').read_text('utf-8'))
        return order, record, agents

    def test_serial_dispatch_runs_both_plans_once(self):
        order, record, agents = self.run_programme()
        self.assertEqual(order, [0, 1])
        self.assertEqual(record["status"], "completed")
        self.assertEqual(len(record["job_ids"]), 2)
        for agent in agents: agent.run.assert_called_once()
        self.assertTrue(record['programme_review']['reviewed'])
        from traffic_agent.programme_review import verify
        self.assertTrue(verify(self.manifest,record,{a.job_id:a.state for a in agents})['passed'])

    def test_missing_final_programme_review_never_becomes_completed(self):
        order,record,agents=self.run_programme(omit_programme=True)
        self.assertEqual(order,[0,1])
        self.assertEqual(record['status'],'incomplete')
        self.assertFalse(record['programme_review']['reviewed'])
        self.assertEqual(record['model_budget']['used'],4)
        last=record['planning_trace'][-1]
        self.assertEqual(last['status'],'rejected')
        self.assertEqual(last['review_correction']['first_attempt']['validation']['code'],'argument_fields')
        for agent in agents:agent.run.assert_called_once()

    def test_programme_audit_rejects_future_and_cross_programme_citations(self):
        from traffic_agent.programme_review import verify
        from traffic_agent.feedback_planner import digest
        import copy
        _,record,agents=self.run_programme()
        states={a.job_id:a.state for a in agents}
        bad=copy.deepcopy(record)
        first=bad['planning_trace'][0]
        first['input']['context']['programme_evidence']=bad['planning_trace'][-1]['input']['context']['programme_evidence']
        first['observation_digest']=digest(first['input'])
        with self.assertRaisesRegex(ValueError,'未来'):verify(self.manifest,bad,states)
        bad=copy.deepcopy(record)
        bad['planning_trace'][-1]['programme_updates'][0]['finding_ids']=['another-programme.benefit']
        with self.assertRaisesRegex(ValueError,'引用'):verify(self.manifest,bad,states)

    def test_programme_certificate_requires_raw_job_verification(self):
        from scripts.verify_programme import verify
        from traffic_agent.data import save_json
        from traffic_agent.feedback_planner import digest
        _,record,agents=self.run_programme()
        root=agents[0].job.parent.parent
        for a in agents:
            job=root/'jobs'/a.job_id;job.mkdir()
            for filename,value in [('programme_plan.json',self.manifest),('dispatch.json',record),
                    ('source_inputs.json',self.manifest['source_inputs']),('state.json',a.state),
                    ('plan.json',dict(programme_plan_digest=digest(self.manifest)))]:
                save_json(job/filename,value)
        with patch('scripts.verify_programme.verify_job',side_effect=ValueError('合成状态没有原生输出')) as raw:
            with self.assertRaisesRegex(ValueError,'没有原生输出'):verify(record['job_id'],root)
            raw.assert_called_once()
        self.assertFalse((root/'programmes'/record['job_id']/'verification.json').exists())

    def test_programme_certificate_rejects_incomplete_and_invalid_job_ids(self):
        from scripts.verify_programme import verify
        from traffic_agent.data import save_json
        _,record,agents=self.run_programme(omit_programme=True)
        root=agents[0].job.parent.parent;directory=root/'programmes'/record['job_id']
        with self.assertRaisesRegex(ValueError,'尚未完整'):verify(record['job_id'],root)
        record['status']='completed';save_json(directory/'result.json',record)
        self.manifest['studies'][0]['job_id']='../../outside';save_json(directory/'plan.json',self.manifest)
        with patch('scripts.verify_programme.verify_job') as raw:
            with self.assertRaisesRegex(ValueError,'越界'):verify(record['job_id'],root)
            raw.assert_not_called()

    def test_model_cannot_cancel_remaining_required_studies_and_claim_completion(self):
        order,record,agents=self.run_programme(stop_route=True)
        self.assertEqual(order,[0])
        self.assertEqual(record['status'],'incomplete')
        self.assertEqual(record['progress'][1]['status'],'skipped')
        self.assertEqual(record['planning_trace'][0]['status'],'rejected')

    def test_history_listing_cannot_label_incomplete_comparison_completed_or_rewrite_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'dispatches').mkdir()
            record=dict(job_id='history',status='completed',progress=[dict(job_id='history',status='completed',
                          review=dict(goal_review=dict(complete=False,unanswered=['强度比较未完成'])))])
            path=root/'dispatches/history.json';path.write_text(json.dumps(record),encoding='utf-8')
            original=path.read_bytes()
            listing=Dispatcher(root,root,Mock()).listing()
            self.assertEqual(listing[0]['status'],'incomplete')
            self.assertEqual(listing[0]['progress'][0]['status'],'incomplete')
            self.assertEqual(listing[0]['recorded_status'],'completed')
            self.assertEqual(path.read_bytes(),original)

    def test_model_feedback_reorders_real_next_group_without_duplicates(self):
        order,record,agents=self.run_programme(reorder=True)
        self.assertEqual(order,[0,2,1])
        self.assertEqual(record['status'],'completed')
        for agent in agents:agent.run.assert_called_once()
        self.assertEqual(record['planning_trace'][0]['action'],'study_3')

    def test_failure_stops_remaining_but_no_candidate_is_preserved(self):
        order, record, agents = self.run_programme(failure=True)
        self.assertEqual(order, [0])
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["progress"][1]["status"], "skipped")
        agents[1].run.assert_not_called()
        order, record, _ = self.run_programme(no_candidate=True)
        self.assertEqual(order, [0, 1])
        self.assertEqual(record["progress"][0]["status"], "no_candidate")


if __name__ == "__main__":
    unittest.main()
