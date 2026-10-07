"""A rule agent: observe, propose, execute, evaluate, revise, and explain."""
import json
import math
import uuid
from datetime import datetime
from pathlib import Path

from .data import (input_fingerprints, prepare, diagnose, save_json, scoped_metrics, scopes,
                   OUTPUTS, numeric, results, changed_input_files)
from .runner import DEFAULT_SOURCE, EXPERIMENTS, execute, now, runtime_lock, setup_runtime, validate_source
from .source_context import assumption, is_default_source, recommendation_scope
from .task import capability_for, Task
from .storage import ensure_space


def knowledge(scenario):
    items = json.loads(Path(__file__).with_name("knowledge.json").read_text(encoding="utf-8"))
    return [k for k in items if scenario in k["topics"]]


def plan(task, source=DEFAULT_SOURCE):
    capability = capability_for(task)
    output = {"task": task.as_dict(), "capability": capability, "knowledge": knowledge(task.scenario),
            "agent_type": "确定性规则推理＋白名单工具调用；不调用大语言模型",
            "steps": ["数据与预算校验", "新建基准并实际仿真", "识别区域瓶颈、冻结评价范围",
                      "生成2级停车治理", "读取结果、约束检查、反馈增强或缩小范围",
                      "按预定规则选择候选或保留基准", "成对需求敏感性与同输入复跑", "报告及过程证据"],
            "assumptions": [assumption(source), "小汽车OD敏感性，其余车型与公交线路不变",
                            "邻区时间、全网时间和过饱和里程容忍阈值是预先设置的实验假设",
                            "本执行层以冻结JSON任务卡为准；规划可来自大模型，规则入口只支持公布模板"],
            "limits": ["不验证现实停车承接和执法成本", "不声称动态排队溢出或均衡收敛",
                       "其他草案方向必须先满足capability.requires，不会用停车流程代替"]}
    if task.scenario == "circulation":
        output["steps"] = ["新建基准并实际仿真", "候选转向的公交保护、状态可达性与绕行上限检查",
                           "冻结一个通过检查且基准转向量非零的瓶颈区域", "同条件比较S1停车2级、S2禁转、S3组合",
                           "核对禁转缓存与转向量实际响应", "拒绝不合格候选，选择单项、组合或基准",
                           "按任务执行基准复跑与推荐方案成对需求敏感性", "报告及过程证据"]
        output["limits"] += ["当前微循环只验证单个转向限制，不包含道路单向化", "绕行固定上限为额外2km且距离3倍，不代表现实实施标准"]
        if task.turn is not None:
            output["steps"] = ["验证用户指定转向和出口停车路段", "建立固定路口状态图的新Vehicle1基准",
                               "检查实际小汽车正转向量及公交/绕行约束并冻结范围",
                               "每个需求完整运行S0/S1停车/S2禁转/S3组合", "每轮同输入原版参考、四车型实际响应与指标约束",
                               "分别选择条件推荐，复核主方案敏感性", "九类基准数组复跑与报告"]
            output["required_runs"] = 4 * len(task.demand_factors) + int(task.repeat_baseline)
            output["assumptions"] += ["显式转向不代表自动瓶颈选点；基准和政策均在指定路口执行既有禁转及禁止掉头"]
            output["limits"] += ["每轮参考检查相对原版的净流量，不能证明全OD路径正确或全网既有规则生效"]
    if task.scenario == "oneway":
        output["steps"] = ["独立Vehicle1硬闭边环境、新基准与固定参数", "按新基准筛选瓶颈并检查反向、公交及全车型边界接入",
                           "冻结双向核心、邻区和全网", "各需求点完整运行S0基准/S1停车/S2单向/S3组合",
                           "实际禁行响应与核心、邻区、全网指标共同筛除", "比较条件推荐和主方案稳健性", "同输入基准复跑与报告"]
        output["required_runs"] = 4 * len(task.demand_factors) + int(task.repeat_baseline)
        output["limits"] += ["与默认分配程序的历史结果不能交叉计算政策收益", "仅单条机动车方向关闭；不使用该分支未更新的机动车转向表"]
        output["assumptions"] += ["新增闭边在原生车型求解前移除，保存转换前后输入及输出映射；不宣称原有禁转全部生效"]
    if task.scenario == "diagnosis":
        output["steps"] = ["校验源数据并建立无治理基准", "读取指定范围内所有路段并按实测耗时排序",
                           "执行设定的小汽车需求假设及基准复现", "记录拥堵位置、道路诊断及证据，不执行治理"]
        output["required_runs"] = len(task.demand_factors) + int(task.repeat_baseline)
        output["limits"].append("路网诊断不验证治理收益，不能把道路耗时排序当作拥堵因果识别")
    if task.search_mode == "robust":
        from .robust_search import protocol
        output["steps"] = ["计算前冻结搜索协议与预算", "真实基准诊断并冻结核心/邻区，补齐各需求基准",
                           "优先测试高需求，遇约束失败提前淘汰统一候选", "根据已测路段耗时变化缩小范围，再验证全部需求",
                           "合格但仍有瓶颈则试验较强等级，始终预留完整配对和复跑预算",
                           "全部需求通过后按最差工况选优，否则保留基准", "保存可重放决策链与真实模型证据"]
        if task.backtrack_on_singleton_failure:
            output["steps"].insert(4, "单路段失败时按无治理基准固定排序尝试未测原核心方向，仍保留完整预算")
        output["search_protocol"] = protocol(task)
    if task.recommendation_version == 'tolerance-v2':
        from .selection import RULE, TOLERANCES
        output['selection_protocol'] = dict(version='tolerance-v2', tolerances=list(TOLERANCES), rule=RULE)
    if task.max_new_network_oversaturated_km is not None:
        output['assumptions'].append(
            f'全网毛新增过饱和有向里程上限{task.max_new_network_oversaturated_km:g}km为本任务事前设定的额外硬约束')
        output['limits'].append('该上限只防止新出现的过饱和被其他路段改善抵消，不代表全网其他损失已消除')
        if 'selection_protocol' in output:
            output['selection_protocol']['additional_hard_constraint'] = (
                f'全网毛新增过饱和有向里程≤{task.max_new_network_oversaturated_km:g}km')
        if task.scenario == 'parking' and task.search_mode == 'standard':
            output['steps'].insert(-2, '联合候选超出全网毛新增上限且预算允许时，逐一实测入选方向的单路段方案')
            output['limits'].append('逐一测试单路段会增加模型调用；预算不足时仍按原缩小范围规则反馈')
    from .tradeoffs import selection_rule
    output['selection_rule'] = selection_rule(task.as_dict())
    return output


def pct(before, after):
    if before == 0:
        return 0.0 if after == 0 else None
    return (after / before - 1) * 100


def compare(before, after, scope, task):
    if [(r["from_node"], r["to_node"], r["length_km"], r["included"]) for r in before] != [
            (r["from_node"], r["to_node"], r["length_km"], r["included"]) for r in after]:
        raise ValueError("评价路网发生变化，不能使用原有范围比较")
    a, b = scoped_metrics(before, scope), scoped_metrics(after, scope)
    delta = {}
    for name in scope:
        delta[name] = {"speed_change_pct": None if a[name]["weighted_speed_kmh"] is None or b[name]["weighted_speed_kmh"] is None
                       else pct(a[name]["weighted_speed_kmh"], b[name]["weighted_speed_kmh"]),
                       "running_time_change_pct": pct(a[name]["running_time_pcu_h"], b[name]["running_time_pcu_h"]),
                       "oversaturated_km_change": b[name]["oversaturated_km"] - a[name]["oversaturated_km"]}
    violations = []
    for name, limit in (("neighbor", task.max_neighbor_time_increase_pct), ("network", task.max_network_time_increase_pct)):
        change = delta[name]["running_time_change_pct"]
        if change is None or change > limit + 1e-9:
            violations.append(f"{name}路段行驶时间增幅超过{limit}%")
    newly_overloaded = [after[i] for i in scope["neighbor"] if before[i]["vc"] <= 1 < after[i]["vc"]]
    # Gross new overloaded length cannot be masked by improvement on another link.
    new_length = sum(r["length_km"] for r in newly_overloaded)
    if new_length > task.max_new_oversaturated_km + 1e-9:
        violations.append(f"邻区新增过饱和有向里程超过{task.max_new_oversaturated_km}km")
    network_new_length = None
    if task.max_new_network_oversaturated_km is not None:
        network_new_length = sum(after[i]["length_km"] for i in scope["network"]
                                 if before[i]["vc"] <= 1 < after[i]["vc"])
        if network_new_length > task.max_new_network_oversaturated_km + 1e-9:
            violations.append(f"全网毛新增过饱和有向里程超过{task.max_new_network_oversaturated_km}km")
    benefit = b["core"]["running_time_pcu_h"] < a["core"]["running_time_pcu_h"] - 1e-9
    if not benefit:
        violations.append("固定核心区路段行驶时间未改善")
    result = {"before": a, "after": b, "delta": delta, "eligible": not violations,
            "violations": violations, "new_neighbor_oversaturated_km": new_length,
            "new_neighbor_oversaturated_links": newly_overloaded}
    if network_new_length is not None:
        result["new_network_oversaturated_km"] = network_new_length
    return result


def recommend(records, version='legacy-v1', *, tolerance=None):
    """Accept the Sep-14 absolute-tolerance API as well as explicit v2 rules."""
    if tolerance is not None:
        if version != 'legacy-v1':
            raise ValueError('只能选择一种推荐规则')
        version = tolerance
    if version == 'tolerance-v2':
        from .selection import record_selection
        selected = record_selection(records)['recommendation']
        return next((r for r in records if r['name'] == selected), None)
    absolute = 0.0 if version == 'legacy-v1' else version
    if type(absolute) not in (int, float) or not math.isfinite(absolute) or absolute < 0:
        raise ValueError('未知推荐版本')
    eligible = [r for r in records if r.get("comparison", {}).get("eligible")]
    if not eligible:
        return None
    def time(r):
        return r['metrics']['network']['running_time_pcu_h']
    if any(not math.isfinite(time(r)) or time(r) < 0 for r in eligible):
        raise ValueError('候选全网耗时必须为有限非负数')
    best = min(map(time, eligible))
    equivalent = [r for r in eligible if time(r) - best <= absolute]
    return min(equivalent, key=lambda r: (len(r['changes']), -sum(c.get('parking', 0) for c in r['changes']), time(r), r['name']))


def feedback(record, baseline, scope, task):
    """The next tool arguments depend on measured outcomes, not a fixed script list."""
    changes = record["changes"]
    if not record["comparison"]["eligible"]:
        if len(changes) > 1:
            return changes[:max(1, len(changes) // 2)], "邻区/全网约束或核心收益未通过，缩小治理范围后重新比较"
        return None, "单路段方案仍未通过约束，停止扩大治理并保留基准或其他合格候选"
    core_speed = record["metrics"]["core"]["weighted_speed_kmh"]
    loaded = any(r["adjusted_load_ratio"] is not None and r["adjusted_load_ratio"] > 1 for r in record["core_rows"])
    if all(c["parking"] == 2 for c in changes) and ((core_speed or 0) < task.target_speed_kmh or loaded):
        return [dict(c, parking=1) for c in changes], "核心速度未达任务阈值或流量/管理修正容量仍大于1，试验1级治理并核对边际收益和外溢"
    return None, "当前治理已达到搜索停止条件或已验证较强等级，不继续无依据加大强度"


def risk_singleton_candidates(record, task, available_runs):
    """Enumerate the frozen selected links only when every singleton fits budget."""
    changes = record['changes']
    limit = task.max_new_network_oversaturated_km
    gross = record['comparison'].get('new_network_oversaturated_km')
    if (task.scenario != 'parking' or task.search_mode != 'standard' or limit is None
            or gross is None or gross <= limit + 1e-9 or len(changes) < 2
            or task.max_feedback < len(changes) or available_runs < len(changes)):
        return []
    return [[dict(change)] for change in changes]


class Agent:
    def __init__(self, task, source=DEFAULT_SOURCE, root=EXPERIMENTS, expected_inputs=None, planner=None):
        self.task = task
        self.planner = planner
        self.programme_plan = None
        self.expected_inputs = expected_inputs
        self.source, self.root = Path(source).resolve(), Path(root).resolve()
        if self.root.is_relative_to(self.source):
            raise ValueError("实验输出根目录不能位于源场景内，否则复制会递归包含自身")
        self.job_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.job = self.root / "jobs" / self.job_id
        self.state = {"id": self.job_id, "status": "created", "started": now(), "task": task.as_dict(),
                      "runs": [], "decisions": [], "sensitivity": [], "run_count": 0}
        from .research_evidence import REFERENCE_SCHEMA
        if getattr(planner,'finding_reference_schema',None)==REFERENCE_SCHEMA:
            self.state['finding_reference_schema']=REFERENCE_SCHEMA
        from .planning_payload import VERSION as INPUT_VERSION
        from .planning_payload import SUPPORTED_VERSIONS as INPUT_VERSIONS
        from .choice_rationale import VERSION as RATIONALE_VERSION
        from .risk_review import VERSION as RISK_VERSION
        from .risk_review import SUPPORTED_VERSIONS as RISK_VERSIONS
        from .planning_response import ARGUMENT_CORRECTION_VERSION
        for key,version in (('input_protocol',INPUT_VERSION),('choice_rationale_protocol',RATIONALE_VERSION),('risk_review_protocol',RISK_VERSION),('argument_correction_protocol',ARGUMENT_CORRECTION_VERSION)):
            actual=getattr(planner,key,None)
            supported=RISK_VERSIONS if key=='risk_review_protocol' else INPUT_VERSIONS if key=='input_protocol' else (version,)
            if actual in supported:self.state[key]=actual
        if not is_default_source(self.source):
            self.state["source"] = str(self.source)
        self.scope = None

    def event(self, kind, payload):
        entry = {"time": now(), "event": kind, "data": payload}
        with (self.job / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")
        # The UTF-8 evidence file is authoritative. A redirected Windows console
        # may use GBK and cannot encode model punctuation such as U+2212.
        try:
            print(json.dumps(entry, ensure_ascii=True, allow_nan=False), flush=True)
        except (OSError, UnicodeError):
            pass

    def persist(self):
        save_json(self.job / "state.json", self.state)

    def simulation(self, name, changes, factor=1.0):
        if self.state["run_count"] >= self.task.max_runs:
            raise RuntimeError("仿真预算已耗尽")
        ensure_space([self.task], self.source, self.root, run_limit=1, include_runtime=False)
        scenario = self.job / "scenarios" / name / "基础路网"
        self.event("prepare_scenario", {"name": name, "changes": changes, "car_demand_factor": factor})
        extra = ("cache/ManageNM.dat", "result/TVolNodecar.tsd") if self.task.scenario == "circulation" else ()
        cleanup = extra
        turn_access = bool(self.state.get("turn_access_profile"))
        if self.task.scenario == "oneway":
            from .oneway import REQUIRED_OUTPUTS
            from .vehicle1 import MODAL_OUTPUTS, UNSUPPORTED_TURN_OUTPUTS, DERIVED_FILES, prepare_parameters
            extra = REQUIRED_OUTPUTS + MODAL_OUTPUTS
            cleanup = extra + UNSUPPORTED_TURN_OUTPUTS
        elif turn_access:
            from .vehicle1 import MODAL_OUTPUTS, UNSUPPORTED_TURN_OUTPUTS, DERIVED_FILES, prepare_parameters
            from .oneway import FLOW_FILES
            extra = MODAL_OUTPUTS + ("cache/ManageNM.dat",) + tuple("result/" + f for f in FLOW_FILES.values())
            cleanup = extra + UNSUPPORTED_TURN_OUTPUTS
        edits = prepare(self.source, scenario, changes, factor, extra_outputs=cleanup) if cleanup else prepare(self.source, scenario, changes, factor)
        expected_differences = changed_input_files(changes, factor)
        if self.task.scenario == "oneway" or turn_access:
            derived = prepare_parameters(scenario, self.runtime / "programs/M6AssignVehicle1.exe")
            expected_differences |= DERIVED_FILES
            self.state["assignment_profile"] = dict(derived, module="M6AssignVehicle1.exe")
            edits.append({"kind": "assignment_profile", **derived})
        actual = input_fingerprints(scenario)
        differences = {k for k in set(actual) | set(self.source_inputs) if actual.get(k) != self.source_inputs.get(k)}
        if differences != expected_differences:
            raise ValueError(f"场景写入差异不符合白名单: {differences}, 应为{expected_differences}")
        save_json(scenario.parent / "edits.json", {"changes": edits, "input_differences": sorted(differences)})
        self.state["run_count"] += 1
        self.state["active_scenario"] = name
        self.persist()
        kwargs = {"required_outputs": extra} if extra else {}
        if self.task.scenario == "oneway":
            kwargs["assignment_module"] = "M6AssignVehicle1.exe"
            from .hard_access import configure
            kwargs["bridge_environment"] = configure(self.job, self.runtime, name, changes)
        elif turn_access:
            from .turn_access import configure
            kwargs["assignment_module"] = "M6AssignVehicle1.exe"
            kwargs["bridge_environment"] = configure(self.job, self.runtime, name, self.state["turn_access_profile"],
                                                     [c["turn"] for c in changes if c.get("kind") == "turn"])
        rows, manifest = execute(scenario, self.runtime, self.job / "logs" / name,
                                 self.task.module_timeout_seconds, self.event, **kwargs)
        record = {"name": name, "changes": changes, "demand_factor": factor, "edits": edits,
                  "scenario": str(scenario.relative_to(self.job)), "execution": manifest,
                  "metrics": scoped_metrics(rows, self.scope) if self.scope else None}
        if self.task.scenario == "oneway":
            from .hard_access import verify_run
            record["hard_access_evidence"] = verify_run(self.job, name, scenario, changes, self.state["hard_access_profile"])
            save_json(self.job / "logs" / name / "bridge_verification.json", record["hard_access_evidence"])
            self.event("hard_access_verified", {"scenario": name, "native_calls": 20, "transform_inputs_verified": True})
        elif turn_access:
            from .turn_access import verify_run
            added = [c["turn"] for c in changes if c.get("kind") == "turn"]
            record["turn_access_evidence"] = verify_run(self.job, name, scenario, self.state["turn_access_profile"], added)
            save_json(self.job / "logs" / name / "turn_verification.json", record["turn_access_evidence"])
            self.event("turn_access_verified", {"scenario": name, "native_calls": 40, "original_reference_each_stage": True})
        if self.scope:
            record["core_rows"] = [rows[i] for i in self.scope["core"]]
        if self.state.get('regions'):
            from .spatial_planning import observe
            record['spatial_observation']=observe(rows,self.state['regions'])
        self.state["runs"].append(record)
        self.persist()
        return rows, record

    def run(self, allow_experimental=False):
        self.job.mkdir(parents=True, exist_ok=False)
        frozen_plan = plan(self.task, self.source)
        if self.programme_plan is not None:
            from .feedback_planner import digest
            study=next(s for s in self.programme_plan['studies'] if s['job_id']==self.job_id)
            if study['task']!=self.task.as_dict():raise ValueError('研究组与预先冻结的总任务卡不同')
            save_json(self.job/'programme_plan.json',self.programme_plan)
            frozen_plan.update(programme_plan_digest=digest(self.programme_plan),evidence_protocol=self.state['evidence_protocol'],
                               research_hypotheses=self.state['research_hypotheses'])
            if self.state.get('review_scope'):
                frozen_plan['review_scope']=self.state['review_scope']
            if self.state.get('choice_basis_protocol'):
                frozen_plan['choice_basis_protocol']=self.state['choice_basis_protocol']
        for key in ('input_protocol','choice_rationale_protocol','risk_review_protocol','argument_correction_protocol'):
            if self.state.get(key):frozen_plan[key]=self.state[key]
        if self.planner:
            from .action_execution import VERSION as EXECUTION_VERSION
            if self.state.get('finding_reference_schema'):
                frozen_plan['finding_reference_schema']=self.state['finding_reference_schema']
            self.state['action_execution_protocol']=EXECUTION_VERSION
            frozen_plan['action_execution_protocol']=EXECUTION_VERSION
            frozen_plan.update(agent_type='大模型编排研究并复核实测；本组按冻结工具与约束执行',
                               planning_protocol='observed-planning-v3', model_request_limit=self.planner.limit)
            if self.task.scenario=='parking':
                from .research_evidence import VERSION as EVIDENCE_VERSION
                hypotheses=getattr(self.planner,'hypotheses',[])
                self.state.update(evidence_protocol=EVIDENCE_VERSION,research_hypotheses=hypotheses if isinstance(hypotheses,list) else [])
                frozen_plan.update(evidence_protocol=EVIDENCE_VERSION,research_hypotheses=self.state['research_hypotheses'])
                from .spatial_planning import VERSION as SPATIAL_VERSION, REGION_LIMIT, DIRECTIONS_PER_REGION
                frozen_plan['spatial_protocol']=dict(version=SPATIAL_VERSION,region_limit=REGION_LIMIT,
                    directions_per_region=DIRECTIONS_PER_REGION,action_limit=self.task.max_links,
                    regional_time_increase_limit_pct=self.task.max_neighbor_time_increase_pct)
                frozen_plan['steps']=['建立本次TranStar基准并扫描全部参与分配路段','在用户范围内提取多个合法候选区，冻结评价并集与各区门槛',
                    '模型读取各区观测，选择治理位置、范围与停车等级','执行完整需求对照，用新收益及各区代价选择下一候选',
                    '完成约定比较和模型复核，再按固定约束推荐或保留基准','保存全部实测、失败和模型观测证据']
            if self.task.scenario == 'parking':
                self.state['planning_protocol']='observed-planning-v3'
            elif self.task.scenario == 'oneway':
                from .oneway_planning import VERSION as TARGET_VERSION
                self.state['target_planning_protocol'] = TARGET_VERSION
                frozen_plan['target_planning_protocol'] = TARGET_VERSION
                frozen_plan['agent_type'] = '大模型依据新基准选择单向目标；完整单项组合对照与结果复核'
                frozen_plan['steps'] = ['新建Vehicle1基准并读取实际路段状态',
                    '检查至多4个合法单向候选；模型比较新观测与绕行条件后选择一个方向',
                    '冻结所选双向核心及邻区，完成各需求的S0基准、S1停车、S2单向与S3组合对照',
                    '模型复核实际响应、约束和初始假设；保留无收益、失败与未验证范围']
            if self.task.search_mode == 'robust':
                frozen_plan['search_protocol'] = dict(version='model-grid-v1',task=self.task.as_dict())
        save_json(self.job / "plan.json", frozen_plan)
        if self.task.search_mode == "robust" or (self.planner and self.task.scenario == 'parking' and len(self.task.demand_factors)>1):
            from .robust_search import protocol
            save_json(self.job / "search_protocol.json", dict(version='model-grid-v1',task=self.task.as_dict()) if self.planner else protocol(self.task))
        self.persist()
        capability = capability_for(self.task)
        if capability["state"] != "executable" and not (allow_experimental and self.task.scenario in ("circulation", "oneway")):
            self.state.update(status="needs_validation", missing=capability["requires"], finished=now())
            self.persist()
            self.event("capability_gate", self.state["missing"])
            return self.job
        try:
            with runtime_lock(self.root):
                self.state['storage_preflight'] = ensure_space([self.task], self.source, self.root)
                self.state["status"] = "running"
                self.persist()
                self.state["source_validation"] = validate_source(self.source)
                self.source_inputs = input_fingerprints(self.source)
                if self.expected_inputs is not None and self.source_inputs != self.expected_inputs:
                    raise ValueError("源场景与已匹配任务卡不一致，未开始原生仿真")
                save_json(self.job / "source_inputs.json", self.source_inputs)
                if self.task.scenario == "oneway":
                    from .hard_access import install
                    self.runtime, self.state["hard_access_profile"] = install(self.job)
                    self.event("hard_access_ready", self.state["hard_access_profile"])
                elif self.task.scenario == "circulation" and self.task.turn is not None:
                    from .turn_agent import validate_explicit_turn
                    from .turn_access import install
                    base_turns = validate_explicit_turn(self.source, self.task)
                    self.runtime, self.state["turn_access_profile"] = install(self.job, self.task.turn[1], base_turns)
                    self.event("turn_access_ready", self.state["turn_access_profile"])
                else:
                    self.runtime = setup_runtime(self.root)
                self.event("knowledge_retrieval", knowledge(self.task.scenario))
                baseline, base = self.simulation("baseline", [])
                if self.task.scenario == "circulation":
                    if self.task.turn is not None:
                        from .turn_agent import diagnose_turn as diagnose_circulation
                    else:
                        from .circulation_agent import diagnose_circulation
                    diagnosis = diagnose_circulation(self, baseline, base)
                elif self.task.scenario == "oneway":
                    from .oneway_agent import diagnose_oneway
                    diagnosis = diagnose_oneway(self, baseline, base, portfolio=bool(self.planner))
                    if self.planner and diagnosis.get('targets'):
                        from .oneway_planning import choose
                        diagnosis = choose(self, baseline, base, diagnosis)
                elif self.task.scenario == "diagnosis":
                    from .diagnostic_study import diagnose_network
                    diagnosis = diagnose_network(baseline, self.task)
                else:
                    diagnosis = diagnose(baseline, self.task)
                    if self.planner:
                        from .spatial_planning import diagnose_portfolio
                        diagnosis=diagnose_portfolio(baseline,self.task)
                        self.state['regions']=diagnosis['regions']
                save_json(self.job / "diagnosis.json", diagnosis)
                self.event("diagnosis", diagnosis)
                if self.state['status'] == 'incomplete':
                    pass  # A failed model target choice must not fall back to rule selection.
                elif self.task.scenario == "diagnosis":
                    from .diagnostic_study import analyze as analyze_diagnosis
                    analyze_diagnosis(self, baseline, base, diagnosis)
                elif not diagnosis["selected"]:
                    self.scope = scopes(baseline, [], self.task.neighbor_hops)
                    save_json(self.job / 'scopes.json', self.scope)
                    base['metrics'] = scoped_metrics(baseline, self.scope)
                    base['core_rows'] = []
                    reason = "指定范围没有同时通过瓶颈、公交和接入规则的候选，不自动换区域"
                    self.state.update(status="no_candidate", recommendation="baseline", reason=reason)
                else:
                    if self.task.scenario == "circulation":
                        if self.task.turn is not None:
                            from .turn_agent import analyze_turn as analyze_circulation
                        else:
                            from .circulation_agent import analyze_circulation
                        analyze_circulation(self, baseline, base, diagnosis)
                    elif self.task.scenario == "oneway":
                        from .oneway_agent import analyze_oneway
                        analyze_oneway(self, baseline, base, diagnosis)
                    else:
                        if self.task.search_mode == "robust" or (self.planner and len(self.task.demand_factors)>1):
                            if self.planner:
                                from .model_search import analyze
                            else:
                                from .robust_search import analyze
                            analyze(self, baseline, base, diagnosis)
                        else:
                            self.analyze(baseline, base, diagnosis)
                if input_fingerprints(self.source) != self.source_inputs:
                    raise ValueError("原始源场景输入发生变化，本次结果不得视为固定输入实验")
        except Exception as exc:
            self.state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            self.event("failure", {"error": self.state["error"]})
            raise
        finally:
            if self.task.scenario != 'diagnosis' and (self.task.recommendation_tolerance_pcu_h > 0 or (self.task.recommendation_version == 'tolerance-v2' and self.task.scenario != 'parking')):
                from .tradeoffs import selection_rule
                self.state['recommendation_rule'] = selection_rule(self.task.as_dict()) + '各需求分别选择；固定主方案仍单独复核。'
            self.state["finished"] = now()
            self.state.pop("active_scenario", None)
            self.persist()
            from .report import write_report
            write_report(self.job, self.state)
        return self.job

    def assess(self, before, after):
        if self.state.get('regions'):
            from .spatial_planning import assess
            return assess(before,after,self.scope,self.task,self.state['regions'])
        return compare(before,after,self.scope,self.task)

    def verify_repeat(self, baseline):
        repeated,_=self.simulation('baseline_repeat',[])
        identical=len(repeated)==len(baseline) and all(a[k]==b[k] for a,b in zip(baseline,repeated) for k in OUTPUTS)
        self.state['repeatability']=dict(exact_match=identical,arrays=list(OUTPUTS),row_count=len(repeated),note='确定性重复，不是独立统计样本')
        if not identical:raise ValueError('同输入复跑不一致，停止正式推荐')

    def analyze(self, baseline, base, diagnosis):
        self.scope = scopes(baseline, [r["index"] for r in diagnosis["selected"]], self.task.neighbor_hops)
        save_json(self.job / "scopes.json", self.scope)
        base["metrics"] = scoped_metrics(baseline, self.scope)
        base["core_rows"] = [baseline[i] for i in self.scope["core"]]
        if self.state.get('regions'):
            from .spatial_planning import observe
            base['spatial_observation']=observe(baseline,self.state['regions'])
        changes = [dict(from_node=r["from_node"], to_node=r["to_node"], old_parking=r["parking"], parking=2)
                   for r in diagnosis["selected"]]
        candidates = []
        seen = set()
        risk_queue = []
        risk_queue_size = 0
        reserve = 2 * (len(self.task.demand_factors) - 1) + int(self.task.repeat_baseline)
        if self.planner:
            self.state['planning_protocol'] = 'observed-planning-v3'
            if self.task.repeat_baseline:
                self.verify_repeat(baseline)
                reserve-=1
            self.persist()
            changes, _ = self.planner.parking(self, diagnosis, candidates, seen,
                                               self.task.max_runs - reserve - self.state['run_count'])
        for iteration in range(self.task.max_feedback + 1):
            if not changes:
                break
            signature = json.dumps(changes, sort_keys=True)
            if signature in seen:
                self.event("stop", {"reason": "反馈候选已计算，不重复消耗预算"})
                break
            if self.state["run_count"] >= self.task.max_runs - reserve:
                self.event("stop", {"reason": "为成对敏感性和复现预留预算"})
                break
            seen.add(signature)
            rows, record = self.simulation(f"parking_{iteration + 1}", changes)
            record["comparison"] = self.assess(baseline, rows)
            candidates.append(record)
            if iteration == 0 and not self.planner:
                risk_queue = risk_singleton_candidates(
                    record, self.task, self.task.max_runs - reserve - self.state['run_count'])
                risk_queue_size = len(risk_queue)
            if self.planner:
                self.persist()  # Make the new measured result visible before waiting for the model.
                available = max(0, self.task.max_runs - reserve - self.state['run_count']) if iteration < self.task.max_feedback else 0
                next_changes, model_decision = self.planner.parking(self, diagnosis, candidates, seen, available)
                from .choice_rationale import text as choice_reason
                reason = choice_reason(model_decision) or model_decision['reason']
            elif risk_queue:
                next_changes = risk_queue.pop(0)
                ordinal = risk_queue_size - len(risk_queue)
                reason = (f'联合方案全网毛新增过饱和超限；预算允许逐一实测入选方向，'
                          f'现试第{ordinal}/{risk_queue_size}条停车2级单路段')
            else:
                next_changes, reason = feedback(record, baseline, self.scope, self.task)
            decision = {"observed_run": record["name"], "comparison": record["comparison"]["delta"],
                        "violations": record["comparison"]["violations"], "reason": reason,
                        "next_changes": next_changes,
                        "will_execute": bool(next_changes) and iteration < self.task.max_feedback
                                        and self.state["run_count"] < self.task.max_runs - reserve
                                        and json.dumps(next_changes, sort_keys=True) not in seen}
            from .study_handoff import actions as handoff_actions
            if self.planner and model_decision.get('status')=='accepted' and model_decision.get('action') in handoff_actions(model_decision['input']['context']):
                gate, explanation = 'study_handoff', '模型已复核本组实测并选择后续研究；当前组不再新增治理候选'
            elif not next_changes:
                gate, explanation = "no_candidate", "反馈规则未提出下一候选"
            elif json.dumps(next_changes, sort_keys=True) in seen:
                gate, explanation = "already_evaluated", "下一候选已计算，不重复消耗预算"
            elif self.state["run_count"] >= self.task.max_runs - reserve:
                gate = "reserved_budget"
                explanation = f"已使用{self.state['run_count']}次，总预算{self.task.max_runs}次；为成对敏感性和基准复跑预留{reserve}次，不能继续加测"
            elif iteration >= self.task.max_feedback:
                gate, explanation = "feedback_limit", f"已达到主实验反馈上限{self.task.max_feedback}次"
            else:
                gate, explanation = "execute", "下一候选通过执行条件，将继续仿真"
            decision.update(execution_gate=gate, execution_reason=explanation)
            self.state["decisions"].append(decision)
            self.event("feedback", decision)
            self.persist()
            if not next_changes:
                break
            changes = next_changes
        winner = recommend(candidates, self.task.selection_mode)
        self.state["recommendation"] = winner["name"] if winner else "baseline"
        self.state["recommendation_rule"] = "先通过固定核心收益和邻区/全网约束，再最小化全网路段行驶时间；相同值优先少路段、较温和治理；无合格方案则保留基准"
        if self.task.recommendation_version == 'tolerance-v2':
            from .selection import record_selection
            self.state['selection_analysis'] = record_selection(candidates)
            self.state['recommendation_rule'] = self.state['selection_analysis']['rule']
        initial = recommend(candidates[:1], self.task.selection_mode)
        self.state["feedback_contribution"] = {"without_feedback": initial["name"] if initial else "baseline",
                                               "with_feedback": self.state["recommendation"],
                                               "note": "相同首轮候选范围；反馈消耗额外仿真次数，非等计算预算的消融研究"}
        if self.task.repeat_baseline and not self.planner:
            repeated, repeat_record = self.simulation("baseline_repeat", [])
            identical = all(all(a[key] == b[key] for a, b in zip(baseline, repeated)) for key in OUTPUTS)
            self.state["repeatability"] = {"exact_match": identical, "arrays": list(OUTPUTS), "row_count": len(repeated),
                                            "note": "确定性重复，不作为独立统计样本"}
            if not identical:
                raise ValueError("相同输入基准复跑输出不一致，停止正式推荐")
        selected_changes = winner["changes"] if winner else []
        for factor in self.task.demand_factors:
            if factor == 1:
                continue
            name = format(factor, ".3f").replace(".", "p")
            low_rows, low_base = self.simulation(f"demand_{name}_baseline", [], factor)
            response = low_base["execution"]["assigned_car_od_sum"] / base["execution"]["assigned_car_od_sum"]
            if not math.isclose(response, factor, rel_tol=1e-5, abs_tol=1e-6):
                raise ValueError(f"需求系数{factor}未按比例进入内部小汽车分配，实际比值{response}")
            if selected_changes:
                high_rows, high = self.simulation(f"demand_{name}_policy", selected_changes, factor)
                assessment = compare(low_rows, high_rows, self.scope, self.task)
                high["comparison"] = assessment
            else:
                assessment = compare(low_rows, low_rows, self.scope, self.task)
                assessment.update(eligible=True, violations=[], note="推荐为保留基准，无需重复相同无治理场景")
            self.state["sensitivity"].append({"car_demand_factor": factor, "assigned_od_factor_verified": response,
                                              "matched_baseline": low_base["name"], "comparison": assessment})
            self.persist()
        self.state["robust_across_tested_demand"] = all(x["comparison"]["eligible"] for x in self.state["sensitivity"])
        if not self.state["sensitivity"]:
            self.state["robust_across_tested_demand"] = None
        if self.planner:
            # The model loop never silently falls back to the legacy policy generator.
            choices = {'1.0': self.state['recommendation']}
            for item in self.state['sensitivity']:
                factor = item['car_demand_factor']
                candidate = next((r for r in self.state['runs'] if r['demand_factor'] == factor and r['changes'] == selected_changes), None)
                choices[str(float(factor))] = candidate['name'] if candidate and item['comparison']['eligible'] else item['matched_baseline']
            self.state['recommendation_by_car_demand'] = choices
        else:
            self.adapt_sensitivity()
        self.state["status"] = "completed"
        if self.planner:
            from .planning_review import finish_research
            finish_research(self)
        self.event("recommendation", {"scenario": self.state["recommendation"],
                                      "robust_across_tested_demand": self.state["robust_across_tested_demand"],
                                      "scope": recommendation_scope(self.source)})

    def adapt_sensitivity(self):
        """Use remaining budget for measured failures, retaining the original bad result."""
        original = next(r for r in self.state["runs"] if r["name"] == self.state["recommendation"])
        choices = {"1.0": self.state["recommendation"]}
        for item in self.state["sensitivity"]:
            factor = item["car_demand_factor"]
            key = str(float(factor))
            if item["comparison"]["eligible"]:
                choices[key] = next((r["name"] for r in self.state["runs"] if r["demand_factor"] == factor
                                     and r["changes"] == original["changes"]), item["matched_baseline"])
                continue
            if "adaptation" in item:
                choices[key] = item["adaptation"]["recommendation"]
                continue
            # No unchecked policy becomes a recommendation just because budget is low.
            choices[key] = item["matched_baseline"]
            if not original["changes"] or self.state["run_count"] >= self.task.max_runs:
                item["adaptation"] = {"status": "budget_stop", "recommendation": item["matched_baseline"],
                                      "reason": "没有剩余预算或可缩小的治理，当前需求条件下保留匹配基准"}
                continue
            failed = dict(original, comparison=item["comparison"])
            proposed, reason = feedback(failed, [], self.scope, self.task)
            if proposed is None:
                item["adaptation"] = {"status": "no_candidate", "recommendation": item["matched_baseline"], "reason": reason}
                continue
            self.event("sensitivity_feedback", {"car_demand_factor": factor, "violations": item["comparison"]["violations"],
                                                 "reason": reason, "next_changes": proposed})
            base_record = next(r for r in self.state["runs"] if r["name"] == item["matched_baseline"])
            base_rows = results(self.job / base_record["scenario"])
            name = "adapt_" + format(factor, ".3f").replace(".", "p")
            rows, record = self.simulation(name, proposed, factor)
            record["comparison"] = compare(base_rows, rows, self.scope, self.task)
            chosen = name if record["comparison"]["eligible"] else item["matched_baseline"]
            item["adaptation"] = {"status": "evaluated", "scenario": name, "recommendation": chosen,
                                  "comparison": record["comparison"], "changes": proposed, "reason": reason}
            choices[key] = chosen
            self.event("conditional_recommendation", {"car_demand_factor": factor, "recommendation": chosen,
                                                       "violations": record["comparison"]["violations"]})
            self.persist()
        self.state["recommendation_by_car_demand"] = choices

    @classmethod
    def adapt_completed(cls, job, source=None):
        """Append recovery experiments to a terminal, verified job; never restart live work."""
        job = Path(job).resolve()
        state = json.loads((job / "state.json").read_text(encoding="utf-8"))
        if state["status"] != "completed":
            raise ValueError("只有已完成的任务可追加敏感性反馈；运行中或失败任务不得盲目重启")
        task = Task.from_dict(state["task"])
        if task.search_mode == "robust":
            raise ValueError("主动稳健搜索按冻结协议结束；请新建任务，不向该证据链追加旧版敏感性反馈")
        agent = cls(task, source if source is not None else state.get("source", DEFAULT_SOURCE), job.parent.parent)
        agent.job, agent.job_id, agent.state = job, state["id"], state
        agent.scope = json.loads((job / "scopes.json").read_text(encoding="utf-8"))
        agent.source_inputs = json.loads((job / "source_inputs.json").read_text(encoding="utf-8"))
        with runtime_lock(agent.root):
            ensure_space([agent.task], agent.source, agent.root, include_runtime=False)
            if input_fingerprints(agent.source) != agent.source_inputs:
                raise ValueError("源输入与原实验不一致，拒绝继续该实验")
            # Verify preserved matched baselines before relying on their cached results.
            for item in state["sensitivity"]:
                record = next(r for r in state["runs"] if r["name"] == item["matched_baseline"])
                saved = json.loads((job / "logs" / record["name"] / "inputs_before.json").read_text(encoding="utf-8"))
                if input_fingerprints(job / record["scenario"]) != saved:
                    raise ValueError("已有匹配基准输入被改动，不能追加对比")
                rows = results(job / record["scenario"])
                if scoped_metrics(rows, agent.scope) != record["metrics"]:
                    raise ValueError("已有匹配基准结果与保存指标不符")
            agent.runtime = setup_runtime(agent.root)
            agent.state.setdefault("previous_completions", []).append(agent.state.pop("finished", None))
            agent.state["status"] = "running"
            agent.persist()
            try:
                agent.adapt_sensitivity()
                agent.state["status"] = "completed"
            except Exception as exc:
                agent.state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                agent.event("failure", {"error": agent.state["error"]})
                raise
            finally:
                agent.state["finished"] = now()
                agent.state.pop("active_scenario", None)
                agent.persist()
                from .report import write_report
                write_report(job, agent.state)
        return job
