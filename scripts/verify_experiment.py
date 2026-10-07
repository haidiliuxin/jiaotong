"""Audit preserved files, not merely the successful status of an agent job."""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import OUTPUTS, input_fingerprints, numeric, save_json, changed_input_files, results, scoped_metrics
from traffic_agent.runner import DEFAULT_SOURCE, MODULES


def verify(job, archived=False, write=True):
    from traffic_agent.archive import verify_archive, original_scenario
    archive = verify_archive(job) if archived else None
    state = json.loads((job / "state.json").read_text(encoding="utf-8"))
    if state["status"] != "completed":
        raise ValueError("任务尚未完成，不能出具完整验证结论")
    source = json.loads((job / "source_inputs.json").read_text(encoding="utf-8"))
    scope = json.loads((job / "scopes.json").read_text(encoding="utf-8"))
    runs = state["runs"]
    base = next(r for r in runs if r["name"] == "baseline")
    base_path = job / base["scenario"]
    base_car = sum(numeric(base_path / "result/ODCar.tsd"))
    evidence = []
    from traffic_agent.agent import compare
    from traffic_agent.task import Task
    task = Task.from_dict(state["task"])
    oneway = task.scenario == "oneway"
    turn_access = task.scenario == "circulation" and task.turn is not None
    vehicle1 = oneway or turn_access
    if turn_access:
        profile = json.loads((job / "turn_access_profile.json").read_text(encoding="utf-8"))
        if profile != state.get("turn_access_profile") or profile['version'] != 2 or not profile['original_reference_each_stage']:
            raise ValueError("禁转适配配置或原版参考记录不一致")
    if state.get("hard_access_profile"):
        profile = json.loads((job / "hard_access_profile.json").read_text(encoding="utf-8"))
        if profile != state["hard_access_profile"] or profile["version"] != 2:
            raise ValueError("硬闭边配置记录与任务版本不一致")
    expected_modules = tuple("M6AssignVehicle1.exe" if vehicle1 and m == "M6AssignVehicle.exe" else m for m in MODULES)
    base_rows = results(base_path)
    if state["run_count"] != len(runs) or len(runs) > state["task"]["max_runs"]:
        raise ValueError("实际完成次数和预算不一致")
    if set(scope["core"]) & set(scope["neighbor"]) or not (set(scope["core"]) | set(scope["neighbor"])) <= set(scope["network"]):
        raise ValueError("固定评价范围重叠或超出网络")
    for run in runs:
        path = job / run["scenario"]
        manifest = json.loads((job / "logs" / run["name"] / "run.json").read_text(encoding="utf-8"))
        if manifest != run["execution"]:
            raise ValueError("任务内执行记录与独立模型日志不一致")
        if manifest["status"] != "completed" or tuple(m["module"] for m in manifest["modules"]) != expected_modules:
            raise ValueError(f"{run['name']}模块执行不完整")
        if any(m["status"] != "completed" or m["returncode"] != 0 for m in manifest["modules"]):
            raise ValueError(f"{run['name']}模块状态异常")
        for name, stamp in manifest["fresh_outputs"].items():
            stat = (path / name).stat()
            if archive:
                preserved = archive['files'][(path/name).relative_to(job).as_posix()]
                actual_bytes, actual_mtime = preserved['bytes'], preserved['mtime_ns']
            else:
                actual_bytes, actual_mtime = stat.st_size, stat.st_mtime_ns
            if actual_bytes != stamp["bytes"] or actual_mtime != stamp["mtime_ns"]:
                raise ValueError(f"{run['name']}/{name}在运行后改变")
            from datetime import datetime
            if actual_mtime/1e9 < datetime.fromisoformat(manifest["started"]).timestamp() - 2:
                raise ValueError("输出早于本次模型运行")
        fingerprints = input_fingerprints(path)
        before = json.loads((job / "logs" / run["name"] / "inputs_before.json").read_text(encoding="utf-8"))
        if fingerprints != before:
            raise ValueError(f"{run['name']}输入被修改")
        changed = {k for k in set(source) | set(before) if source.get(k) != before.get(k)}
        expected = changed_input_files(run["changes"], run["demand_factor"])
        if vehicle1:
            from traffic_agent.vehicle1 import DERIVED_FILES, MODAL_OUTPUTS, UNSUPPORTED_TURN_OUTPUTS, VEHICLE1_SHA256
            from traffic_agent.oneway import REQUIRED_OUTPUTS
            if state.get("hard_access_profile"):
                from traffic_agent.hard_access import verify_run
                kwargs = {'recorded_scenario': original_scenario(archive, run['scenario'])} if archive else {}
                bridge = verify_run(job, run["name"], path, run["changes"], state["hard_access_profile"], **kwargs)
                if bridge != run.get("hard_access_evidence"):
                    raise ValueError("原生转换、分轮OD和流量证据与记录不符")
            if turn_access:
                from traffic_agent.turn_access import verify_run
                added = [c['turn'] for c in run['changes'] if c.get('kind') == 'turn']
                kwargs = {'recorded_scenario': original_scenario(archive, run['scenario'])} if archive else {}
                bridge = verify_run(job, run['name'], path, state['turn_access_profile'], added, **kwargs)
                saved_bridge = json.loads((job/'logs'/run['name']/'turn_verification.json').read_text(encoding='utf-8'))
                if bridge != run.get('turn_access_evidence') or bridge != saved_bridge:
                    raise ValueError('禁转转换、参考守恒及五轮流量与记录不符')
                from traffic_agent.oneway import FLOW_FILES
                REQUIRED_OUTPUTS = ('cache/ManageNM.dat',) + tuple('result/' + f for f in FLOW_FILES.values())
                from traffic_agent.circulation import restrictions
                from traffic_agent.data import read_text
                source_rules = restrictions(base_path/'data/manageN.txt')
                if restrictions(path/'data/manageN.txt') != source_rules | set(map(tuple, added)):
                    raise ValueError('禁转措施之外的既有规则发生变化')
                tokens = list(map(int, read_text(path/'cache/ManageNM.dat').split()))
                cached = {tuple(tokens[i:i+3]) for i in range(1, len(tokens), 3)}
                if len(tokens) != 1 + 3*tokens[0] or len(cached) != tokens[0] or cached != source_rules | set(map(tuple, added)):
                    raise ValueError('禁转输入与缓存集合不符')
            expected |= DERIVED_FILES
            if state["assignment_profile"]["binary_sha256"] != VEHICLE1_SHA256:
                raise ValueError("分配程序版本不符")
            if not (set(REQUIRED_OUTPUTS) | set(MODAL_OUTPUTS)) <= manifest["fresh_outputs"].keys():
                raise ValueError("单向/车型输出缺少新生成证据")
            if any((path / filename).exists() for filename in UNSUPPORTED_TURN_OUTPUTS):
                raise ValueError("保留了该分支不支持的旧机动车转向表")
            if (path / "params/Mparam635.txt").read_bytes() != (base_path / "params/Mparam631.txt").read_bytes().replace(b"Mparam631.txt-", b"Mparam635.txt-", 1):
                raise ValueError("635参数与固定八字段不符")
            from traffic_agent.data import read_text
            for relative in DERIVED_FILES - {"params/Mparam635.txt"}:
                if read_text(path / relative).splitlines()[3] != "3.3":
                    raise ValueError("车型派生参数不是3.3")
        if changed != expected:
            raise ValueError(f"{run['name']}出现额外输入差异")
        geometries = [line for line in (path / "data/geom.txt").read_bytes().splitlines() if line.strip()]
        sizes = {key: len(numeric(path / "result" / file)) for key, file in OUTPUTS.items()}
        if any(n != len(geometries) for n in sizes.values()):
            raise ValueError(f"{run['name']}输出维度不符")
        factor = sum(numeric(path / "result/ODCar.tsd")) / base_car
        if not math.isclose(factor, run["demand_factor"], rel_tol=1e-8):
            raise ValueError(f"{run['name']}输入OD系数不符")
        internal_factor = manifest["assigned_car_od_sum"] / base["execution"]["assigned_car_od_sum"]
        if not math.isclose(internal_factor, run["demand_factor"], rel_tol=1e-5):
            raise ValueError(f"{run['name']}内部OD响应不符")
        rows = results(path)
        if scoped_metrics(rows, scope) != run["metrics"]:
            raise ValueError(f"{run['name']}报告指标与实际结果不符")
        if run["changes"]:
            matched = next(r for r in runs if not r["changes"] and r["demand_factor"] == run["demand_factor"])
            matched_rows = base_rows if matched["name"] == "baseline" else results(job / matched["scenario"])
            assessment = compare(matched_rows, rows, scope, task)
            if state.get('planning_protocol')=='observed-planning-v3' and state.get('regions'):
                from traffic_agent.spatial_planning import assess
                assessment=assess(matched_rows,rows,scope,task,state['regions'])
            if oneway:
                from types import SimpleNamespace
                from traffic_agent.oneway_agent import assess_oneway
                recomputed = dict(run)
                assessment = assess_oneway(SimpleNamespace(job=job, scope=scope, task=task), matched_rows, rows, matched, recomputed)
                if recomputed.get("oneway_response") != run.get("oneway_response"):
                    raise ValueError("单向真实响应与报告不符")
            if turn_access:
                from types import SimpleNamespace
                from traffic_agent.turn_agent import assess_turn
                recomputed = dict(run)
                assessment = assess_turn(SimpleNamespace(job=job, scope=scope, task=task), matched_rows, rows, matched, recomputed)
                if recomputed.get('turn_response') != run.get('turn_response'):
                    raise ValueError('原生四车型转移响应与报告不符')
            if state["task"]["scenario"] == "circulation" and not turn_access and any(c.get("kind") == "turn" for c in run["changes"]):
                from types import SimpleNamespace
                from traffic_agent.circulation_agent import turn_response
                required = {"cache/ManageNM.dat", "result/TVolNodecar.tsd"}
                if not required <= manifest["fresh_outputs"].keys():
                    raise ValueError("微循环输出缺乏新生成证据")
                response = turn_response(SimpleNamespace(job=job), matched, run)
                if response != run.get("turn_response"):
                    raise ValueError("保存的转向响应与文件不符")
                if not response["passed"]:
                    assessment["eligible"] = False
                    assessment["violations"].append("软件转向量或公交/绕行检查未通过，不能推荐禁转")
            if assessment != run["comparison"]:
                raise ValueError(f"{run['name']}报告约束与实际结果不符")
        evidence.append({"scenario": run["name"], "modules": len(manifest["modules"]), "result_rows": sizes,
                         "input_difference_files": sorted(changed), "car_od_factor": factor,
                         "internal_od_factor": internal_factor})
    repeat = next((r for r in runs if r["name"] == "baseline_repeat"), None)
    repeated = None
    if repeat:
        repeated = {key: numeric(base_path / "result" / name) == numeric(job / repeat["scenario"] / "result" / name)
                    for key, name in OUTPUTS.items()}
        if not all(repeated.values()):
            raise ValueError("基准重复输出不相等")
        if turn_access:
            from traffic_agent.oneway import FLOW_FILES
            from traffic_agent.turn_access import MODES
            arrays = {f: numeric(base_path/'result'/f) == numeric(job/repeat['scenario']/'result'/f)
                      for f in list(OUTPUTS.values()) + list(FLOW_FILES.values())}
            exact_turns = all(base['turn_access_evidence']['modes'][m]['turn_totals'] ==
                              repeat['turn_access_evidence']['modes'][m]['turn_totals'] for m in MODES)
            if (not all(arrays.values()) or not exact_turns or arrays != state['repeatability']['arrays']
                    or not state['repeatability']['exact_match'] or not state['repeatability']['turn_totals_exact']):
                raise ValueError('九类基准道路数组或转移量复跑不同')
            repeated = arrays
    by_name = {r["name"]: r for r in runs}
    if task.scenario == "diagnosis":
        from traffic_agent.diagnostic_study import diagnose_network, observation
        from traffic_agent.data import scopes
        diagnosis = diagnose_network(base_rows, task)
        saved = json.loads((job / "diagnosis.json").read_text(encoding="utf-8"))
        if saved != diagnosis or scope != scopes(base_rows, [r["index"] for r in diagnosis["selected"]], task.neighbor_hops):
            raise ValueError("诊断对象或范围与本次基准不符")
        if (any(r["changes"] for r in runs) or state["recommendation"] != "baseline"
                or state.get("robust_across_tested_demand") is not None
                or bool(repeat) != task.repeat_baseline
                or len(runs) != len(task.demand_factors) + int(task.repeat_baseline)
                or state["run_count"] != len(runs) or len(runs) > task.max_runs):
            raise ValueError("诊断任务执行了额外措施、超预算或错误声明治理结论")
        indices = [r["index"] for r in base_rows if r["included"] and (not task.road_name or r["name"] == task.road_name)]
        actual = [observation(results(job/r["scenario"]), indices, r["demand_factor"], r["name"])
                  for r in runs if r["name"] != "baseline_repeat"]
        if (actual != state.get("diagnostic_observations")
                or {o["car_demand_factor"] for o in actual} != set(task.demand_factors)):
            raise ValueError("道路诊断或需求覆盖与原始输出重算不一致")
    if oneway or turn_access:
        from traffic_agent.agent import recommend
        from traffic_agent.oneway_agent import diagnose_oneway
        from traffic_agent.data import scopes
        from types import SimpleNamespace
        diagnosis = json.loads((job / "diagnosis.json").read_text(encoding="utf-8"))
        # Rebuild selection without writing to the experiment or emitting synthetic events.
        fake = SimpleNamespace(job=job, source=base_path, task=task, event=lambda *a: None)
        if turn_access:
            from traffic_agent.turn_agent import diagnose_turn
            actual_diagnosis = diagnose_turn(fake, base_rows, base)
        else:
            actual_diagnosis = diagnose_oneway(fake, base_rows, base, record_evidence=False,
                                               portfolio=bool(state.get('target_planning_protocol')))
            if state.get('target_planning_protocol'):
                from traffic_agent.oneway_planning import selected_diagnosis
                from traffic_agent.feedback_planner import digest
                catalog = json.loads((job/'oneway_target_catalogue.json').read_text('utf-8'))
                if catalog != state.get('oneway_target_catalogue') or catalog['diagnosis_digest'] != digest(actual_diagnosis):
                    raise ValueError('单向模型候选并非来自本次原生基准与实际拓扑')
                actual_diagnosis = selected_diagnosis(actual_diagnosis, diagnosis.get('selected_target'))
        if actual_diagnosis != diagnosis or scope != scopes(base_rows, [r["index"] for r in diagnosis["selected"]], task.neighbor_hops):
            raise ValueError("新基准选点或固定范围不一致")
        design = state["factorial_design"]
        protocol = json.loads((job / "factorial_protocol.json").read_text(encoding="utf-8"))
        if dict(design, full_demand_grid=False) != protocol or not design["full_demand_grid"]:
            raise ValueError("政策前协议与完成设计不一致")
        if len(runs) != 4 * len(task.demand_factors) + int(task.repeat_baseline):
            raise ValueError("没有完成全部组合与复跑")
        selected = diagnosis["selected"]
        parking = [{"from_node": r["from_node"], "to_node": r["to_node"], "old_parking": r["parking"], "parking": 2} for r in selected]
        close = {"kind": "oneway", "closed_direction": [selected[0]["from_node"], selected[0]["to_node"]]}
        expected_policies = {"S1_parking": parking, "S2_oneway": [close], "S3_combined": parking + [close]}
        if turn_access:
            from traffic_agent.turn_agent import policies
            expected_policies = policies(selected[0], task.turn)
            if design['turn_access'] != state['turn_access_profile'] or design['turn'] != task.turn:
                raise ValueError('完整组合与原始禁转协议不同')
        if design["policies"] != expected_policies:
            raise ValueError("政策定义不是冻结的双向停车、单向与组合")
        first_policy = next(r for r in runs if r["changes"])
        from datetime import datetime
        protocol_mtime = (archive['files']['factorial_protocol.json']['mtime_ns']/1e9 if archive else
                          (job / "factorial_protocol.json").stat().st_mtime)
        if protocol_mtime >= datetime.fromisoformat(first_policy["execution"]["started"]).timestamp():
            raise ValueError("组合协议没有在首项政策运行前保存")
        for factor in task.demand_factors:
            subset = [r for r in runs if r["demand_factor"] == factor and r["name"] != "baseline_repeat"]
            baseline_record = next(r for r in subset if not r["changes"])
            candidates = [r for r in subset if r["changes"]]
            if len(subset) != 4 or {r.get("policy_label") for r in candidates} != set(design["policies"]):
                raise ValueError("需求点缺失预定政策")
            if any(r["changes"] != design["policies"][r["policy_label"]] for r in candidates):
                raise ValueError("政策措施在需求间改变")
            winner = recommend(candidates, task.selection_mode)
            if state["recommendation_by_car_demand"][str(float(factor))] != (winner["name"] if winner else baseline_record["name"]):
                raise ValueError("条件推荐不是预定规则最优合格项")
        if state["recommendation"] != state["recommendation_by_car_demand"]["1.0"]:
            raise ValueError("主推荐与1.0条件建议不同")
        primary_label = by_name[state["recommendation"]].get("policy_label")
        checks = []
        for item in state["sensitivity"]:
            factor = item["car_demand_factor"]
            record = next(r for r in runs if r["demand_factor"] == factor and
                          (r.get("policy_label") == primary_label if primary_label else not r["changes"]))
            if item["fixed_primary_policy"] != record["name"] or (primary_label and item["comparison"] != record["comparison"]):
                raise ValueError("主方案敏感性被条件换方案掩盖")
            checks.append(record.get("comparison", {}).get("eligible", True))
        if {i["car_demand_factor"] for i in state["sensitivity"]} != set(task.demand_factors) - {1.0}:
            raise ValueError("缺少主方案的需求复核")
        expected_robust = all(checks) if primary_label and checks else None
        if state["robust_across_tested_demand"] != expected_robust:
            raise ValueError("主方案稳健性结论不符")
    recommended = by_name[state["recommendation"]]
    if task.recommendation_version == 'tolerance-v2' or task.recommendation_tolerance_pcu_h > 0:
        from traffic_agent.agent import plan
        saved_plan = json.loads((job/'plan.json').read_text(encoding='utf-8'))
        key = 'selection_protocol' if task.recommendation_version == 'tolerance-v2' else 'selection_rule'
        expected_plan = plan(task, state.get('source', DEFAULT_SOURCE))
        if (saved_plan.get(key) != expected_plan[key] or saved_plan['task'] != state['task']
                or ('source' in state and saved_plan.get('assumptions') != expected_plan['assumptions'])):
            raise ValueError('容差协议或数据源说明没有随任务预先冻结或参数被改变')
        if state.get('evidence_protocol') and (saved_plan.get('evidence_protocol')!=state['evidence_protocol'] or saved_plan.get('research_hypotheses')!=state.get('research_hypotheses')):
            raise ValueError('研究假设没有在新基准前冻结')
        if saved_plan.get('choice_basis_protocol')!=state.get('choice_basis_protocol'):
            raise ValueError('选点观测协议没有在新基准前冻结')
        if saved_plan.get('finding_reference_schema')!=state.get('finding_reference_schema'):
            raise ValueError('假设引用协议没有在新基准前冻结')
        if any(saved_plan.get(k)!=state.get(k) for k in ('input_protocol','choice_rationale_protocol','risk_review_protocol')):
            raise ValueError('决策输入或试验依据协议没有在新基准前冻结')
        if state.get('programme_protocol') and saved_plan.get('programme_plan_digest')!=state.get('programme_plan_digest'):
            raise ValueError('研究总计划摘要没有随本组任务冻结')
        if state.get('target_planning_protocol') and saved_plan.get('target_planning_protocol') != state['target_planning_protocol']:
            raise ValueError('单向模型选点协议没有在新基准前冻结')
        from datetime import datetime
        plan_time = archive['files']['plan.json']['mtime_ns']/1e9 if archive else (job/'plan.json').stat().st_mtime
        if plan_time >= datetime.fromisoformat(base['execution']['started']).timestamp():
            raise ValueError('容差协议保存晚于基准开始')
    if task.recommendation_version == 'tolerance-v2' and task.scenario == 'parking' and task.search_mode == 'standard' and not state.get('model_grid'):
        from traffic_agent.selection import record_selection
        candidates = [r for r in runs if r['changes'] and r['demand_factor'] == 1]
        selection = record_selection(candidates)
        if state.get('selection_analysis') != selection or state['recommendation'] != (selection['recommendation'] or 'baseline'):
            raise ValueError('容差推荐与实测候选重算不一致')
    if task.recommendation_tolerance_pcu_h > 0 and task.scenario == 'parking' and task.search_mode == 'standard' and not state.get('model_grid'):
        from traffic_agent.agent import recommend
        candidates = [r for r in runs if r['changes'] and r['demand_factor'] == 1]
        choice = recommend(candidates, task.selection_mode)
        if state['recommendation'] != (choice['name'] if choice else 'baseline'):
            raise ValueError('绝对耗时容差推荐与实测候选不一致')
    if recommended["changes"] and not recommended["comparison"]["eligible"]:
        raise ValueError("主推荐包含未通过约束的方案")
    for factor, name in state.get("recommendation_by_car_demand", {}).items():
        record = by_name[name]
        if record["demand_factor"] != float(factor) or (record["changes"] and not record["comparison"]["eligible"]):
            raise ValueError("条件推荐选择了需求不匹配或未通过约束的方案")
    report = {"passed": True, "completed_scenarios": len(runs), "module_executions": sum(x["modules"] for x in evidence),
              "scope_sizes": {k: len(v) for k, v in scope.items()}, "repeat_arrays_exact": repeated,
              "verified_runs": evidence, "limitations": "只核验保存的实验与输入输出证据，不证明现实标定有效或全部草案已实现"}
    if turn_access:
        report.update(native_assignment_calls=sum(r['turn_access_evidence']['native_calls'] for r in runs),
                      adapted_calls=20*len(runs), reference_calls=20*len(runs), full_demand_grid_verified=True)
    if task.search_mode == "robust" and not state.get('model_grid'):
        from traffic_agent.search_audit import verify_search
        report["active_search_audit"] = verify_search(job, state, archive)
    if state.get('planner_trace') or state.get('target_planning_protocol'):
        from traffic_agent.planning_audit import verify_planning
        diagnosis = json.loads((job/'diagnosis.json').read_text(encoding='utf-8'))
        if state.get('planning_protocol') or state.get('target_planning_protocol'):
            from traffic_agent.data import diagnose, scopes
            if state.get('planning_protocol')=='observed-planning-v3' and task.scenario=='parking':
                from traffic_agent.spatial_planning import diagnose_portfolio as diagnose
                if state.get('regions')!=diagnosis.get('regions'):
                    raise ValueError('候选区域与冻结诊断不同')
            if task.scenario=='parking' and (diagnosis != diagnose(base_rows,task) or scope != scopes(base_rows,[r['index'] for r in diagnosis['selected']],task.neighbor_hops)):
                raise ValueError('模型规划的冻结范围不是由新基准诊断产生')
            for run in runs:
                rows=results(job/run['scenario'])
                if run.get('core_rows') != [rows[i] for i in scope['core']]:
                    raise ValueError('模型读取的核心路段与原生输出不同')
                if state.get('regions'):
                    from traffic_agent.spatial_planning import observe
                    if run.get('spatial_observation')!=observe(rows,state['regions']):
                        raise ValueError('模型区域观测与实际原生输出不符')
        entries=[json.loads(x) for x in (job/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        model_events=[x for x in entries if x['event'] in ('model_decision','model_review')]
        if [e['data'] for e in model_events] != [{k:v for k,v in t.items() if k!='input'} for t in state['planner_trace']]:
            raise ValueError('模型决策事件与状态记录不同')
        report['model_planning_audit'] = verify_planning(state, diagnosis)
    if archived:
        report['portable_archive_verified'] = True
    if write:
        save_json(job / ('portable_verification.json' if archived else 'verification.json'), report)
        print(json.dumps({k: v for k, v in report.items() if k != "verified_runs"}, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--seal', action='store_true', help='严格核验通过后生成可移植档案清单，仅在原运行目录使用')
    mode.add_argument('--archive', action='store_true', help='先核对档案全部字节，再按原路径和时间戳执行语义核验')
    args = parser.parse_args()
    job = args.job.resolve()
    verify(job, archived=args.archive)
    if args.seal:
        from traffic_agent.archive import write_archive
        archive = write_archive(job)
        print(f"ARCHIVE {len(archive['files'])} files")
