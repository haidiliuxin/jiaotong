"""Motor-vehicle one-way trial: access edits, boundary detours and real response.

Only one directed link may be closed in a trial. No geometry/lanes are reassigned.
The graph certificate concerns encoded access, not physical implementation.
"""
import heapq
import math
import re
from collections import defaultdict

from .circulation import TurnGraph, bus_turns, restrictions
from .data import geometry, numeric, read_text

MODE_COLUMNS = {"car": 3, "motor": 4, "truck": 6, "bus": 7}
FLOW_FILES = {"car": "VolLinkCar.tsd", "taxi": "VolLinkTax.tsd", "motor": "VolLinkMot.tsd",
              "truck": "VolLinkTrk.tsd", "bus": "VolLinkBus.tsd"}
REQUIRED_OUTPUTS = ("cache/ManageR1.dat",) + tuple("result/" + f for f in FLOW_FILES.values())


def access_rows(path):
    records = {}
    for line in read_text(path).splitlines()[1:]:
        if not line.strip():
            continue
        values = tuple(map(int, line.split()))
        if len(values) != 10 or min(values[:2]) < 0 or any(v not in (0, 1, 2, 3) for v in values[2:]):
            raise ValueError("ManageR1需要端点与八类合法通行码")
        pair = values[:2]
        if pair in records:
            raise ValueError("ManageR1有重复有向路段")
        records[pair] = values
    return records


def close_motor_direction(path, pair):
    """0 -> 2 for four motor modes only; preserve every other byte."""
    pair = tuple(pair)
    before = access_rows(path)
    if pair not in before or pair[::-1] not in before or pair[0] == pair[1]:
        raise ValueError("必须为既有双向道路的一条方向")
    if any(before[p][col] != 0 for p in (pair, pair[::-1]) for col in MODE_COLUMNS.values()):
        raise ValueError("原双向道路必须允许四类机动车通行")
    lines = path.read_bytes().splitlines(keepends=True)
    found = 0
    for i, line in enumerate(lines[1:], 1):
        tokens = list(re.finditer(rb"\S+", line))
        if len(tokens) != 10 or tuple(int(t[0]) for t in tokens[:2]) != pair:
            continue
        for col in sorted(MODE_COLUMNS.values(), reverse=True):
            token = tokens[col]
            line = line[:token.start()] + b"2" + line[token.end():]
        lines[i] = line
        found += 1
    if found != 1:
        raise ValueError("路权修改不能唯一定位")
    expected = dict(before)
    changed = list(before[pair])
    for col in MODE_COLUMNS.values():
        changed[col] = 2
    expected[pair] = tuple(changed)
    path.write_bytes(b"".join(lines))
    if access_rows(path) != expected:
        raise ValueError("路权修改后存在额外变化")


def boundary_detour(graph, closed, start, goal, limit):
    """Goal (-1,v) means destination v; otherwise restore the exact outgoing edge."""
    heap, distances, parent = [(0.0, start)], {start: 0.0}, {}
    while heap:
        cost, state = heapq.heappop(heap)
        if cost != distances[state]:
            continue
        if state == goal or (goal[0] == -1 and state[1] == goal[1]):
            path = [state]
            while path[-1] != start:
                path.append(parent[path[-1]])
            path.reverse()
            return {"states": [list(p) for p in path], "distance_km": cost}
        prev, via = state
        for to in graph.out[via]:
            edge = (via, to)
            road = graph.edges[edge]
            if edge == closed or to == prev or (prev, via, to) in graph.banned:
                continue
            if not road["included"] and edge != goal:
                continue  # A connector may terminate a required OD boundary, never shortcut it.
            value = cost + road["length_km"]
            if value <= limit + 1e-9 and value < distances.get(edge, math.inf):
                distances[edge], parent[edge] = value, state
                heapq.heappush(heap, (value, edge))
    return None


class OneWayNetwork:
    def __init__(self, rows, access, banned=(), protected=None, route_count=0):
        self.rows = {(r["from_node"], r["to_node"]): r for r in rows}
        if set(access) != set(self.rows):
            raise ValueError("ManageR1与GEOM路段集合不符")
        self.access, self.protected, self.route_count = access, protected or {}, route_count
        self.graphs = {mode: TurnGraph(rows, {p: v[col] for p, v in access.items()}, banned)
                       for mode, col in MODE_COLUMNS.items() if mode != "bus"}
        self.incoming = {}
        for mode, graph in self.graphs.items():
            incoming = defaultdict(list)
            for frm, to in graph.edges:
                incoming[to].append(frm)
            self.incoming[mode] = incoming

    @classmethod
    def load(cls, scenario):
        rows = geometry(scenario)
        pairs = {(r["from_node"], r["to_node"]) for r in rows}
        _, count = bus_turns(scenario, pairs)  # Validate route counts, IDs and all directed edges.
        protected = defaultdict(list)
        for filename in ("BusNodeBus.txt", "BusNodeBRT.txt"):
            lines = [s for s in read_text(scenario / "data" / filename).splitlines() if s.strip()]
            for line in lines[2:]:
                route, _, *nodes = map(int, line.split())
                for edge in zip(nodes, nodes[1:]):
                    protected[edge].append(f"{filename}:{route}")
        return cls(rows, access_rows(scenario / "data/ManageR1.txt"),
                   restrictions(scenario / "data/manageN.txt"), dict(protected), count)

    def validate(self, pair, max_extra_km=2.0, max_ratio=3.0):
        pair = tuple(pair)
        if len(pair) != 2 or any(type(n) is not int or n < 0 for n in pair) or pair[0] == pair[1]:
            raise ValueError("单向组织需要两个不同的非负整数节点")
        if not (math.isfinite(max_extra_km) and max_extra_km >= 0 and math.isfinite(max_ratio) and max_ratio >= 1):
            raise ValueError("绕行上限非法")
        violations, checks = [], {}
        if any(p not in self.rows or not self.rows[p]["included"] for p in (pair, pair[::-1])):
            violations.append("需要两条有效的原双向道路方向")
        elif any(self.access[p][col] != 0 for p in (pair, pair[::-1]) for col in MODE_COLUMNS.values()):
            violations.append("原双向方向并非均允许四类机动车通行")
        if pair in self.protected:
            violations.append("关闭方向被既有公交线路使用")
        if not violations:
            u, v = pair
            for mode, graph in self.graphs.items():
                entries = [(-1, u)] + [(a, u) for a in self.incoming[mode][u]
                                      if a != v and (a, u, v) not in graph.banned]
                exits = [(-1, v)] + [(v, b) for b in graph.out[v]
                                    if b != u and (u, v, b) not in graph.banned]
                records = []
                for start in entries:
                    for goal in exits:
                        direct = graph.edges[pair]["length_km"]
                        if goal[0] != -1:
                            direct += graph.edges[goal]["length_km"]
                        replacement = boundary_detour(graph, pair, start, goal,
                                                      min(direct + max_extra_km, direct * max_ratio))
                        if replacement:
                            replacement.update(extra_km=replacement["distance_km"] - direct,
                                               ratio=replacement["distance_km"] / direct)
                        records.append({"entry": list(start), "exit": list(goal),
                                        "direct_km": direct, "replacement": replacement})
                checks[mode] = records
                if any(r["replacement"] is None for r in records):
                    violations.append(f"{mode}存在不能按绕行上限替代的原有进入/离开状态或端点OD接入")
        return {"eligible": not violations, "closed_direction": list(pair), "open_direction": list(pair[::-1]),
                "violations": violations, "bus_routes": self.protected.get(pair, []),
                "bus_routes_checked": self.route_count, "modal_boundary_checks": checks,
                "max_extra_km": max_extra_km, "max_ratio": max_ratio,
                "claim": "检查小汽车(含出租车)、摩托车、货车原有边界状态及端点OD替代；保持全部既有公交线路边",
                "limits": ["仅单条方向关闭，不分配反向车道，不改变非机动车与步行路权",
                           "图检查基于已编码路权和禁转；仍须实际分车型流量验证，不证明现实标志及道路条件"]}


def response(baseline, policy, pair):
    pair = tuple(pair)
    access = access_rows(policy / "data/ManageR1.txt")
    ft = [tuple(map(int, s.split())) for s in read_text(policy / "cache/FT.dat").splitlines() if s.strip()]
    cache = [tuple(map(int, s.split())) for s in read_text(policy / "cache/ManageR1.dat").splitlines() if s.strip()]
    if len(ft) != len(cache) or len(set(ft)) != len(ft) or set(ft) != set(access):
        raise ValueError("路权缓存与FT不能唯一匹配")
    # V4.8 observed output preserves 0/1/2/3 input codes, despite appendix 2-40
    # describing a binary cache. Require exact values; do not accept any nonzero.
    expected = {p: values[2:] for p, values in access.items()}
    cache_matches = all(values == expected[p] for p, values in zip(ft, cache))
    rows = geometry(baseline)
    if [(r["from_node"], r["to_node"]) for r in geometry(policy)] != [(r["from_node"], r["to_node"]) for r in rows]:
        raise ValueError("基准与政策GEOM顺序不一致")
    index = next(r["index"] for r in rows if (r["from_node"], r["to_node"]) == pair)
    reverse = next(r["index"] for r in rows if (r["from_node"], r["to_node"]) == pair[::-1])
    flows = {}
    for mode, filename in FLOW_FILES.items():
        before, after = numeric(baseline / "result" / filename), numeric(policy / "result" / filename)
        if len(before) != len(rows) or len(after) != len(rows) or min(before + after) < 0:
            raise ValueError("分车型流量维数或非负性错误")
        flows[mode] = {"before": before[index], "after": after[index], "reverse_after": after[reverse]}
    active = sum(v["before"] for v in flows.values()) > 0.01
    zero = all(v["after"] <= 0.01 for v in flows.values())
    reverse_active = sum(v["reverse_after"] for v in flows.values()) > 0.01
    actual_codes = all(access[pair][col] == 2 and access[pair[::-1]][col] == 0 for col in MODE_COLUMNS.values())
    return {"passed": cache_matches and actual_codes and active and zero and reverse_active,
            "cache_matches_all_access_rows": cache_matches, "cache_encoding": "exact_input_codes_v48",
            "motor_access_codes_correct": actual_codes,
            "baseline_has_motor_flow": active, "closed_all_motor_flows_zero": zero,
            "open_direction_has_motor_flow": reverse_active, "closed_direction": list(pair), "flows": flows,
            "limits": "路权和禁行响应成立不代表交通收益；各车型量纲分别呈现，不将合计用于收益指标"}
