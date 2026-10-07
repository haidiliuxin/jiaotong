"""Executable study designs, separate from the three native intervention primitives."""

STUDIES = {
    "diagnosis": "路网体检",
    "standard": "策略对照",
    "demand_stress": "需求压力检验",
    "robust": "跨需求稳健搜索",
    "spillover": "邻区保护与回退",
}
DEMAND_PROFILES = {"base": [1.0], "variation": [0.9, 1.0, 1.1], "surge": [1.0, 1.2]}
UNIT_VERSION='complete-study-unit-v3'


def study_units(config,comparisons):
    """Cost and dependency semantics of one complete study, before any model call."""
    n=len(config['demand_factors']);repeat=int(config['repeat_baseline'])
    requested=comparisons.get('research_depth',{}).get('minimum_candidates',1)
    parking=1+max(requested,comparisons['regions_per_parking_group']*(2 if comparisons['strength_comparison'] else 1))
    units=[]
    for strategies,cells,engine in [([],['新基准诊断'],'Vehicle'),
            (['parking'],['新基准','全部必需停车候选'],'Vehicle'),
            (['parking','oneway'],['S0 新基准','S1 停车','S2 单向','S3 停车＋单向'],'Vehicle1'),
            (['parking','turn'],['S0 新基准','S1 停车','S2 禁转','S3 停车＋禁转'],'Vehicle1')]:
        count=1 if not strategies else parking if len(strategies)==1 else 4
        units.append(dict(strategies=strategies,cells=cells,assignment=engine,
            native_scenes_at_given_factors=n*count+repeat,complete_within_one_group=True,
            uses_other_group_baseline_or_target=False))
    depth=comparisons.get('research_depth',{})
    required_design=None
    if depth.get('candidate_domain')=='parking' and depth.get('minimum_candidates',1)>1:
        required_design=dict(kind='independent_parking_candidate_comparison',
            strategies=['parking'],minimum_distinct_treatments=depth['minimum_candidates'],
            minimum_native_scenes_at_given_factors=n*parking+repeat,
            combination_group_counts_toward_this_requirement=False,
            location_and_strength_selected_after_fresh_baseline=True)
    return dict(version=UNIT_VERSION,demand_factors=config['demand_factors'],repeat_baseline=config['repeat_baseline'],units=units,
        required_design=required_design,
        rule='一个停车组可在同需求新基准下顺序实测多个不同候选：比较两个方案只需一份基准加两个治理场景，不需要两个停车组；具体位置和等级在新基准后由模型决定。组合组自己建立基准并测齐S0/S1/S2/S3。不同组不共享仿真结果；需求、组数变化后由程序重新核算成本。')

CATALOG = [
    dict(id="diagnosis", name="路网体检", study="diagnosis", strategies=[],
         use="先找拥堵位置或只分析现状", execution="新基准诊断全部指定道路，按实测耗时排序，不改变治理参数"),
    dict(id="parking", name="停车强度优化", study="standard", strategies=["parking"],
         use="路侧停车、通行干扰、治理强度取舍", execution="新基准后由模型选择停车等级及方向，读取新结果再决定后续候选"),
    dict(id="oneway", name="单向组织对照", study="standard", strategies=["parking", "oneway"],
         use="比较双向通行与单向组织", execution="模型依据新基准与公交/绕行检查选择一个方向，再完整比较停车、单向、组合"),
    dict(id="turn", name="路口禁转对照", study="standard", strategies=["parking", "turn"],
         use="指定道路的局部转向组织", execution="已校验转向的停车、禁转、组合四方案及四车型响应检查"),
    dict(id="demand_stress", name="需求压力检验", study="demand_stress", strategies=["parking"],
         use="高峰、需求增加、客流活动的假设评估", execution="小汽车OD离散缩放，每个需求有自己的无治理基准；不是分时客流预测"),
    dict(id="robust", name="跨需求稳健搜索", study="robust", strategies=["parking"],
         use="找出需求波动时仍适用的方案", execution="各需求先建立新基准，模型选择措施并测齐全部需求；再依据完整结果决定缩小、增强或换方向"),
    dict(id="spillover", name="邻区保护与回退", study="spillover", strategies=["parking"],
         use="担心拥堵转移，优先保护周边", execution="3跳邻区，全网毛新增过饱和上限0km，预算内逐一检验核心方向"),
]


def minimum_runs(task):
    from .planning_review import required_policies
    count = len(task["demand_factors"])
    multiplier = 1 if task["scenario"] == "diagnosis" else 2 if task["scenario"] == "parking" else 4
    if task['scenario']=='parking' and task.get('completion_required'):
        multiplier=1+required_policies(task)
    return multiplier * count + int(task["repeat_baseline"])


def execution_design(task):
    """Only describe what the executor actually does; model prose grants no new action."""
    factors = task.demand_factors
    diagnostic = task.scenario == "diagnosis"
    stages = [dict(title="建立本次基准", detail="校验源数据并新建仿真副本，运行完整TranStar分配，读取实际速度、流量与V/C。"),
              dict(title="定位与冻结范围", detail=("按所述范围的实测路段耗时排序，汇总道路与拥堵指标。" if diagnostic else
                   "在新基准中筛选候选，冻结核心、邻区和全网；没有候选就保留诊断，不更换研究道路。"))]
    if diagnostic:
        matrix = ["无治理基准"]
        recovery = "只保存观测和诊断，不执行停车、单向或禁转。未发现拥堵也如实报告。"
    elif task.search_mode == "robust":
        matrix = ["各需求无治理基准", "同一停车候选在全部需求配对", "预算内反馈候选"]
        stages.append(dict(title="观测驱动搜索", detail="从高需求开始；违反约束则缩小治理范围，单路段失败可按基准顺序回退；合格但仍有瓶颈才增强停车等级。"))
        recovery = "淘汰违反任一需求约束的统一候选；缺测不补推，预算不足不启动新的整组验证；无完整合格措施则保留基准。"
    elif task.scenario == "parking":
        matrix = ["无治理基准", "停车2级", "依据实测决定增强或缩小范围"]
        stages.append(dict(title="实测后调整", detail=("先测联合治理；全网毛新增超限且预算足够时逐一实测入选方向，否则缩小范围；" if task.max_new_network_oversaturated_km is not None else "停车2级起步；") + "通过约束但仍有瓶颈才试更强等级。"))
        recovery = "邻区/全网约束或核心收益失败时缩小范围；单路段仍失败则停止扩大治理，保留基准或已测合格措施。"
    else:
        action = "单向" if task.scenario == "oneway" else "禁转"
        matrix = ["S0 无治理", "S1 停车", f"S2 {action}", f"S3 停车＋{action}"]
        stages.append(dict(title="完整单项与组合对照", detail=f"每个需求计算S0/S1/S2/S3，核对{action}的实际车型响应；组合不一定比单项好。"))
        recovery = "公交、绕行、接入或实际响应不通过时拒绝候选；指标不合格不推荐，没有合格方案则保留本组基准。"
    if len(factors) > 1:
        stages.append(dict(title="需求条件复核", detail=f"小汽车OD系数 {' / '.join(map(str, factors))}；其他车型保持原数据。只在同需求、同组基准下比较。"))
    if task.repeat_baseline:
        stages.append(dict(title="同输入复现", detail="复跑需求1.0基准并逐数组核对；不一致时停止形成正式结论。"))
    stages.append(dict(title="形成结论与证据", detail="保存原生输出、输入差异、失败及负收益结果；报告结论的适用范围。"))
    criteria = (["仅作现状诊断，不给出未经治理实验验证的收益"] if diagnostic else [
        "核心区路段行驶时间必须改善",
        f"邻区耗时增幅≤{task.max_neighbor_time_increase_pct:g}%，全网≤{task.max_network_time_increase_pct:g}%",
        f"邻区毛新增过饱和有向里程≤{task.max_new_oversaturated_km:g}km",
        "通过硬约束后，按容差规则优先选择效果相近且措施较少的方案",
    ])
    if task.max_new_network_oversaturated_km is not None:
        criteria.insert(3, f"全网毛新增过饱和有向里程≤{task.max_new_network_oversaturated_km:g}km")
    if task.scenario == "oneway":
        criteria[1] = f"邻区耗时增幅≤{task.max_neighbor_time_increase_pct:g}%，且全网耗时严格改善"
    return dict(stages=stages, matrix=matrix, factors=factors, criteria=criteria, recovery=recovery,
                stop="原生执行失败、源输入变化或复现不一致时停止后续调度；无自动重试。")
