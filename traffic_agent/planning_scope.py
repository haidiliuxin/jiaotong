"""Known execution prerequisites, not a predicted traffic outcome or model plan."""
import re

from .llm_client import ModelError

VERSION='planning-scope-v3'
PREREQUISITE_VERSION='planning-prerequisites-v4'

UNSUPPORTED_TERMS = {
    'construction': r'施工|施工占道|道路封闭',
    'signal': r'信号|配时|绿灯|相位',
    'transit': r'公交优先|公交专用|公交线路|公交运行|公交乘客',
    'staggered': r'错峰|分时OD|通勤时段',
    'other': r'限速|收费|潮汐车道|匝道控制|货车限行',
}


def unsupported_capabilities(request):
    offered=[]
    for clause in re.split(r'[。；;\n]',request):
        if re.search(r'不(?:用|要|必|需要)?(?:做|实施|考虑|研究)?(?:施工|信号|配时|公交优先|错峰|限速|收费)',clause):
            continue
        offered.extend(code for code,pattern in UNSUPPORTED_TERMS.items() if re.search(pattern,clause,re.I))
        if ('单向' in clause and '禁转' in clause and re.search(r'同时|叠加|联合',clause)):
            offered.append('combined_oneway_turn')
    return list(dict.fromkeys(offered))

_LOCATION=re.compile(r'学校|医院|园区|场馆|大道|公路|道路|路口|街|巷|路|[东西南北](?:向|侧|边)|附近|周边|门口')
_GENERIC_LOCATIONS=('案例道路','案例路网','整个案例路网','全路网','路网','周边影响','周边代价','周边耗时')
_NEIGHBOR_EFFECT=re.compile(r'周边(?:的)?(?:影响|代价|耗时|恶化|外溢|负荷|新增过饱和|速度|收益)')
_QUANTIFIED_ROADS=re.compile(r'(?:[0-9一二两三四五六七八九十百]+条|每条?|各条?|所有|全部)(?:道路|路段)')
_RESOLVED_REFERENCES=re.compile(r'(?:这些|上述|所述|已匹配)(?:的)?(?:道路|路段)')
_SINGULAR_RESOLVED_REFERENCE=re.compile(r'(?:该|这条|这段|此)(?:道路|路段|路)')
_ROAD_PROPERTIES=re.compile(r'道路名称|道路指标|路段状态|路段指标|路段流量|路段速度|路侧停车|路侧干扰|路权')
_TURN=re.compile(r'禁转|禁止.{0,4}转|转向|微循环')
_NEGATED_TURN=re.compile(r'(?:不|无需|无须|别)[^，。；;]*(?:禁转|转向|微循环)')


def location_anchors(request,catalog,explicit):
    """Unresolved location markers, excluding spans resolved by this topology.

    This is the same bounded location grammar accepted by validate_questions,
    not a geocoder or an assertion that arbitrary place names are resolved.
    Generic traffic effects do not constrain the study's geographic scope.
    """
    if explicit.get('links') or explicit.get('turn'):return []
    resolved=[m.span() for text in (*catalog['matched_roads'],*_GENERIC_LOCATIONS)
              for m in re.finditer(re.escape(text),request)]
    resolved.extend(m.span() for m in _NEIGHBOR_EFFECT.finditer(request))
    resolved.extend(m.span() for m in _QUANTIFIED_ROADS.finditer(request))
    resolved.extend(m.span() for m in _ROAD_PROPERTIES.finditer(request))
    if catalog['matched_roads']:
        resolved.extend(m.span() for m in _RESOLVED_REFERENCES.finditer(request))
    if len(catalog['matched_roads'])==1:
        resolved.extend(m.span() for m in _SINGULAR_RESOLVED_REFERENCE.finditer(request))
    return [m.span() for m in _LOCATION.finditer(request)
            if not any(start<=m.start() and m.end()<=end for start,end in resolved)]


def turn_gap(request,catalog,explicit):
    if explicit.get('turn') or any(x['kind']=='turn' for x in catalog['targets'].values()):return False
    for clause in re.split(r'[，。；;\n]',request):
        if _TURN.search(clause) and not _NEGATED_TURN.search(clause):
            return True
    return False


def decision_permissions(request,catalog,explicit):
    anchors=location_anchors(request,catalog,explicit)
    clauses=list(re.finditer(r'[^，。；;\n]+',request))
    def quoted_fragment(start,end):
        clause=next(m for m in clauses if m.start()<=start and end<=m.end())
        left=max(clause.start(),start-45);right=min(clause.end(),left+100)
        return request[left:right].strip()
    # A lone suffix such as “路” supplies no concrete place to clarify.
    quotes=list(dict.fromkeys(q for a in anchors if len(q:=quoted_fragment(*a))>=2))
    turn_quotes=list(dict.fromkeys(quoted_fragment(m.start()+t.start(),m.start()+t.end())
                     for m in clauses if not _NEGATED_TURN.search(m.group())
                     for t in _TURN.finditer(m.group())))
    needs=[]
    if quotes:needs.append('unresolved_location')
    if turn_gap(request,catalog,explicit):needs.append('turn_target')
    unsupported=unsupported_capabilities(request)
    return dict(version=PREREQUISITE_VERSION,clarification_needs=needs,
                unsupported_capabilities=unsupported,
                clarification_quotes={k:(quotes if k=='unresolved_location' else turn_quotes) for k in needs},
                intents=['execute',*(['needs_input'] if needs else []),*(['unsupported'] if unsupported else [])],
                basis='只为原文中尚未解析的具体定位或必需禁转目标开放追问；已解析道路和全网自动选点不缺道路名称。',
                plan_selection='研究、策略组合、假设与取舍仍由模型提出；位置与等级在新基准完成后选择。')


class PrerequisiteError(ModelError):
    """An incorrect prerequisite with concrete, locally checkable feedback."""


def turn_mentioned(request):
    """Conservative scope boundary, including explicit and negated turn terms."""
    return _TURN.search(request) is not None


def execution_scope(config, catalog, comparisons):
    roads=catalog['matched_roads']
    factors=config['demand_factors']
    regions=comparisons['regions_per_parking_group']
    levels=2 if comparisons['strength_comparison'] else 1
    turn_refs=[ref for ref,value in catalog['targets'].items() if value['kind']=='turn']
    # This cost is conditional on selecting parking at these demand factors.
    # More/fewer model-proposed groups are still compiled and checked separately.
    candidate_count=max(regions*levels,comparisons.get('research_depth',{}).get('minimum_candidates',1))
    runs_per_group=len(factors)*(1+candidate_count)+int(config['repeat_baseline'])
    return dict(version=VERSION,
        search_scope='named_roads' if roads else 'whole_dataset',roads=roads,
        target_resolution=dict(
            diagnosis=dict(mode='fresh_baseline',road_name_required=False,target_ref='auto'),
            parking=dict(mode='model_after_fresh_baseline',road_name_required=False,target_ref='auto'),
            oneway=dict(mode='model_after_fresh_baseline',road_name_required=False,target_ref='auto'),
            turn=dict(mode='validated_topology_reference',available_refs=turn_refs)),
        candidate_limit=dict(max_changed_directed_links=config['max_links'],applies_to='each_independent_candidate',
                             not_a_road_name_requirement=True),
        regional_design=dict(regions_per_parking_group=regions,levels_per_region=levels,
                             minimum_distinct_candidates_per_parking_group=candidate_count,
                             independent_candidates=True,shared_fresh_baseline_per_demand=True),
        parking_cost=dict(demand_factors=factors,groups_for_named_roads=max(1,len(roads)),
                          minimum_runs_per_group=runs_per_group,
                          minimum_total_runs=runs_per_group*max(1,len(roads)),
                          total_run_budget=config['total_runs'],
                          conditions='仅适用停车、上述需求和原文要求的区域/强度比较；完整计划由程序重新核算'),
        candidate_availability='unknown_until_new_baseline',
        scope_note='未指定道路时在整个案例路网建立新基准，再由模型从合法候选中选点；没有候选则如实停止。',
        limit_note=f"每个独立候选最多改变{config['max_links']}条有向路段；比较不同区域是独立实验，不要求同时改变多个区域。")


def validate_questions(questions, request, catalog, explicit):
    """Reject unsupported prerequisites; never fill in a plan or issue a retry.

    A cited unresolved place remains a model interpretation, not verified
    geocoding. These checks establish a real user quote and reject claims that
    contradict supported automatic selection or already resolved topology.
    """
    permissions=decision_permissions(request,catalog,explicit)
    for item in questions:
        quote=item['source_quote'].strip()
        if len(quote)<2 or len(quote)>100 or quote not in request:
            raise PrerequisiteError('模型追问没有引用用户原文中的具体定位条件；未要求用户补充、未执行仿真')
        # Enforce the actual request-specific schema even if the provider
        # ignores enum constraints. A matched road cannot borrow a different
        # unresolved place's permission; a genuine mixed clause remains valid.
        if quote not in permissions['clarification_quotes'].get(item['need'],[]):
            raise PrerequisiteError('该追问不对应本次开放的定位缺口；只能引用实际未解析的原文片段，不能重问已知道路或自动选点，未执行仿真')
