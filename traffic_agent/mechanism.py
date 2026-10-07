"""Describe measured parking response, without claiming single-link causality."""


def parking_response(before, after, changes):
    a = {(r['from_node'], r['to_node']): r for r in before}
    b = {(r['from_node'], r['to_node']): r for r in after}
    evidence = []
    for change in changes:
        if change.get('kind', 'parking') != 'parking':
            continue
        pair = (change['from_node'], change['to_node'])
        x, y = a[pair], b[pair]
        ca, cb = x['adjusted_capacity_pcu_h'], y['adjusted_capacity_pcu_h']
        capacity_increased = ca is not None and cb is not None and cb > ca
        saved = x['length_km']*(x['flow_pcu_h']/x['speed_kmh']-y['flow_pcu_h']/y['speed_kmh'])
        if capacity_increased and y['speed_kmh'] <= x['speed_kmh']:
            note = '管理容量上升，但本次速度没有提高；容量联动不等于耗时收益'
        elif capacity_increased:
            note = '管理容量与速度均上升；仍须检查流量转移及核心、邻区、全网结果'
        else:
            note = '未观察到管理容量上升；该项证据不支持容量改善解释'
        evidence.append(dict(from_node=pair[0], to_node=pair[1], name=x['name'],
                             parking_before=x['parking'], parking_after=y['parking'],
                             capacity_before_pcu_h=ca, capacity_after_pcu_h=cb,
                             speed_before_kmh=x['speed_kmh'], speed_after_kmh=y['speed_kmh'],
                             load_before=x['adjusted_load_ratio'], load_after=y['adjusted_load_ratio'],
                             joint_saved_time_pcu_h=saved, note=note))
    return dict(roads=evidence,
                no_positive_joint_link_benefit=bool(evidence) and all(r['joint_saved_time_pcu_h'] <= 1e-9 for r in evidence),
                interpretation='每条道路是联合治理仿真的观察，不是拆分干预的因果贡献；未验证停车需求承接和执法成本')
