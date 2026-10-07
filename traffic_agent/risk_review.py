"""Separate observed costs from model-authored, explicitly untested concerns.

The model never retypes the measured risk summary. It may identify an untested
concern, cite the observations motivating it, and select the evidence it needs.
That is a research judgment, not an observation or a semantic certification.
"""
import copy
from .llm_client import substantive_text
from .planning_response import ResponseValidationError

LEGACY_VERSION='observed-risk-boundary-v1'
VERSION='observed-risk-boundary-v2'
SUPPORTED_VERSIONS=(LEGACY_VERSION,VERSION)


def active(payload):
    version=payload.get('context',{}).get('risk_review_protocol')
    if version not in (None,*SUPPORTED_VERSIONS):raise ValueError('未知的风险证据协议')
    return version is not None


def references(payload):
    costs=payload.get('context',{}).get('measured_conclusion',{}).get('costs',[])
    return list(dict.fromkeys([o['ref'] for o in payload.get('observations',[])]+[c['ref'] for c in costs]))


def neighbor_details(comparison):
    """Bounded native link facts with explicit coverage; missing is not empty."""
    links=comparison.get('new_neighbor_oversaturated_links')
    if not isinstance(links,list):return {}
    return dict(neighbor_new_links=[{k:r.get(k) for k in
                ('index','name','from_node','to_node','length_km','vc','speed_kmh','flow_pcu_h')} for r in links[:10]],
                neighbor_new_link_count=len(links),neighbor_new_links_complete=len(links)<=10)


def evidence_catalog(payload):
    """Availability comes from typed fields in this input, never model prose."""
    from .conclusion_review import number
    from .planning_payload import nodes,pointer
    observations=payload.get('observations',[]);context=payload.get('context',{})
    primary=[(('observations',),observations),
             (('context','measured_conclusion','costs'),context.get('measured_conclusion',{}).get('costs',[]))]
    for i,completed in enumerate(context.get('completed',[])):
        primary.append((('context','completed',i,'review','conclusion_review','costs'),
                        completed.get('review',{}).get('conclusion_review',{}).get('costs',[])))
    paths=dict(directed_link_state=[],paired_core_time=[],paired_neighbor_limits=[],new_neighbor_link_details=[])
    for root,value in primary:
        for suffix,node in nodes(value):
            if not isinstance(node,dict):continue
            path=pointer(root+suffix)
            if all(type(node.get(k)) is int for k in ('from_node','to_node')) and any(number(node.get(k)) is not None for k in ('speed_kmh','vc','flow_pcu_h')):
                paths['directed_link_state'].append(path)
            delta=node.get('delta');delta=delta if isinstance(delta,dict) else {}
            core_delta=delta.get('core');core_delta=core_delta if isinstance(core_delta,dict) else {}
            neighbor_delta=delta.get('neighbor');neighbor_delta=neighbor_delta if isinstance(neighbor_delta,dict) else {}
            core=node.get('core_time_change_pct',core_delta.get('running_time_change_pct'))
            if number(core) is not None:paths['paired_core_time'].append(path)
            neighbor=node.get('neighbor_time_change_pct',neighbor_delta.get('running_time_change_pct'))
            gross=node.get('neighbor_new_oversaturated_km',node.get('new_neighbor_oversaturated_km'))
            if number(neighbor) is not None and number(gross) is not None:paths['paired_neighbor_limits'].append(path)
            for key in ('neighbor_new_links','new_neighbor_oversaturated_links'):
                if isinstance(node.get(key),list):paths['new_neighbor_link_details'].append(pointer(root+suffix+(key,)))
    descriptions=dict(
        directed_link_state=('方向级静态状态','只使用实际提供的流量、速度和V/C字段；未提供字段不能当成已测。'),
        paired_core_time=('同条件核心耗时配对','可比较已试措施与其同需求基准，不证明未试措施效果或现实成因。'),
        paired_neighbor_limits=('邻区耗时与毛新增配对','可按冻结门槛检查已试方案；毛新增与净变化分别保留。'),
        new_neighbor_link_details=('新增过饱和路段明细','可定位所列新增过饱和路段；以总数和明细覆盖标记区分完整与节选，静态明细不能证明动态回堵。'))
    result=[dict(id=id,title=title,status='provided' if paths[id] else 'not_in_current_input',
                 source_paths=paths[id],meaning=meaning) for id,(title,meaning) in descriptions.items()]
    return result+[
        dict(id='untested_demand_pairs',title='未试需求的配对结果',status='new_simulation_required',source_paths=[],
             meaning='需要另行研究和原生预算，为未试需求运行新基准与同措施；不自动执行。'),
        dict(id='dynamic_queue_evidence',title='动态排队或回堵证据',status='separate_evidence_required',source_paths=[],
             meaning='需要另行采集时间序列等动态证据；现有静态批次分配不能确认回堵程度或位置。'),
        dict(id='causal_identification',title='现实成因识别证据',status='separate_evidence_required',source_paths=[],
             meaning='需要独立识别设计和证据；当前配对变化不能单独证明现实机制或零风险。')]


STATUS=dict(provided='本轮已提供',not_in_current_input='本轮输入未提供',
            new_simulation_required='需要新研究及仿真',separate_evidence_required='需要另行取得证据')


def constrain(tool,payload):
    if not active(payload):return
    properties=tool['parameters']['properties'];required=tool['parameters']['required']
    del properties['risk'];required.remove('risk')
    fields=dict(hypothesis=dict(type='string',minLength=1,maxLength=300,
                    description='尚未证实的风险假设；不得当成已观测行为，也不重抄实测代价。'),
                basis_refs=dict(type='array',items=dict(type='string',enum=references(payload)),
                    minItems=1,maxItems=3,uniqueItems=True,description='哪些已提供观测促使你提出该假设；引用不等于证明。'),
                verification_needed=dict(type='string',minLength=1,maxLength=300,
                    description='需要什么新观测才能检验；现有批次接口不支持的行为须明确需另行采集或验证。'))
    if payload['context']['risk_review_protocol']==VERSION:
        del fields['verification_needed']
        fields['verification_needs']=dict(type='array',minItems=1,maxItems=3,uniqueItems=True,
            items=dict(anyOf=[dict(type='string',enum=[r['id']],description=STATUS[r['status']]+'：'+r['title']+'。'+r['meaning'])
                             for r in evidence_catalog(payload)]),
            description='选择检验该假设需要的证据类型。哪些数据已提供、哪些仍需取得，由实际输入决定；不要自行重写接口能力。')
    properties['unverified_risks']=dict(type='array',maxItems=3,
        items=dict(type='object',properties=fields,required=list(fields),additionalProperties=False),
        description='模型额外提出的待验证风险。没有具体假设时填[]，这不表示零风险；已测代价和固定证据缺口由观测记录显示。')
    required.append('unverified_risks')


def capture_schema(payload,schema):
    if not active(payload):return schema
    result=copy.deepcopy(schema)
    # A model that still sends the old public field must be rejected, while its
    # original explanation remains inspectable just like extra action prose.
    result['properties']['risk']=dict(type='string')
    if payload['context']['risk_review_protocol']==VERSION:
        result['properties']['unverified_risks']['items']['properties']['verification_needed']=dict(type='string')
    return result


def validate(value,payload):
    if not active(payload):return
    def require(ok,message):
        if not ok:raise ResponseValidationError('risk_review',message)
    rows=value.get('unverified_risks');refs=references(payload)
    typed=payload['context']['risk_review_protocol']==VERSION
    verification='verification_needs' if typed else 'verification_needed'
    available={r['id'] for r in evidence_catalog(payload)} if typed else set()
    require(isinstance(rows,list) and len(rows)<=3,'待验证风险须为最多三项的数组，没有额外假设可填空数组')
    for row in rows:
        require(isinstance(row,dict) and set(row)=={'hypothesis','basis_refs',verification},
                '风险假设、所据观测和需要的验证必须分开填写')
        for key in ('hypothesis',) if typed else ('hypothesis','verification_needed'):
            require(substantive_text(row[key]) and len(row[key])<=300,'待验证风险与验证方式须为有效短文')
        if typed:
            needs=row['verification_needs']
            require(isinstance(needs,list) and 1<=len(needs)<=3 and all(isinstance(n,str) and n in available for n in needs)
                    and len(set(needs))==len(needs),'风险验证只能引用不重复的证据目录项')
        cited=row['basis_refs']
        require(isinstance(cited,list) and 1<=len(cited)<=3 and all(isinstance(r,str) and r in refs for r in cited)
                and len(set(cited))==len(cited),'风险假设只能引用本轮真实提供且不重复的观测')


def measured_summary(payload):
    costs=payload['context'].get('measured_conclusion',{}).get('costs',[])
    if not costs:return '当前尚无已返回的治理配对代价；基准与候选检查不能证明治理效果。'
    adverse=sum(c.get('has_measured_cost') is True for c in costs)
    failed=sum(c.get('constraint_passed') is False for c in costs)
    return f'输入中已返回{len(costs)}项治理配对，其中{adverse}项记录到代价增加、{failed}项约束未通过。各项数值及未知项保留在实测代价表；通过门槛不等于零风险。'


def generated_values(payload):
    return dict(risk=measured_summary(payload),risk_origin=payload['context']['risk_review_protocol']) if active(payload) else {}


def verify(entry):
    enabled=active(entry['input'])
    if not enabled:
        if entry.get('risk_origin') in SUPPORTED_VERSIONS or 'unverified_risks' in entry:
            raise ValueError('旧记录不能事后追加新的风险证据协议')
        return
    if entry.get('status')=='accepted':
        validate(entry,entry['input'])
        if any(entry.get(k)!=v for k,v in generated_values(entry['input']).items()):
            raise ValueError('实测代价说明与原始观测不符')


def detail_coverage(payload):
    """State the exact input coverage, without interpreting model wording."""
    rows=[]
    for o in payload.get('observations',[]):
        if not o.get('changes') or not o.get('delta'):continue
        links=o.get('neighbor_new_links');total=o.get('neighbor_new_link_count')
        provided=len(links) if isinstance(links,list) else None
        known=type(total) is int and total>=0 and provided is not None and total>=provided
        complete=(provided==total) if known else None
        text=('本轮输入未提供新增过饱和路段明细' if provided is None else
              f'已提供{provided}条／总计{total}条（'+('完整' if complete else '节选')+'）' if known else
              f'已提供{provided}条，完整数量未提供')
        rows.append(dict(ref=o['ref'],provided=provided,total=total if known else None,complete=complete,text=text))
    return rows


def display(entry):
    if entry.get('status')!='accepted' or not active(entry.get('input',{})):return None
    verify(entry)
    version=entry['input']['context']['risk_review_protocol'];rows=copy.deepcopy(entry['unverified_risks'])
    if version==VERSION:
        catalog={r['id']:r for r in evidence_catalog(entry['input'])}
        for row in rows:
            row['verification_evidence']=[copy.deepcopy(catalog[id]) for id in row['verification_needs']]
            row['verification_needed']='；'.join(STATUS[r['status']]+'：'+r['title']+'。'+r['meaning'] for r in row['verification_evidence'])
            row['verification_origin']='actual_input_and_model_selected_evidence_ids'
    return dict(version=version,measured_summary=entry['risk'],
                unverified=rows,observed_detail_coverage=detail_coverage(entry['input']),
                boundary='这些是模型待验证假设；所引观测只是提出问题的依据，不证明风险已经发生。')
