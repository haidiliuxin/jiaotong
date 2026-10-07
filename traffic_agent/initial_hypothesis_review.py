"""One budgeted repair of typed hypothesis criteria, before any native work.

Only the policy family or an explicit original quantifier may be repaired.
Questions, test, study order and execution conditions stay fixed. This is a finite
contract, not arbitrary semantic equivalence, and never invents a replacement.
"""
import copy

from . import hypothesis_contract
from .llm_client import ModelError
from .question_wording import VERSION as WORDING_VERSION,V3_VERSION as V3_WORDING_VERSION,V4_VERSION as V4_WORDING_VERSION,V5_VERSION as V5_WORDING_VERSION,LEGACY_VERSION as LEGACY_WORDING_VERSION

LEGACY_VERSION='initial-hypothesis-correction-v1'
POLICY_VERSION='initial-hypothesis-correction-v2'
VERSION='initial-hypothesis-correction-v3'
CODE='invalid_hypothesis_criterion'


class HypothesisDraftError(ModelError):
    def __init__(self,check):
        super().__init__('初始假设判据未通过：'+'；'.join(f"{i['hypothesis_id']}：{i['message']}" for i in check['issues']))
        self.check=check


def design(entries,compilation,wording_protocol=LEGACY_WORDING_VERSION):
    return dict(studies=[dict(source_studies=copy.deepcopy(e['source_studies']),
                task={k:copy.deepcopy(e['task'][k]) for k in
                      (('scenario','road_name','demand_factors','max_neighbor_time_increase_pct','max_new_oversaturated_km')
                       if wording_protocol in (V3_WORDING_VERSION,V4_WORDING_VERSION,V5_WORDING_VERSION,WORDING_VERSION) else ('scenario','road_name','demand_factors'))
                      if k in e['task']}) for e in entries],
                aliases=[{k:a[k] for k in ('source_study','retained_source_study')} for a in compilation.get('actions',[])])


def inspect(assessment,scope,budget,version=VERSION,wording_protocol=WORDING_VERSION):
    entries=scope['studies'];compilation=dict(actions=scope['aliases'])
    issues=[];bound=[]
    for i,h in enumerate(assessment,1):
        try:
            bound.extend(hypothesis_contract.bind([h],entries,compilation,wording_protocol=wording_protocol))
        except ModelError as exc:
            options=[]
            for policy in hypothesis_contract.POLICY_OPTIONS[h['criterion']['test']]:
                candidate=copy.deepcopy(h);candidate['criterion']['policies']=policy
                try:
                    hypothesis_contract.bind([candidate],entries,compilation,wording_protocol=wording_protocol)
                    options.append(policy)
                except ModelError:pass
            issue=dict(hypothesis_id=f'H{i}',message=str(exc),allowed_policies=options)
            if version==VERSION and not options:
                from .question_wording import explicit_quantifier
                quantifier=explicit_quantifier(h['hypothesis'],wording_protocol)
                candidates=[]
                if quantifier and quantifier!=h['criterion']['quantifier']:
                    for policy in hypothesis_contract.POLICY_OPTIONS[h['criterion']['test']]:
                        candidate=copy.deepcopy(h);candidate['criterion'].update(policies=policy,quantifier=quantifier)
                        try:hypothesis_contract.bind([candidate],entries,compilation,wording_protocol=wording_protocol)
                        except ModelError:continue
                        candidates.append({k:v for k,v in candidate['criterion'].items() if h['criterion'][k]!=v})
                if candidates:
                    minimum=min(len(c) for c in candidates)
                    candidates=[c for c in candidates if len(c)==minimum]
                    # A concrete original quantifier is required. Never offer a
                    # weaker question merely because it would be easier to pass.
                    issue['allowed_fields']={k:sorted({c[k] for c in candidates}) for k in candidates[0]}
            issues.append(issue)
    source_ids={s for e in entries for s in e['source_studies']}|{a['source_study'] for a in scope['aliases']}
    references_known=all(s in source_ids for h in assessment for s in h['criterion']['studies'])
    feasible=bool(issues and all(i.get('allowed_fields') or i['allowed_policies'] for i in issues) and references_known and budget['fits'] and budget['model']['used']==1
                  and budget['model']['required']+1<=budget['model']['limit'])
    check=dict(version=version,design=copy.deepcopy(scope),budget_check=copy.deepcopy(budget),issues=issues,correction_feasible=feasible)
    # Older receipts predate selectable wording versions. Reproduce their exact
    # shape and v1 interpretation; new receipts freeze the explicit version.
    if wording_protocol!=LEGACY_WORDING_VERSION:check['wording_protocol']=wording_protocol
    return bound,check


def bind(assessment,entries,compilation,budget):
    bound,check=inspect(assessment,design(entries,compilation,WORDING_VERSION),budget)
    if check['issues']:raise HypothesisDraftError(check)
    return bound


def verify_check(check,proposal,request):
    from .planning_budget import verify_initial_costs
    if not isinstance(check,dict) or check.get('version') not in (LEGACY_VERSION,POLICY_VERSION,VERSION):
        raise ValueError('初始判据修正缺少已登记校验协议')
    verify_initial_costs(check['budget_check'],request)
    for h in proposal['assessment']:hypothesis_contract.validate_shape(h['criterion'])
    if inspect(proposal['assessment'],check['design'],check['budget_check'],check['version'],
               check.get('wording_protocol',LEGACY_WORDING_VERSION))[1]!=check or not check['correction_feasible']:
        raise ValueError('初始判据修正与原提议、错误或可用预算不符')


def verify_revision(check,first_proposal,last_proposal,result):
    """The second reply cannot obtain validity by dropping or replacing a question."""
    from .planning_budget import verify_budget_revision
    def questions(proposal):
        return [dict(hypothesis=h['hypothesis'],evidence_needed=h['evidence_needed'],
                     criterion={k:v for k,v in h['criterion'].items() if k not in (('policies','quantifier') if check['version']==VERSION else ('policies',))})
                for h in proposal['assessment']]
    def studies(proposal):
        return [{k:s[k] for k in ('study','strategies','road','target','demand_profile')} for s in proposal['studies']]
    current=result['budget_check'];previous=check['budget_check']
    wording=check.get('wording_protocol',LEGACY_WORDING_VERSION)
    if wording!=result.get('question_wording_protocol',LEGACY_WORDING_VERSION):
        raise ValueError('修正使用的问题口径协议与执行前冻结记录不同')
    verify_check(check,first_proposal,result['request'])
    verify_budget_revision(previous,current)
    allowed={i['hypothesis_id']:i.get('allowed_fields',dict(policies=i['allowed_policies'])) for i in check['issues']}
    policy_changes_valid=all(all(last['criterion'][key] in allowed.get(f'H{i}',{}).get(key,[first['criterion'][key]])
                                for key in ('policies','quantifier'))
                             for i,(first,last) in enumerate(zip(first_proposal['assessment'],last_proposal['assessment']),1))
    if (questions(first_proposal)!=questions(last_proposal) or studies(first_proposal)!=studies(last_proposal)
            or not policy_changes_valid
            or first_proposal.get('intent')!='execute' or last_proposal.get('intent')!='execute'
            or current['groups']!=previous['groups'] or current['native']!=previous['native'] or not current['fits']
            or design(result['experiments'],result['compilation'],wording)!=check['design']):
        raise ValueError('判据字段修正改变了原问题、判据类型、原文量词含义、研究组、顺序或执行条件；未启动仿真')
    # Rebind from the persisted public reply, instead of trusting the result flag.
    bound,new_check=inspect(last_proposal['assessment'],check['design'],current,check['version'],wording)
    if new_check['issues'] or bound!=result['assessment']:
        raise ValueError('修正后的判据仍无效或与事前冻结的实际检验不符')


def display(check):
    """Only bounded public fields for the UI; no arbitrary provider payload."""
    from .llm_settings import redact
    if not isinstance(check,dict) or check.get('version') not in (LEGACY_VERSION,POLICY_VERSION,VERSION):return None
    issues=check.get('issues')
    if not isinstance(issues,list):return None
    return dict(version=check['version'],correction_feasible=check.get('correction_feasible') is True,
                issues=[dict(hypothesis_id=redact(str(i.get('hypothesis_id','')))[:20],
                             message=redact(str(i.get('message','')))[:600]) for i in issues[:4] if isinstance(i,dict)])
