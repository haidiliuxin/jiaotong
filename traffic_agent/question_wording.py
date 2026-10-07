"""Bounded question/measurement checks, not general language entailment.

Generate the operational question from the frozen contract. Preserve original
model wording separately; absence of a known conflict is no semantic proof.
"""
import re
from .llm_client import ModelError
from .llm_settings import redact
from .hypothesis_contract import QUESTION_DEFINITIONS

LEGACY_VERSION='bounded-question-wording-v1'
PREVIOUS_VERSION='bounded-question-wording-v2'
V3_VERSION='bounded-question-wording-v3'
V4_VERSION='bounded-question-wording-v4'
V5_VERSION='bounded-question-wording-v5'
VERSION='bounded-question-wording-v6'
STRUCTURED_VERSIONS=(V3_VERSION,V4_VERSION,V5_VERSION,VERSION)
POLICIES={'all':'全部治理类型','parking':'停车措施','traffic_control':'交通组织措施','combined':'组合措施','control_and_combined':'交通组织和组合措施（不含仅停车）'}
PREDICATES={test:value['predicate'] for test,value in QUESTION_DEFINITIONS.items() if test!='unavailable'}


def separation_note(clause):
    """Recognize whole metalinguistic clauses, never embedded success claims.

    This is deliberately a small grammar. In particular, a trailing 'separate
    judgments' cannot excuse an earlier affirmative conjunction in the text.
    """
    text=re.sub(r'\s+','',clause)
    prefix=r'(?:(?:且|并|也|但))?'
    judgment=r'(?:(?:本次|本项|该项|此项|这项))?(?:(?:核心(?:总)?耗时|核心收益|收益)(?:的)?)?(?:判定|判断|检验|评估)'
    condition=r'(?:是否(?:通过|满足)(?:(?:全部|所有|原定|预定|推荐|邻区|周边|两项)){0,3}(?:约束|门槛)|是否(?:可|可以|适合)推荐|推荐资格|核心(?:总)?耗时是否(?:下降|降低|减少)|是否(?:降低|减少)核心(?:总)?耗时)'
    independent=r'(?:(?:应当|应|需|须|要))?(?:分开|分别|独立)(?:(?:进行)?(?:判定|判断|检验|评估)|进行)?'
    patterns=[prefix+judgment+r'(?:与|和)'+condition+independent,
              prefix+condition+r'(?:与|和)'+condition+independent,
              prefix+r'(?:两者|二者|这两项|两项判定)'+independent]
    return any(re.fullmatch(pattern,text) for pattern in patterns)


def assertions(text,version=VERSION):
    """Skip explicit limitations, not negative outcomes such as 不恶化."""
    for clause in re.split(r'[，,。；;\n]',text):
        clause=clause.strip()
        if not clause:continue
        if version!=LEGACY_VERSION and separation_note(clause):continue
        if re.match(r'(?:但|且|并|也|仍|这|它|本次|当前|因此|故)*\s*(?:不等于|不代表|不证明|不能|无法|不以|不要求|不讨论|不检验|尚无|不涉及|不包含|不包括|未测|不把)',clause):
            continue
        if not re.search(r'但|然而|却|仍可',clause) and re.search(r'(?:不能|无法|不足以|未能)(?:直接)?(?:证明|验证|判断|测量|观测|检验|读取)|(?:没有|缺少|尚无|未提供).{0,12}(?:排队|逐秒|因果|成因)',clause):
            continue
        yield clause


def explicit_quantifier(text,version=VERSION):
    text='；'.join(assertions(text,version))
    universal=bool(re.search(r'(?:所有|全部)(?:已试|已测|治理|停车|组合)?(?:措施|方案)',text))
    existential=bool(re.search(r'至少一(?:个|项|种)(?:已试|已测|治理|停车|组合)?(?:措施|方案)',text))
    if version in STRUCTURED_VERSIONS:
        existential |= bool(re.search(r'至少(?:在)?(?:某|任意)?一(?:个|项|种)(?:已试|已测)?(?:措施|方案)?(?:上|中)?(?:成立|满足|通过)?',text))
    if version in (V5_VERSION,VERSION):
        existential |= bool(re.search(r'(?:至少(?:存在|有)|存在至少)一(?:个|项|种)[^，,。；;\n]{0,30}?(?:方案|措施|候选)',text))
        universal |= bool(re.search(r'(?:两个|两项|两种|各个|各项|每个)(?:已试|已测|停车|治理|交通)?(?:候选|方案|措施)(?:均|都)(?:须|需|能|可)?(?:通过|满足|降低|改善)',text))
    return 'all' if universal and not existential else 'any' if existential and not universal else None


def policy_scope(text):
    """Only a complete, explicit enumeration can narrow the typed family."""
    enumerated=bool(re.search(r'(?:停车|停放)(?:治理|措施|单项|方案)?[、，,](?:单向|禁转|交通组织)(?:组织|措施|单项|方案)?(?:及|与|和|、|，|,)(?:其)?组合',text))
    if enumerated:return 'all'
    parking=bool(re.search(r'(?:停车|停放)(?:治理|措施|单项|方案)?',text))
    comparator=bool(re.search(r'(?:优于|低于|比|相对|相比于|比较)[^。；;\n]{0,12}(?:仅停车|停车单项|单项停车)',text))
    control=bool(re.search(r'单向|禁转|交通组织',text))
    combined=bool(re.search(r'组合|联合',text))
    if control and combined and (not parking or comparator):return 'control_and_combined'
    return None


def named_neighbor_km(text):
    """Read an explicit neighborhood gross overload bound, never infer one."""
    match=re.search(r'(?:邻区|周边)[^。；;\n]{0,38}?(?:毛新增|新增)[^。；;\n]{0,20}?(?:过饱和)[^。；;\n]{0,20}?(?:不超过|小于等于|≤|上限为?|门槛为?)\s*(\d+(?:\.\d+)?)\s*(?:km|公里|千米)',text,re.I)
    if not match:
        match=re.search(r'(?:邻区|周边)[^。；;\n]{0,38}?(?:毛新增|新增)[^。；;\n]{0,20}?(?:过饱和)[^。；;\n]{0,20}?(\d+(?:\.\d+)?)\s*(?:km|公里|千米)\s*(?:门槛|上限)',text,re.I)
    return float(match.group(1)) if match else None


def comparison_references(text):
    """Read the operand of an explicit core-time comparison, not background.

    Only affirmative clauses supplied by assertions() enter this parser. A
    baseline mentioned as experimental context is not the comparison operand.
    """
    parking=r'仅停车(?:措施|方案|对照)?|停车单项|单项停车'
    control=r'(?:单向|禁转|交通组织)单项(?:措施|方案)?'
    pair='(?:(?:'+parking+')[与和及、](?:'+control+')|(?:'+control+')[与和及、](?:'+parking+'))'
    references={'untreated_baseline':r'无治理(?:新)?基准|无治理对照|无措施基准|新基准',
                'parking_only':parking,
                'both_single_measures':r'(?:两个|两种|各个|全部)单项(?:措施|方案)?|'+pair}
    metric=r'核心(?:范围)?(?:总)?(?:耗时|行驶时间|时间消耗)'
    qualifier=r'(?:(?:本组|该组|同一需求|同需求|同一组|自身|对应|其|的)\s*)*'
    found=set()
    for clause in re.split(r'[，,。；;\n]',text):
        matches=[]
        for name,reference in references.items():
            operand=qualifier+'(?P<reference>'+reference+')'
            patterns=[metric+r'.{0,16}?(?:低于|小于|少于)\s*'+operand,
                      r'(?:相比于|相对于|相比|相对|比)\s*'+operand+r'.{0,12}?'+metric+r'.{0,12}?(?:更低|更少|降低|下降|都低)']
            matches.extend((name,*m.span('reference')) for pattern in patterns for m in re.finditer(pattern,clause))
        # A coordinated operand contains each single name. Keep the complete
        # comparison object, never collapse the pair to its parking substring.
        found.update(name for name,start,end in matches if not any(
            other_start<=start and end<=other_end and other_end-other_start>end-start
            for _,other_start,other_end in matches))
    return found


def definition_conflicts(hypothesis,evaluation,version):
    """Check both public fields against the same typed comparison and limits."""
    definition=QUESTION_DEFINITIONS[evaluation['test']]
    found=[]
    for field in ('hypothesis','evidence_needed'):
        text='；'.join(assertions(hypothesis[field],version))
        if definition['metric']=='core_running_time':
            for reference in sorted(comparison_references(text)):
                if reference!=definition['comparator']:
                    found.append(dict(code='comparison_reference_changed',field=field,
                        expected_comparator=definition['comparator'],claimed_comparator=reference,
                        message='所写核心耗时比较的参照对象与冻结判据不同：无治理基准、仅停车和两个单项不能互换。'))
        if evaluation['test']=='neighbor_limits':
            time=re.search(r'(?:邻区|周边)[^。；;\n]{0,20}?(?:耗时|行驶时间)(?:增幅|变化)?[^。；;\n]{0,12}?(?:不超过|小于等于|≤|上限为?|门槛为?)\s*(\d+(?:\.\d+)?)\s*%',text)
            for key,claimed in (('max_neighbor_time_increase_pct',float(time.group(1)) if time else None),
                                ('max_new_oversaturated_km',named_neighbor_km(text))):
                limits=[s[key] for s in evaluation['scope'] if key in s]
                if claimed is not None and any(abs(claimed-limit)>1e-9 for limit in limits):
                    found.append(dict(code='frozen_neighbor_threshold_changed',field=field,metric=key,
                        claimed_value=claimed,frozen_values=limits,
                        message='原问题或所需证据写出的邻区门槛与各组冻结配置不同，不能用另一个阈值回答。'))
    return found


def conflicts(hypothesis,evaluation,all_roads=(),version=VERSION):
    if version not in (LEGACY_VERSION,PREVIOUS_VERSION,*STRUCTURED_VERSIONS):raise ModelError('未知的问题口径校验协议')
    test=evaluation['test']
    if test=='unavailable':return []
    text='；'.join(assertions(hypothesis['hypothesis'],version))
    evidence='；'.join(assertions(hypothesis['evidence_needed'],version))
    issues=[]
    def add(code,message):issues.append(dict(code=code,message=message))
    if version in STRUCTURED_VERSIONS:
        named_scope=policy_scope(text)
        if named_scope and test!='combination_core_advantage' and evaluation['policies']!=named_scope and not (test=='core_advantage_over_parking' and evaluation['policies']=='all'):
            add('policy_scope_changed','原问题列出的措施类型与判据对象不一致，不能漏掉仅停车或添加未要求的措施。')
    else:
        control_pair=bool(re.search(r'(?:单向|交通组织)(?:组织|措施|单项)?(?:（含组合）|\(含组合\)|或组合|及组合)',text))
        if control_pair and evaluation['policies']!='control_and_combined' and not (test=='core_advantage_over_parking' and evaluation['policies']=='all'):
            add('policy_scope_changed','原问题只问交通组织及组合，判据对象不能加入仅停车或漏掉其中一类。')
    if re.search(r'排队(?:长度|车辆数|时间|缩短|减少|消除)?|逐秒|动态回堵|因果|成因|双向冲突|协同(?:机制|效应)|(?:效益|收益)上限|零风险|全局最优',text+'；'+evidence):
        add('unavailable_measurement','批次指标不能直接检验排队、成因、风险或效益上限；保留unavailable或另列明确的可测问题，不能替换原命题。')
    core=bool(re.search(r'(?:核心|通行|行驶|路段).{0,8}(?:耗时|时间|代价).{0,8}(?:降|减|改善|节约)|(?:降|减|改善|节约).{0,8}(?:核心|通行|行驶|路段).{0,8}(?:耗时|时间|代价)',text))
    constraint=bool(re.search(r'(?:通过|满足).{0,8}(?:约束|门槛)|(?:可|适合)推荐|推荐资格|全部约束|(?:邻区|周边).{0,12}(?:不恶化|不回退|不产生|无外溢|无回退|可接受|门槛|约束)',text))
    if version in (V4_VERSION,V5_VERSION,VERSION):
        constraint |= bool(re.search(r'(?:约束|门槛).{0,12}(?:通过|满足)',text))
    if test in ('core_time_decrease','core_advantage_over_parking','combination_core_advantage') and constraint:
        add('benefit_and_constraint','核心耗时判据不包含推荐资格或周边约束；复合问题须拆开，不能用一个指标回答两个条件。')
    if test=='admissible' and core and not constraint:
        add('constraint_substitutes_benefit','原文仅问耗时是否下降，不能用全部推荐约束替代核心收益判据。')
    if test=='neighbor_limits' and core:
        add('neighbor_substitutes_core','邻区门槛不能同时检验核心收益；需保留分别的问题。')
    if version in STRUCTURED_VERSIONS:
        response=bool(re.search(r'(?:原生|禁行|禁转|单向|控制).{0,12}(?:响应|生效|落实|执行到位)|(?:响应|生效).{0,8}(?:禁行|禁转|单向|控制)',text))
        if response and test=='admissible' and not constraint:
            add('control_substitutes_recommendation','原问题只问控制是否生效，不能用推荐资格替代控制响应。')
        if test=='control_response' and (core or constraint):
            add('control_response_mixed','控制响应须与核心收益、推荐资格分别检验。')
        named_km=named_neighbor_km(text)
        if named_km is not None and test=='neighbor_limits':
            limits=[s['max_new_oversaturated_km'] for s in evaluation['scope'] if 'max_new_oversaturated_km' in s]
            if limits and any(abs(named_km-limit)>1e-9 for limit in limits):
                add('neighbor_threshold_changed','原问题明确的邻区过饱和门槛与任务实际配置不一致；不能按另一个数值回答。')
    if test=='core_time_decrease' and re.search(r'(?:比|相对|相比于).{0,3}(?:仅停车|停车单项|单项停车)',text):
        add('comparator_changed','相对仅停车的额外收益不能改用无治理基准判断。')
    if test=='core_advantage_over_parking' and re.search(r'(?:两个|两种|各个|全部)单项',text):
        add('missing_single_comparator','仅停车不是两个单项；要比较组合与两个单项，须使用组合双单项判据。')
    if test=='neighbor_limits' and re.search(r'(?:邻区|周边).{0,8}(?:完全不恶化|完全不回退|无任何(?:影响|代价)|零(?:代价|增幅|增长))',text):
        add('threshold_is_not_zero','通过原定邻区门槛不等于严格零代价；不能以容许增幅的判据回答零增长命题。')
    quantifier=explicit_quantifier(hypothesis['hypothesis'],version)
    if quantifier and evaluation['quantifier']!=quantifier:
        add('quantifier_changed','假设中的“所有”与“至少一个”必须沿用，不能更换量词。')
    if version in (V5_VERSION,VERSION):
        evidence_quantifier=explicit_quantifier(hypothesis['evidence_needed'],version)
        if evidence_quantifier and evidence_quantifier!=evaluation['quantifier']:
            add('evidence_quantifier_changed','所需证据中的量词与冻结判据不一致，不能以所有候选代替至少一个或反之。')
    if test.startswith('core_') or test=='combination_core_advantage':
        if re.search(r'(?:整个|全)(?:路网|城市)|整条道路|全路段|全网(?:总)?(?:耗时|通行)',text):
            add('core_is_not_whole_network','冻结核心仅含本次选定评价方向，不能替代整条道路或全网效果。')
    scoped={s['road'] for s in evaluation['scope'] if s.get('road')}
    mentioned={r for r in all_roads if r and r in text}
    if mentioned-scoped:add('road_scope_changed','假设提到的道路没有包含在判据研究组内。')
    factors={float(x) for s in evaluation['scope'] for x in s['demand_factors']}
    named={float(m.group(1)) for m in re.finditer(r'需求(?:系数)?\s*[×x:=：]?\s*(\d+(?:\.\d+)?)',text)}
    if named-factors:add('demand_scope_changed','假设点名的需求系数没有安排在判据对应研究中。')
    if version==VERSION:issues.extend(definition_conflicts(hypothesis,evaluation,version))
    return issues


def interpretation(hypothesis,evaluation,all_roads=(),version=VERSION):
    generated='authoring' in hypothesis
    if generated:
        from .question_authoring import verify_row
        verify_row(hypothesis)
        if version!=VERSION:raise ModelError('结构化生成问题须使用当前冻结问题协议，不能套用旧记录协议')
    # Only compiler output that exactly reconstructs from the saved definition
    # avoids the prose heuristic. Model-authored free text still uses it.
    issues=[] if generated else conflicts(hypothesis,evaluation,all_roads,version)
    if issues:raise ModelError('假设与事前判据冲突：'+'；'.join(i['message'] for i in issues))
    test=evaluation['test']
    scope='；'.join(f"第{s['study_id'].split('_')[-1]}组（{s.get('road') or '全网候选搜索范围'}），需求×{'/'.join(f'{x:g}' for x in s['demand_factors'])}"
                   +(f"，邻区耗时增幅≤{s['max_neighbor_time_increase_pct']:g}%，毛新增过饱和≤{s['max_new_oversaturated_km']:g}km"
                     if version in STRUCTURED_VERSIONS and test=='neighbor_limits' and 'max_new_oversaturated_km' in s else '')
                   for s in evaluation['scope'])
    question=('本接口不能直接判定原问题，保留证据缺口。' if test=='unavailable' else
              f"逐组独立检验：{scope}。每组{POLICIES[evaluation['policies']]}中{'至少一个' if evaluation['quantifier']=='any' else '所有'}已试措施，是否在每个约定需求下{PREDICATES[test]}？")
    if test in ('core_advantage_over_parking','control_response') and evaluation['policies']=='all':
        question=question.replace('全部治理类型','交通组织和组合措施')
    value=dict(version=version,question=question,wording_check='not_measurable' if test=='unavailable' else 'no_explicit_conflict',
        boundary='只检查明确的指标、条件、量词及范围冲突；未发现冲突不是任意文字的语义认证。实际回答上面明确列出的问题，原始模型提问保留供核对。',
        limits=['各组独立与自身新基准比较，不共享不同引擎的效果。',
                '核心指标仅适用于本次冻结的评价方向，不自动覆盖整条道路。',
                '只涉及本次已试措施与离散需求，不代表全局最优或实时变化。'])
    if version!=LEGACY_VERSION:
        notes=[dict(source=key,kind='separate_criteria',text=redact(clause.strip()))
               for key in ('hypothesis','evidence_needed') for clause in re.split(r'[，,。；;\n]',hypothesis[key])
               if separation_note(clause)]
        if notes:value['wording_notes']=notes
    if version in STRUCTURED_VERSIONS:
        value['contract']=dict(test=test,policies=evaluation['policies'],quantifier=evaluation['quantifier'],
                               scope=[dict(s) for s in evaluation['scope']])
    if generated:
        value.update(origin='model_structured_definition',wording_check='generated_from_definition',
            boundary='本问题由模型选择的结构化定义生成；原用户输入和模型研究理由另行保留。判据通过不证明现实因果，也不代表未测措施已被检验。')
    return value
