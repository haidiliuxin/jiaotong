"""Minimal paid-preview accounting, including failures before a job exists."""
import json
import math
import uuid

from .data import save_json
from .llm_client import ModelError
from .llm_settings import redact
from .runner import now


def safe_usage(usage):
    if not isinstance(usage, dict):
        return None
    text={'request_model','response_model','response_id','tokens_source','wire_api','finish_reason','response_status',
          'request_tool_choice','request_schema_sha256','transport_version','tool_choice_profile','requested_tool_choice'}
    counts={'tokens','input_tokens','output_tokens','reasoning_tokens','cached_input_tokens','tool_call_count',
            'request_tool_count','request_max_output_tokens','request_size_bytes'}
    result={}
    for key,value in usage.items():
        if key in text and (value is None or isinstance(value,str)):
            result[key]=redact(value)[:200] if isinstance(value,str) else None
        elif key in counts and (value is None or type(value) is int and value>=0):result[key]=value
        elif key=='latency_s' and (value is None or type(value) in (int,float) and math.isfinite(value) and value>=0):result[key]=value
        elif key=='request_thinking_mode' and value in ('disabled','enabled'):result[key]=value
    return result


def safe_proposal(value):
    """Public planning fields only, never a raw reply or an executable ticket."""
    if not isinstance(value,dict):return None
    def short(v,limit=400):return redact(v)[:limit] if isinstance(v,str) else ''
    decision=value.get('decision')
    body=decision if isinstance(decision,dict) else value
    def items(key):return body[key][:5] if isinstance(body.get(key),list) else []
    questions=items('questions') or items('missing') or items('missing_information')
    missing=[short(v.get('question')) if isinstance(v,dict) else short(v) for v in questions if isinstance(v,(str,dict))]
    result=dict(status='unvalidated',goal=short(value.get('goal')),reason=short(value.get('reason',value.get('reasoning'))),
                studies=[],missing=missing)
    citations=[dict(need=short(v.get('need'),80),source_quote=short(v.get('source_quote'),100))
               for v in items('questions') if isinstance(v,dict)]
    if not citations and isinstance(value.get('question_citations'),list):
        citations=[dict(need=short(v.get('need'),80),source_quote=short(v.get('source_quote'),100))
                   for v in value['question_citations'][:4] if isinstance(v,dict)]
    if citations:result['question_citations']=citations
    intent=body.get('intent')
    if intent in ('execute','needs_input','unsupported'):result['intent']=intent
    # Preserve only this public typed interpretation, never arbitrary reply keys.
    if 'assessment' in body:
        from .hypothesis_contract import validate,validate_shape
        from .question_authoring import study_keys,render,verify_row
        result['assessment']=[]
        try:keys=study_keys(items('studies') or items('experiments'))
        except ModelError:keys={}
        for h in items('assessment'):
            if not isinstance(h,dict):continue
            if 'definition' in h:
                try:
                    if set(h)!={'definition'}:raise ModelError('结构化问题混入额外字段')
                    row=render(h['definition'],keys)
                except ModelError:
                    definition=h['definition'] if isinstance(h['definition'],dict) else {}
                    row=dict(definition={k:([short(x,80) for x in v[:5] if isinstance(x,str)] if isinstance(v,list) else short(v,80))
                        for k,v in definition.items() if k in ('test','study_keys','policies','quantifier')},criterion_status='invalid')
                    row.update({k:short(h[k]) for k in ('hypothesis','evidence_needed') if k in h})
                result['assessment'].append(row)
                continue
            row=dict(hypothesis=short(h.get('hypothesis')),evidence_needed=short(h.get('evidence_needed')))
            try:
                validate_shape(h.get('criterion'))
                row['criterion']={k:v for k,v in h['criterion'].items()}
                validate(h['criterion'])
            except ModelError:row['criterion_status']='invalid'
            if 'authoring' in h:
                try:verify_row(h)
                except ModelError:row['criterion_status']='invalid'
                else:row['authoring']=h['authoring']
            result['assessment'].append(row)
    unsupported=items('capabilities') or items('unsupported') or items('unsupported_requests')
    if unsupported:result['unsupported']=[short(v,80) for v in unsupported if isinstance(v,str)]
    studies=items('studies') or items('experiments')
    for study in studies:
        if not isinstance(study,dict):continue
        strategies=study.get('strategies')
        result['studies'].append(dict(study=short(study.get('study'),40),
            strategies=[short(s,40) for s in strategies[:3] if isinstance(s,str)] if isinstance(strategies,list) else [],
            road=short(study.get('road',study.get('road_name')),120),
            target=short(study.get('target',study.get('target_ref')),120),
            demand_profile=short(study.get('demand_profile'),40),rationale=short(study.get('rationale'))))
        if 'key' in study:result['studies'][-1]['key']=short(study['key'],40)
    return result if result['goal'] or result['reason'] or result['studies'] else None


def proposal_from_reply(response):
    calls=response.get('calls') if isinstance(response,dict) else None
    if not isinstance(calls,list) or len(calls)!=1 or not isinstance(calls[0],dict) or calls[0].get('name')!='compose_traffic_plan':return None
    try:return safe_proposal(json.loads(calls[0]['arguments']))
    except (ValueError,KeyError,TypeError):return None


def listing(root, limit=8):
    """Read failed previews as well as successful ones, without changing evidence."""
    paths = list((root / 'planning_attempts').glob('preview_*.json'))
    records = []
    for path in paths:
        try:
            raw = json.loads(path.read_text('utf-8'))
            if not isinstance(raw, dict):
                continue
            record = {k: redact(raw[k])[:4000 if k == 'request' else 600]
                      for k in ('id', 'started', 'finished', 'request', 'status', 'error', 'explanation','session_id','phase','corrects')
                      if isinstance(raw.get(k), str)}
            if type(raw.get('ordinal')) is int and raw['ordinal'] in (1,2):record['ordinal']=raw['ordinal']
            record.update(requested=raw.get('requested') is True, usage=safe_usage(raw.get('usage')))
            if raw.get('knowledge_retrieval'):
                from .knowledge_retrieval import verify as verify_knowledge
                bundle=raw['knowledge_retrieval']
                try:verify_knowledge(bundle,raw.get('request'),'initial')
                except (OSError,ValueError,TypeError):pass
                else:
                    record['knowledge_retrieval']=dict(version=bundle['version'],hits=[
                        {key:redact(hit[key]) for key in ('id','source','text')} for hit in bundle['hits']])
            proposal=safe_proposal(raw.get('proposal'))
            if proposal:record['proposal']=proposal
            check=display_budget_check(raw.get('budget_check'))
            if check:record['budget_check']=check
            from .initial_hypothesis_review import display
            hypotheses=display(raw.get('hypothesis_check'))
            if hypotheses:record['hypothesis_check']=hypotheses
            from .hypothesis_policy_patch import for_version
            raw_patch=raw.get('policy_patch')
            patch=for_version(raw_patch.get('version')) if isinstance(raw_patch,dict) else None
            receipt=patch.display(raw.get('policy_patch')) if patch else None
            if receipt:record['policy_patch']=receipt
            if patch and raw.get('proposal_origin')==patch.ORIGIN:record['proposal_origin']=patch.ORIGIN
            records.append(record)
        except (OSError, ValueError):
            continue  # Do not repair or overwrite an incomplete original record.
    ordered=sorted(records, key=lambda r: (r.get('started', ''), r.get('id', '')), reverse=True)
    return ordered if limit is None else ordered[:limit]


def quality(root):
    """Local observed planning outcomes, including failures; no raw model text."""
    records=listing(root,limit=None)
    sessions={};requested=[r for r in records if r['requested']]
    for record in requested:
        sessions.setdefault(record.get('session_id') or record['id'],[]).append(record)
    first_ready=recovered=finished=0
    for attempts in sessions.values():
        attempts.sort(key=lambda r:(r.get('ordinal',1),r.get('started','')))
        first_ready+=attempts[0].get('status')=='ready'
        recovered+=len(attempts)>1 and attempts[0].get('status')!='ready' and attempts[-1].get('status')=='ready'
        finished+=attempts[-1].get('status') not in ('preparing','requesting','validating')
    count=len(sessions);known=unknown=0;latencies=[]
    for record in requested:
        usage=record.get('usage') or {};tokens=usage.get('tokens')
        if type(tokens) is int:known+=tokens
        else:unknown+=1
        if type(usage.get('latency_s')) in (int,float):latencies.append(usage['latency_s'])
    return dict(version='initial-planning-quality-v1',sessions=count,finished_sessions=finished,
        first_ready=first_ready,recovered_ready=recovered,requests=len(requested),
        first_ready_rate=first_ready/count if count else None,
        final_ready_rate=(first_ready+recovered)/count if count else None,
        known_tokens=known,unknown_usage=unknown,known_latency_s=sum(latencies),latency_samples=len(latencies),
        boundary='本机历史初始规划的观察统计，包含失败；不是受控成功率实验，也不代表仿真通过率或现实预测精度。')


def display_budget_check(check):
    """Only public costs in the attempts endpoint; no arbitrary saved fields."""
    from .planning_budget import INITIAL_VERSION,LEGACY_INITIAL_VERSION
    if not isinstance(check,dict) or check.get('version') not in (INITIAL_VERSION,LEGACY_INITIAL_VERSION):return None
    def counts(value):
        return {k:v for k,v in (value if isinstance(value,dict) else {}).items()
                if k in ('required','limit','used') and type(v) is int and 0<=v<=1000}
    rows=check.get('groups')
    if not isinstance(rows,list):return None
    groups=[]
    for row in rows[:5]:
        if not isinstance(row,dict):continue
        conditions=row.get('conditions')
        if not isinstance(conditions,dict):continue
        groups.append(dict(title=redact(str(row.get('title','')))[:200],
            conditions={k:redact(str(conditions.get(k) or ''))[:200] for k in ('road_name','scenario')},
            native_runs=row.get('native_runs') if type(row.get('native_runs')) is int else None,
            model_requests=row.get('model_requests') if type(row.get('model_requests')) is int else None,
            may_omit=row.get('may_omit') is True))
        omission=row.get('omission')
        if isinstance(omission,dict) and omission.get('kind')=='extra_factorial_component':
            groups[-1]['omission']={k:redact(str(omission.get(k,'')))[:300] for k in ('kind','source_quote','reason')}
    return dict(version=check['version'],groups=groups,native=counts(check.get('native')),model=counts(check.get('model')),
                fits=check.get('fits') is True,correction_feasible=check.get('correction_feasible') is True)


class PlanningAttempt:
    def __init__(self,client,root,request):
        self.client=client
        self.path=root/'planning_attempts'/('preview_'+uuid.uuid4().hex+'.json')
        self.record=dict(id=self.path.stem,started=now(),request=redact(request),
                         status='preparing',requested=False,usage=None,native_started=False)

    def persist(self):
        save_json(self.path,self.record)

    def request(self,*args,**kwargs):
        if (len(args)>1 and isinstance(args[1],list) and args[1]
                and isinstance(args[1][0],dict) and isinstance(args[1][0].get('content'),str)):
            try:
                payload=json.loads(args[1][0]['content'])
            except ValueError:
                payload={}
            if isinstance(payload,dict) and payload.get('knowledge_retrieval'):
                from .knowledge_retrieval import verify as verify_knowledge
                bundle=payload['knowledge_retrieval']
                verify_knowledge(bundle,payload.get('request'),'initial')
                self.record['knowledge_retrieval']=bundle
        self.record.update(status='awaiting_model',requested=True)
        self.persist()
        try:
            response=self.client.request(*args,**kwargs)
        except Exception as exc:
            if isinstance(exc,ModelError) and exc.request_sent is False:
                self.record['requested']=False
            self.finish('model_failed',exc)
            raise
        # Only transport-produced metadata, never raw output/headers or keys.
        self.record['usage']=safe_usage(response.get('metadata'))
        from . import hypothesis_policy_patch as patch
        context=getattr(self.client,'policy_patch_context',None)
        patch=patch.for_version(context.get('version')) if isinstance(context,dict) else None
        if patch:
            # Save the actual call and its cost before local compilation. A bad
            # patch is a validation failure, not a lost/failed network request.
            self.record.update(policy_patch=patch.capture(response,context['source_attempt_id']),status='validating')
            self.persist()
            try:response=patch.apply(response,context)
            except ModelError:
                self.record['policy_patch']['status']='rejected';self.persist()
                raise
            self.record['policy_patch']['status']='applied'
            self.record['proposal_origin']=patch.ORIGIN
        proposal=proposal_from_reply(response)
        if proposal:self.record['proposal']=proposal
        self.record['status']='validating'
        self.persist()
        return response

    def finish(self,status,error=None,explanation=None):
        if self.record['status']=='model_failed':return
        self.record.update(status=status,finished=now())
        if explanation is not None:
            self.record['explanation']=redact(explanation)[:600]
        if error is not None:
            self.record['error']=redact(str(error))[:600] if isinstance(error,(ModelError,ValueError)) else '本地规划处理失败；未记录原始异常内容'
            if isinstance(error,ModelError) and error.usage is not None:self.record['usage']=safe_usage(error.usage)
        self.persist()
