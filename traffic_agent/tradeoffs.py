"""Read-only explanations from saved paired metrics, including old archives."""


def tradeoffs(state):
    items = []
    for run in state.get('runs', []):
        comparison = run.get('comparison', {})
        warnings = []
        for area, delta in comparison.get('delta', {}).items():
            label = {'core': '核心区', 'neighbor': '邻区', 'network': '全网'}.get(area, area)
            for key, title, unit in [('running_time_change_pct', '路段行驶时间增加', '%'),
                                     ('oversaturated_km_change', '过饱和有向里程净增', ' km')]:
                value = delta.get(key)
                if value is not None and value > 0:
                    warnings.append(f'{label}{title} {value:.6f}{unit}')
        gross = comparison.get('new_network_oversaturated_km')
        if gross is not None and gross > 0:
            warnings.append(f'全网毛新增过饱和有向里程 {gross:.6f} km')
        if warnings:
            items.append({'scenario': run['name'], 'demand_factor': run['demand_factor'],
                          'eligible': comparison.get('eligible', False), 'warnings': warnings})
    return items


def selection_rule(task):
    network_limit = task.get('max_new_network_oversaturated_km')
    extra = (f'另设全网毛新增过饱和有向里程硬上限 {network_limit:g} km，超过即拒绝候选。'
             if network_limit is not None else '')
    if task.get('recommendation_version') == 'tolerance-v2':
        from .selection import RULE
        return RULE + extra
    if task.get('search_mode') == 'robust':
        return '原任务按全部已设需求的硬约束与paired-minimax-v1选择同一措施；无完整合格措施保留基准。' + extra
    tolerance = task.get('recommendation_tolerance_pcu_h', 0)
    return (f'先通过既定约束；以最低全网路段耗时为参照，差值不超过 {tolerance:g} pcu·h 的候选'
            '优先较少参数改动、较温和停车等级，再比较耗时和名称。'
            '零容差保留历史精确排序；容差为任务预设假设，不代表已标定的工程精度。' + extra)
