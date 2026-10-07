"""Local turn-state graph with preserved original origin indices and path costs."""
from decimal import Decimal


def transform(raw, mode, via, banned):
    nodes = list(map(int, raw['cache/rnode.dat'].split()))
    original_nodes = list(nodes)
    ft = [tuple(map(int, line.split())) for line in raw['cache/FT.dat'].splitlines()]
    counts = list(map(int, raw['cache/n_r.dat'].split()))
    tv = list(map(int, raw['cache/tv.dat'].split()))
    origins = [node for node, count in zip(nodes, counts[1:]) for _ in range(count)]
    if (len(set(nodes)) != len(nodes) or len(set(ft)) != len(ft) or counts[0] != len(nodes)
            or len(counts) != len(nodes)+1 or min(counts[1:]) < 0
            or any(v < 1 or v > len(nodes) for v in tv)
            or list(zip(origins, [nodes[v-1] for v in tv])) != ft):
        raise ValueError('原生路由图无法唯一重建')
    if via not in nodes or nodes.index(via)+1 in list(map(int, raw['cache/szn.dat'].split()))[1:]:
        raise ValueError('当前转向适配只支持非质心节点')
    incoming = {u: i for i, (u, v) in enumerate(ft) if v == via}
    outgoing = {v: i for i, (u, v) in enumerate(ft) if u == via}
    if not incoming or not outgoing or via in incoming or via in outgoing:
        raise ValueError('不支持无进出口或带自环的原交叉口')
    banned = set(map(tuple, banned))
    if any(v != via or u not in incoming or w not in outgoing for u, v, w in banned):
        raise ValueError('禁转规则不属于本交叉口真实转向')
    cost_name = f'cache/Rwm_{mode}.dat'
    costs = [Decimal(line.decode('ascii').strip()) for line in raw[cost_name].splitlines()]
    if len(costs) != len(ft) or any(not c.is_finite() or c < 0 for c in costs):
        raise ValueError('原阻抗维数或数值错误')
    if any(costs[i] <= 0 for i in outgoing.values()):
        raise ValueError('状态转移需要正出口阻抗')
    fresh = max(nodes)+1
    ports = []
    for neighbors in (incoming, outgoing):
        table = {}
        for neighbor in neighbors:
            table[neighbor] = fresh
            nodes.append(fresh)
            fresh += 1
        ports.append(table)
    ip, op = ports
    edges = []
    for i, (u, v) in enumerate(ft):
        edges.append(dict(u=op[v] if u == via else u, v=ip[u] if v == via else v,
                          physical_index=i, turn=None, source_exit=None,
                          cost=str(costs[i]/2 if u == via else costs[i])))
    for u in incoming:
        for v, index in outgoing.items():
            if (u, via, v) in banned:
                continue
            edges.append(dict(u=ip[u], v=op[v], physical_index=None, turn=[u, via, v],
                              source_exit=None, cost=str(costs[index]/2)))
    for v, index in outgoing.items():
        edges.append(dict(u=via, v=op[v], physical_index=None, turn=None,
                          source_exit=v, cost=str(costs[index]/2)))
    internal = {node: i+1 for i, node in enumerate(nodes)}
    edges.sort(key=lambda e: (internal[e['u']], internal[e['v']]))
    out_counts = dict.fromkeys(nodes, 0)
    for edge in edges:
        out_counts[edge['u']] += 1
    if any(out_counts[n] == 0 for n in ip.values()):
        raise ValueError('禁转切断了一个进口状态的全部出口')
    def encode(values):
        return ('\r\n'.join(map(str, values))+'\r\n').encode('ascii')
    changed = dict(raw)
    number = raw['data/number.txt'].splitlines()
    if list(map(int, number[:2])) != [len(ft), len(original_nodes)]:
        raise ValueError('原始规模字段错误')
    changed.update({
        'data/number.txt': encode([len(edges), len(nodes), *[line.decode('ascii') for line in number[2:]]]),
        'cache/rnode.dat': encode(nodes),
        'cache/n_r.dat': encode([len(nodes), *out_counts.values()]),
        'cache/tv.dat': encode([internal[e['v']] for e in edges]),
        'cache/FT.dat': encode([f"{e['u']} {e['v']}" for e in edges]),
        cost_name: encode([e['cost'] for e in edges])})
    mapping = dict(via=via, original_nodes=len(original_nodes), original_edges=len(ft),
                   native_nodes=len(nodes), native_edges=len(edges),
                   banned_turns=sorted(map(list, banned)), inports=ip, outports=op, edges=edges)
    return changed, mapping
