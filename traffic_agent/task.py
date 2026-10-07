"""Validated task cards and deliberately bounded Chinese task interpretation."""
import math
import re
from dataclasses import asdict, dataclass, field


CAPABILITIES = {
    "diagnosis": {"state": "executable", "name": "路网现状与需求诊断", "requires": []},
    "parking": {"state": "executable", "name": "停车干扰治理", "requires": []},
    "circulation": {"state": "needs_validation", "name": "局部禁转与停车组合",
                    "requires": ["提供显式turn三节点以使用已验证的状态图组合流程；未指定转向的旧自动流程仍未通过响应验收"],
                    "supported_subflow": "显式单转向：四车型五轮、每需求S0/S1/S2/S3；各新任务仍须通过公交、绕行、实际响应和指标检查"},
    "oneway": {"state": "executable", "name": "单方向禁行与停车组合", "requires": []},
    "construction": {"state": "needs_validation", "name": "施工占道",
                     "requires": ["验证车道/容量字段与模型联动", "施工无治理专属基准", "替代路径与可达性检查"]},
    "transit": {"state": "needs_validation", "name": "公交优先",
                "requires": ["公交乘客需求与线路数据核对", "人次口径指标", "道路资源与公交运行联合验证"]},
    "signal": {"state": "needs_validation", "name": "信号与渠化",
               "requires": ["相位冲突与绿灯约束", "转向流量及控制参数联动验证", "排队估计模型边界"]},
    "staggered": {"state": "needs_validation", "name": "通勤错峰",
                  "requires": ["分时OD与跨时段需求守恒", "相邻时段损失核算", "可参与错峰OD约束"]},
}


def capability_for(task):
    if task.scenario == "circulation" and task.turn is not None:
        return {"state": "executable", "name": "显式禁转与停车组合", "requires": [],
                "evidence": "20260908_100153_bb7198：13场景、91顶层及520原生调用独立核验",
                "limits": "只处理指定非质心路口的单个新增禁转；不代表全网既有规则、自动选点或现实治理已验证"}
    return CAPABILITIES[task.scenario]


@dataclass
class Task:
    request: str = "分析停车干扰并比较治理强度"
    scenario: str = "parking"
    search_mode: str = "standard"
    backtrack_on_singleton_failure: bool = False
    recommendation_version: str = "legacy-v1"
    road_name: str | None = None
    links: list[list[int]] = field(default_factory=list)
    turn: list[int] | None = None
    max_links: int = 3
    max_runs: int = 10
    max_feedback: int = 2
    comparison_regions: int | None = None
    comparison_policies: int | None = None
    demand_factors: list[float] = field(default_factory=lambda: [0.9, 1.0, 1.1])
    repeat_baseline: bool = True
    neighbor_hops: int = 2
    target_speed_kmh: float = 25.0
    max_neighbor_time_increase_pct: float = 0.5
    max_network_time_increase_pct: float = 0.1
    max_new_oversaturated_km: float = 0.1
    max_new_network_oversaturated_km: float | None = None
    module_timeout_seconds: int = 900
    recommendation_tolerance_pcu_h: float = 0.0

    @classmethod
    def from_dict(cls, obj):
        if not isinstance(obj, dict):
            raise ValueError("任务必须为JSON对象")
        unknown = set(obj) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"未知任务字段: {sorted(unknown)}")
        obj = dict(obj)
        task = cls(**obj)
        task._absolute_tolerance_supplied = 'recommendation_tolerance_pcu_h' in obj
        if task.recommendation_version not in ('legacy-v1', 'tolerance-v2'):
            raise ValueError('recommendation_version必须为legacy-v1或tolerance-v2')
        if not isinstance(task.scenario, str) or task.scenario not in CAPABILITIES:
            raise ValueError("未知场景类型")
        if task.search_mode not in ("standard", "robust"):
            raise ValueError("search_mode必须为standard或robust")
        if type(task.backtrack_on_singleton_failure) is not bool:
            raise ValueError("backtrack_on_singleton_failure必须为布尔值")
        if task.backtrack_on_singleton_failure and (task.search_mode != "robust" or task.scenario != "parking"):
            raise ValueError("单路段失败后的回退只支持停车主动稳健搜索")
        if task.recommendation_version == 'tolerance-v2' and task.recommendation_tolerance_pcu_h != 0:
            raise ValueError('tolerance-v2与绝对pcu·h容差不能同时启用，请选择一种规则')
        if task.search_mode == 'robust' and task.recommendation_tolerance_pcu_h != 0:
            raise ValueError('跨需求主动搜索请使用tolerance-v2；绝对pcu·h容差仅支持standard模式')
        if task.search_mode == "robust" and task.scenario != "parking":
            raise ValueError("主动稳健搜索目前只支持停车治理")
        if task.search_mode == "robust" and "max_runs" not in obj:
            task.max_runs = 11
        if not isinstance(task.request, str) or not task.request.strip() or len(task.request) > 4000:
            raise ValueError("request必须为1至4000字符文本")
        if task.road_name is not None and (not isinstance(task.road_name, str) or not task.road_name.strip()):
            raise ValueError("road_name必须为非空道路名称或null")
        if task.comparison_regions is not None and (task.scenario!='parking' or type(task.comparison_regions) is not int or not 1<=task.comparison_regions<=4):
            raise ValueError('comparison_regions仅用于停车研究，必须为1至4的整数')
        if task.comparison_policies is not None and (task.scenario!='parking' or type(task.comparison_policies) is not int or not 2<=task.comparison_policies<=4):
            raise ValueError('comparison_policies仅用于停车研究，必须为2至4的整数')
        for name, lower, upper in [("max_links", 1, 10), ("max_runs", 1 if task.scenario == "diagnosis" else 2, 30), ("max_feedback", 0, 3),
                                   ("neighbor_hops", 1, 3), ("module_timeout_seconds", 10, 3600)]:
            value = getattr(task, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"{name}必须为{lower}至{upper}的整数")
        for name, lower, upper in [("target_speed_kmh", 1, 120), ("max_neighbor_time_increase_pct", 0, 100),
                                   ("max_network_time_increase_pct", 0, 100), ("max_new_oversaturated_km", 0, 100),
                                   ("recommendation_tolerance_pcu_h", 0, 100)]:
            value = getattr(task, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError(f"{name}不在允许范围")
        if 'max_new_network_oversaturated_km' in obj:
            value = task.max_new_network_oversaturated_km
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1000:
                raise ValueError('max_new_network_oversaturated_km必须为0至1000km的有限数')
        if type(task.repeat_baseline) is not bool:
            raise ValueError("repeat_baseline必须为布尔值")
        if not isinstance(task.demand_factors, list) or not 1 <= len(task.demand_factors) <= 3:
            raise ValueError("最多指定3个需求系数")
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 0.5 <= v <= 1.5 for v in task.demand_factors):
            raise ValueError("需求系数必须在0.5至1.5之间")
        if 1.0 not in task.demand_factors or len(set(task.demand_factors)) != len(task.demand_factors):
            raise ValueError("需求系数必须包含1.0且不能重复")
        if task.search_mode == "robust" and len(task.demand_factors) < 2:
            raise ValueError("主动稳健搜索至少需要两个需求系数")
        if not isinstance(task.links, list) or len(task.links) > task.max_links:
            raise ValueError("指定路段数量超出max_links")
        for pair in task.links:
            if not isinstance(pair, list) or len(pair) != 2 or any(type(v) is not int or v < 0 for v in pair) or pair[0] == pair[1]:
                raise ValueError("links必须包含[from_node,to_node]整数对")
        if len({tuple(p) for p in task.links}) != len(task.links):
            raise ValueError("指定路段不能重复")
        if task.scenario == "circulation" and len(task.links) > 1:
            raise ValueError("当前禁转组合实验只接受一个明确瓶颈路段；多点微循环尚未验证")
        if task.turn is not None:
            if (task.scenario != "circulation" or not isinstance(task.turn, list) or len(task.turn) != 3
                    or any(type(n) is not int or n < 0 for n in task.turn) or len(set(task.turn)) != 3):
                raise ValueError("turn仅用于禁转场景，必须为三个不同的非负整数节点")
            if task.links and task.links != [task.turn[1:]]:
                raise ValueError("显式禁转的停车核心为出口路段，links必须与turn后两个节点一致")
        if task.scenario == "oneway" and (len(task.links) > 1 or task.max_links < 2):
            raise ValueError("单向组合只接受一个关闭方向，max_links至少2用于双向停车对照")
        # Reserve paired sensitivity runs and repetition before spending feedback budget.
        minimum = 2 + 2 * (len(task.demand_factors) - 1) + int(task.repeat_baseline)
        if task.scenario == "circulation":
            minimum += 2  # S0 / S1 / S2 / S3, before demand pairs and repeat.
        if task.scenario == "oneway":
            minimum = 4 * len(task.demand_factors) + int(task.repeat_baseline)
            if "max_runs" not in obj:
                task.max_runs = minimum
        if task.turn is not None:
            minimum = 4 * len(task.demand_factors) + int(task.repeat_baseline)
            if "max_runs" not in obj:
                task.max_runs = minimum
        if task.scenario == "diagnosis":
            if task.links:
                raise ValueError("路网体检按道路范围诊断，不接受治理路段参数")
            minimum = len(task.demand_factors) + int(task.repeat_baseline)
        if task.max_runs < minimum:
            raise ValueError(f"完成基准、首轮治理、成对敏感性及复现至少需要{minimum}次仿真")
        return task

    def as_dict(self):
        result = asdict(self)
        # Preserve archived task/protocol bytes and their original semantics.
        if self.recommendation_version == 'legacy-v1':
            result.pop('recommendation_version')
        if self.recommendation_tolerance_pcu_h == 0 and not getattr(self, '_absolute_tolerance_supplied', False):
            result.pop('recommendation_tolerance_pcu_h')
        if self.max_new_network_oversaturated_km is None:
            result.pop('max_new_network_oversaturated_km')
        if not self.backtrack_on_singleton_failure:
            result.pop('backtrack_on_singleton_failure')
        if self.comparison_regions is None:
            result.pop('comparison_regions')
        if self.comparison_policies is None:
            result.pop('comparison_policies')
        return result

    @property
    def selection_mode(self):
        return self.recommendation_version if self.recommendation_version == 'tolerance-v2' else self.recommendation_tolerance_pcu_h


_DEMAND_NUMBER = r'\d+(?:\.\d+)?'
_DEMAND_SEPARATOR = r'(?:[,，、/]|和|及|与|\s+)'
_DEMAND_FACTORS = re.compile(
    r'需求系数\s*(?:[：:]|分别为|为|是)?\s*('
    + _DEMAND_NUMBER + r'(?:\s*' + _DEMAND_SEPARATOR + r'\s*' + _DEMAND_NUMBER + r')*)')


def request_demand_factors(text):
    """Read only explicit discrete demand factors; never infer an unstated grid."""
    match = _DEMAND_FACTORS.search(text)
    factors = None
    if match:
        if re.match(r'\s*(?:[-~～至到])\s*\d', text[match.end():]):
            raise ValueError('需求系数区间尚未接入；请明确列出离散需求系数')
        factors = [float(x) for x in re.findall(_DEMAND_NUMBER, match[1])]
    if '不做敏感性' in text:
        if factors is not None and factors != [1.0]:
            raise ValueError('不做敏感性与指定的多个或非基准需求系数冲突')
        return [1.0]
    return factors


def parse_request(text):
    """No general LLM claims: supported grammar is displayed in the UI and docs."""
    task = {"request": text}
    if re.search(r"主动稳健|稳健搜索", text):
        task.update(search_mode="robust", max_runs=11)
    families = [("diagnosis", r"路网体检|只做诊断|仅诊断"), ("construction", r"施工|占道"), ("transit", r"公交优先|公交专用"),
                ("signal", r"信号|渠化"), ("staggered", r"错峰"), ("oneway", r"单向"), ("circulation", r"微循环|绕行|转向|禁转")]
    found = [name for name, pattern in families if re.search(pattern, text)]
    if len(found) > 1:
        raise ValueError("当前请分别提交不同场景任务，不能将多个尚未验证的流程混合执行")
    task["scenario"] = found[0] if found else "parking"
    if not found and not re.search(r"停车|拥堵", text):
        raise ValueError("未识别任务类型，请使用停车/拥堵任务模板或结构化JSON")
    road = re.search(r"(?:道路|路段)[：:]\s*([^；;，,\n]+)", text)
    if road:
        task["road_name"] = road[1].strip()
    turns = re.findall(r"转向[：:][ \t]*([^；;\n]*)", text)
    if turns:
        match = re.fullmatch(r"(\d+)\s*(?:→|->)\s*(\d+)\s*(?:→|->)\s*(\d+)", turns[0].strip())
        if task["scenario"] != "circulation" or len(turns) != 1 or not match:
            raise ValueError("显式转向格式为：转向：进口节点→路口节点→出口节点；只能指定一项")
        task["turn"] = [int(n) for n in match.groups()]
    directions = re.findall(r"方向[：:][ \t]*([^；;\n]*)", text)
    if re.search(r"方向[：:]", text):
        if task["scenario"] != "oneway" or len(directions) != 1 or len(re.findall(r"方向[：:]", text)) != 1:
            raise ValueError("方向字段仅用于单向组合任务，且只能指定一个关闭方向")
        pair = re.fullmatch(r"(\d+)\s*(?:→|->)\s*(\d+)", directions[0].strip())
        if not pair:
            raise ValueError("关闭方向格式为：方向：起点→终点，例如方向：3237→3236")
        task["links"] = [[int(pair[1]), int(pair[2])]]
    count = re.search(r"最多(?:治理)?\s*(\d+)\s*条", text)
    if count:
        task["max_links"] = int(count[1])
    runs = re.search(r"预算[：:]\s*(\d+)", text)
    if runs:
        task["max_runs"] = int(runs[1])
    factors = request_demand_factors(text)
    if factors is not None:
        task["demand_factors"] = factors
    if "不复跑" in text:
        task["repeat_baseline"] = False
    return Task.from_dict(task)
