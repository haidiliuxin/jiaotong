"""Structured policy coverage and bounded claim support, not general entailment."""
import json
import re

LEGACY_VERSION='measured-evidence-support-v1'
V2_VERSION='measured-evidence-support-v2'
VERSION='measured-evidence-support-v3'


def policy_key(changes):
    return json.dumps(sorted(changes,key=lambda c:json.dumps(c,sort_keys=True)),sort_keys=True,separators=(',',':'))


def ledger(observations,context,*,version=VERSION):
    if version not in (LEGACY_VERSION,V2_VERSION,VERSION):raise ValueError('未知的措施证据协议')
    required=sorted(set(context.get('task',{}).get('demand_factors',[])))
    bases={(o['ref'].split(':')[0],o.get('demand_factor')) for o in observations if not o.get('changes')}
    grouped={}
    for o in observations:
        if not o.get('changes') or o.get('delta') is None:continue
        # Policies from independent research groups cannot complete each other.
        key=(o['ref'].split(':')[0],policy_key(o['changes']))
        group=grouped.setdefault(key,dict(owner=key[0],changes=o['changes'],rows=[]))
        group['rows'].append(dict(ref=o['ref'],demand_factor=o.get('demand_factor'),
                                  eligible=o.get('eligible'),violations=o.get('violations',[])))
    policies=[]
    for key,group in sorted(grouped.items()):
        rows=group['rows'];measured={r['demand_factor'] for r in rows if type(r['demand_factor']) in (int,float)}
        planned=required or sorted(measured)
        missing=sorted(set(planned)-measured)
        missing_bases=[f for f in planned if (group['owner'],f) not in bases]
        complete=bool(planned) and not missing and not missing_bases and (bool(required) or version==LEGACY_VERSION)
        passed=complete and all(any(r['demand_factor']==f and r['eligible'] is True for r in rows)
                                    and all(r['eligible'] is True for r in rows if r['demand_factor']==f) for f in planned)
        policies.append(dict(group,required_demand_factors=planned,missing_demand_factors=missing,missing_baseline_factors=missing_bases,
            failed_demand_factors=sorted({r['demand_factor'] for r in rows if r['eligible'] is False and type(r['demand_factor']) in (int,float)}),
            complete=complete,eligible=passed))
    result=dict(version=version,required_demand_factors=required,policies=policies,
        eligible_policy_count=sum(p['eligible'] for p in policies),
        optimality_proven=False,
        boundary='资格仅对应同研究组、同措施、已测需求的完整配对；有限菜单试验不证明全网最优或唯一可行。')
    if version!=LEGACY_VERSION:result['coverage_known']=bool(required)
    return result


def issues(value,facts):
    result=[]
    legacy=facts['version']==LEGACY_VERSION
    robust_pattern=(r'(?:全部|所有|每个|各个|各|跨)需求(?:条件|点|工况)?(?:下)?(?:的)?'
                    r'(?:治理效果|验证结果)?(?:均|都|仍|持续|也)?(?:保持|已|已经|可|能够|是|获得)?'
                    r'(?:有效|通过|合格|稳健)(?!性|与否)' if facts['version']==VERSION else
                    r'(?:全部|所有|每个|各个|跨)需求[^。；;\n]{0,15}(?:均|都|仍|持续)?(?:有效|通过|合格|稳健)')
    texts=[(field,value.get(field,'')) for field in ('summary','reason','risk','next_check','programme_summary')]
    texts.extend((key+':'+u['hypothesis_id'],u['explanation'])
                 for key in ('hypothesis_updates','programme_updates') for u in value.get(key,[]))
    for field,text in texts:
        if not isinstance(text,str):continue
        for clause in re.split(r'[。；;\n]' if legacy else r'[。；;\n]|但是|然而|但|却',text):
            denial=r'不能|不得|无法|不代表|未证明|未找到|尚未|不保证|不声称|不意味着|若|如果|待验证'
            if not legacy:denial+='|并非|不是|没有|无一|未通过|未合格'
            if re.search(denial,clause):continue
            code=None
            if re.search(r'全(?:网|局)(?:的)?最优|所有(?:可能)?方案中最优',clause):code='unsupported_optimality_claim'
            elif (re.search(r'唯一(?:的)?(?:可行|有效|可用|合格)(?:的)?方案',clause)
                  and (not re.search(r'已测|本次测试|当前已执行',clause)
                       or not legacy and facts.get('coverage_known') and facts['eligible_policy_count']!=1)):
                code='unqualified_unique_claim'
            elif (re.search(robust_pattern,clause)
                  and (legacy or facts.get('coverage_known'))
                  and (not facts['eligible_policy_count'] or len(facts['required_demand_factors'])<2)):code='unsupported_robust_claim'
            if code:result.append(dict(code=code,field=field,refs=[],quote=clause[:160],
                message='结论超出本次完整配对证据；逐候选保留失败或缺测需求，只能评价已测范围，不证明全网最优或唯一可行。'))
    return result
