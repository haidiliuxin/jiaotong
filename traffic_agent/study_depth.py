"""Explicit research depth from the user's question, before model selection.

Only an expressly requested comparison or demand check becomes mandatory.
Candidate locations, interventions and their ordering remain model decisions.
"""
import re

from .planning_scope import PrerequisiteError
from .task import request_demand_factors

LEGACY_VERSION = 'research-depth-v1'
DEMAND_VERSION = 'research-depth-v2'
VERSION = 'research-depth-v3'
SUPPORTED_VERSIONS = (LEGACY_VERSION, DEMAND_VERSION, VERSION)
NUMBERS = {'二': 2, '两': 2, '三': 3, '四': 4, '2': 2, '3': 3, '4': 4}
COMPARE = re.compile(
    r'(?:比较|对比|评估).{0,12}?(?:(?:至少)?([二两三四234])(?:个|种|项)|多个|几种|不同)'
    r'.{0,8}?(?:停车)?(?:备选|候选|方案|措施|治理思路)'
    r'|(?:(?:至少)?([二两三四234])(?:个|种|项)|多个|几种|不同)'
    r'.{0,8}?(?:停车)?(?:备选|候选|方案|措施|治理思路).{0,8}?(?:比较|对比|评估)')
PARKING_COMPARE = re.compile(r'(?:比较|对比|评估).{0,12}?(?:(?:至少)?([二两三四234])(?:个|种|项)|多个|几种|不同)'
                             r'.{0,8}?停车(?:治理)?(?:备选|候选|方案|措施|治理思路)|'
                             r'(?:(?:至少)?([二两三四234])(?:个|种|项)|多个|几种|不同)'
                             r'.{0,8}?停车(?:治理)?(?:备选|候选|方案|措施|治理思路).{0,8}?(?:比较|对比|评估)')
DEMAND = re.compile(r'(?:需求|OD)(?:变化|波动|增加|增长|下降|改变)|(?:不同|多种|多个|跨)需求|'
                    r'(?:高峰|低谷).{0,12}?(?:仍|有效|稳健)')
NO_DEMAND_V1 = re.compile(r'不(?:用|要|必|需要)?(?:做|进行|检验|考虑)?(?:需求|跨需求|敏感性)')
NO_DEMAND = re.compile(r'不(?:用|要|必|需要)?(?:再|做|进行|检验|考虑)?(?:需求|跨需求|敏感性)')
FIND_ONE = re.compile(r'(?:找到|找出|先找).{0,12}?(?:一个|一种).{0,8}?(?:可用|合格|有效).{0,8}?(?:方案|措施)|'
                      r'(?:一个|一种).{0,8}?(?:可用|合格|有效).{0,8}?(?:方案|措施).{0,8}?(?:就停|停止|即可)')


def from_request(request, version=VERSION):
    if version not in SUPPORTED_VERSIONS:
        raise ValueError('未知研究深度协议')
    comparison_count = 1
    comparison_quote = ''
    demand_quote = ''
    find_one_quote = ''
    negative_demand=NO_DEMAND if version!=LEGACY_VERSION else NO_DEMAND_V1
    for clause in re.split(r'[。；;\n]', request or ''):
        if not clause.strip():
            continue
        if not re.search(r'不(?:用|要|必|需要)?(?:再|做|进行)?(?:比较|对比|评估)', clause):
            match = COMPARE.search(clause)
            if version==VERSION and not match:
                match=PARKING_COMPARE.search(clause)
            if match:
                groups=match.groups()
                comparison_count = max(comparison_count, NUMBERS.get(next((g for g in groups if g),None), 2))
                comparison_quote = clause.strip()
        if not negative_demand.search(clause):
            if DEMAND.search(clause):
                demand_quote = clause.strip()
        if FIND_ONE.search(clause):
            find_one_quote = clause.strip()
    if version!=LEGACY_VERSION:
        factors=request_demand_factors(request or '')
        if factors is not None and len(factors)>1:
            if NO_DEMAND.search(request):
                raise PrerequisiteError('同时指定多个离散需求系数和不做需求检验，研究深度冲突；请明确是否逐需求配对检验')
            if not demand_quote:
                demand_quote=next((clause.strip() for clause in re.split(r'[。；;\n]',request)
                                   if '需求系数' in clause),request.strip())
    intent = ('demand_robustness' if demand_quote else 'compare_candidates' if comparison_count > 1
              else 'find_one' if find_one_quote else 'adaptive')
    result=dict(version=version, intent=intent, minimum_candidates=comparison_count,
                demand_validation=bool(demand_quote), comparison_quote=comparison_quote,
                demand_quote=demand_quote, find_one_quote=find_one_quote)
    if version==VERSION:
        result['candidate_domain']='parking' if comparison_count>1 and PARKING_COMPARE.search(comparison_quote) else 'all'
    return result


def verify_plan(depth, tasks):
    treatments = [task for task in tasks if task['scenario'] != 'diagnosis']
    if depth['demand_validation'] and any(len(t['demand_factors']) < 2 for t in tasks):
        raise PrerequisiteError('原问题明确要求检验需求变化下是否有效；治理研究须在至少两个需求条件下实测，不能只安排单需求')
    if depth['minimum_candidates'] > 1:
        parking_only=depth.get('version')==VERSION and depth.get('candidate_domain')=='parking'
        if parking_only:
            from .planning_review import required_policies
            possible=sum(required_policies(t) for t in treatments if t['scenario']=='parking')
        else:
            possible=sum((3 if t['scenario'] in ('oneway', 'circulation') else
                          max(t.get('comparison_policies') or 1,
                              t.get('comparison_regions') or 1)) for t in treatments)
        if possible < depth['minimum_candidates']:
            kind='停车' if parking_only else '治理'
            raise PrerequisiteError(f'原问题明确要求比较多个{kind}备选；初始研究没有预留足够的不同实测{kind}方案')
