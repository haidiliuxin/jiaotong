"""Compile validated study proposals without changing research obligations.

Only structurally equivalent proposals may share a fresh parking baseline.
The compiler never invents a target, treatment, model judgment or simulation.
"""
import copy
import json
import re

from .llm_client import ModelError
from .llm_settings import redact
from .planning_review import strength_requested
from .spatial_requirements import required_regions

VERSION='plan-compilation-v3'
DIAGNOSIS_INTENT_VERSION='diagnosis-intent-v1'


def diagnosis_constraints(request,roads=()):
    """Protect explicit observation-only scopes without banning later research.

    Road names come from the actual local catalog. These are restrictions on a
    model proposal, not a strategy selector or a replacement model response.
    """
    only=r'(?:只(?:想|要|需)?|仅)(?:先)?(?:做|进行)?(?:诊断|分析|体检|查看|看看)'
    no_control=r'不(?:要|用|必)?(?:执行|进行|开展)?治理|暂不(?:执行|进行)?治理|治理(?:暂不执行|待确认)'
    first=r'先(?:做个|做|了解|看看|查看)?(?:体检|诊断|现状(?:分析)?|路况)'
    follow=r'(?:再|随后|接着|然后|之后)[^。；;，,\n]{0,20}(?:停车|单向|禁转|治理)'
    known=sorted(set(roads));parts=[];inherited=[]
    for match in re.finditer(r'[^。；;，,\n]+[。；;，,\n]?',request):
        clause=match.group();named=[road for road in known if road in clause]
        scope=named or (inherited if not re.search(r'本次|全部|所有道路|整个|全网',clause) else [])
        # Negating "only diagnose" is not a ban on interventions.
        clean=re.sub(r'(?:不(?:要|用|必|想|应)?|别|勿)(?:只|仅)(?:做|进行)?(?=诊断|分析|体检|查看|看看)','',clause)
        parts.append(dict(quote=clause.rstrip('。；;，,\n'),clean=clean,roads=scope,named=named))
        inherited=scope if clause.endswith(('，',',')) else []
    global_only=False;restricted=set();quotes=[]
    for i,part in enumerate(parts):
        hard=re.search(only+'|'+no_control,part['clean']);soft=re.search(first,part['clean'])
        if not hard and not soft:continue
        continuation=False
        if not hard:
            for j,later in enumerate(parts[i:],i):
                if j>i and part['roads'] and later['named'] and not set(later['named'])<=set(part['roads']):break
                tail=later['clean'][soft.end():] if j==i else later['clean']
                if re.search(follow,tail) and not re.search(r'不|别|勿|待确认|等我|待我|我确认|人工确认|批准|同意后|再说|暂缓',tail):
                    continuation=True;break
        if continuation:continue
        if part['roads']:restricted.update(part['roads'])
        else:global_only=True
        quotes.append(dict(source_quote=redact(part['quote']),roads=part['roads']))
    return dict(version=DIAGNOSIS_INTENT_VERSION,only=global_only or bool(known and set(known)<=restricted),
                roads=sorted(restricted),source_quotes=quotes,
                rule='明确只诊断的范围不能安排治理；先观察再开展明确要求的治理研究，不等于只诊断。不同道路的限制分别保留。')


def requirements(request,matched_roads,diagnosis_only):
    from .study_depth import from_request
    depth=from_request(request)
    areas=required_regions(request)
    per_group=1 if len(matched_roads)>1 else areas
    strength=strength_requested(request)
    return dict(regional_comparison=areas,regions_per_parking_group=per_group,
                strength_comparison=strength,diagnosis_only=diagnosis_only,
                research_depth=depth,
                parking_groups='按指定道路分别研究' if len(matched_roads)>1 else '一个停车组内根据新基准选择不同候选区',
                required_parking_policies_per_demand=per_group*(2 if strength else 1),
                baseline_sharing='同一停车组各需求只建一次新基准；不同适配器不共享',
                note='区域数量是后续实际治理位置的数量，不是重复建立相同自动诊断组。')


def normalize(tasks,entries,minimum_parking_candidates=1):
    """Consolidate equivalent proposals when the requested comparison survives.

    An unrequested diagnosis can share its matching parking baseline. Identical
    parking proposals can share one candidate or multi-area study. Distinct roads, controls,
    demand grids or study methods remain distinct; all other duplicates fail.
    """
    sources=copy.deepcopy(entries)
    redundant={i:next((j for j,p in enumerate(tasks) if p['scenario']=='parking'
                       and p.get('road_name')==t.get('road_name')
                       and set(t['demand_factors'])<=set(p['demand_factors'])),None)
               for i,t in enumerate(tasks) if t['scenario']=='diagnosis'
               and not re.search(r'体检|诊断|现状',t.get('request',''))}
    redundant={i:j for i,j in redundant.items() if j is not None}
    selected=[];compiled=[];seen={};actions=[];notes=[]
    for i,(task,entry) in enumerate(zip(tasks,entries)):
        if i in redundant:
            actions.append(dict(kind='diagnosis_baseline',source_study=i+1,retained_source_study=redundant[i]+1))
            continue
        # Exact compiled conditions plus declared method; prose is not identity.
        signature=json.dumps([task,entry['study'],entry['target_ref']],sort_keys=True,ensure_ascii=False)
        if signature in seen:
            index=seen[signature];kept=compiled[index]
            if task['scenario']=='parking' and (task.get('comparison_regions',1)>1 or minimum_parking_candidates>1):
                kept['source_studies'].append(i+1)
                actions.append(dict(kind='regional_parking' if task.get('comparison_regions',1)>1 else 'candidate_parking',
                                    source_study=i+1,retained_source_study=kept['source_studies'][0],
                                    **({'regions':task['comparison_regions']} if task.get('comparison_regions',1)>1 else
                                       {'minimum_candidates':minimum_parking_candidates})))
                continue
            label={'parking':'停车研究','oneway':'单向对照','circulation':'禁转对照','diagnosis':'路网体检'}[task['scenario']]
            raise ModelError(f"模型重复安排相同实验（第{kept['source_studies'][0]}、{i+1}组：{label}，{redact(task.get('road_name') or '全网')}）；没有可合并且保留目标的执行设计")
        seen[signature]=len(selected)
        selected.append(task)
        compiled.append(dict(entry,source_studies=[i+1]))
    if redundant:
        notes.append('未被要求单独开展的同范围体检提议已合并到停车研究的新基准；保留明确要求的诊断，不复用历史结果。')
    for entry,task in zip(compiled,selected):
        if len(entry['source_studies'])>1:
            obligation=(f"{task['comparison_regions']}个不同候选区" if task.get('comparison_regions',1)>1 else
                        f'{minimum_parking_candidates}个不同停车候选')
            notes.append(f"第{'、'.join(map(str,entry['source_studies']))}组同范围停车提议合并为一个研究：仍须实测{obligation}，使用本次同需求新基准，不重复建组或预设治理位置。")
    return selected,compiled,dict(version=VERSION,proposed_studies=len(entries),executable_studies=len(compiled),
                                   source_studies=sources,actions=actions,notes=notes)
