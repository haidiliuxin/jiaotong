"""Turn-state detours, bus protection and byte-preserving ManageN edits.

This checks the encoded car graph, not unrecorded signs or real-world feasibility.
Each removed edge-state transition must have a replacement under ALL new bans.
"""
import heapq
import math
import re
from collections import Counter, defaultdict

from .data import geometry, read_text


def restrictions(path):
    lines = read_text(path).splitlines()
    lines = [line for line in lines if line.strip()]
    declared = int(lines[0].split()[-1])
    rows = [tuple(map(int, line.split())) for line in lines[2:]]
    if any(len(row) != 6 for row in rows):
        raise ValueError("ManageN需要六列整数")
    counts = Counter(row[0] for row in rows)
    if len(counts) != declared:
        raise ValueError("ManageN节点数量不符")
    seen = Counter()
    turns = set()
    for via, count, ordinal, frm, middle, to in rows:
        seen[via] += 1
        if via != middle or count != counts[via] or ordinal != seen[via]:
            raise ValueError("ManageN节点、组内数量或序号不一致")
        turn = (frm, via, to)
        if turn in turns:
            raise ValueError("ManageN存在重复转向")
        turns.add(turn)
    return turns


def append_restrictions(path, additions):
    """Only rewrite numeric count fields; preserve unrelated lines and GB bytes."""
    additions = [tuple(t) for t in additions]
    original = restrictions(path)
    if len(set(additions)) != len(additions) or set(additions) & original:
        raise ValueError("新增禁转重复或已经存在")
    if any(len(t) != 3 or any(type(n) is not int or n < 0 for n in t) or len(set(t)) != 3 for t in additions):
        raise ValueError("新增禁转必须为三个不同的非负整数节点")
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    nonempty = [i for i, line in enumerate(lines) if line.strip()]
    header = nonempty[0]
    via_counts = Counter(t[1] for t in original | set(additions))
    lines[header], count = re.subn(rb"\d+(?=[ \t\r\n]*$)", str(len(via_counts)).encode(), lines[header])
    if count != 1:
        raise ValueError("ManageN头部数量无法唯一定位")
    groups = defaultdict(list)
    for turn in additions:
        groups[turn[1]].append(turn)
    old_counts = Counter(t[1] for t in original)
    last = {}
    for i in nonempty[2:]:
        via = int(lines[i].split()[0])
        last[via] = i
        if via in groups:
            lines[i] = re.sub(rb"^(\d+[ \t]+)\d+", lambda m: m[1] + str(via_counts[via]).encode(), lines[i])
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    inserted = {}
    for via, turns in groups.items():
        records = []
        for ordinal, (frm, middle, to) in enumerate(turns, old_counts[via] + 1):
            records.append(f"{via}\t{via_counts[via]}\t{ordinal}\t{frm}\t{middle}\t{to}".encode() + newline)
        inserted.setdefault(last.get(via, len(lines) - 1), []).extend(records)
    result = []
    for i, line in enumerate(lines):
        result.append(line)
        if i in inserted:
            if not line.endswith(b"\n"):
                result.append(newline)
            result.extend(inserted[i])
    path.write_bytes(b"".join(result))
    if restrictions(path) != original | set(additions):
        raise ValueError("ManageN写入后集合不符")


def bus_turns(scenario, pairs):
    protected = defaultdict(list)
    routes = 0
    for filename in ("BusNodeBus.txt", "BusNodeBRT.txt"):
        lines = [line for line in read_text(scenario / "data" / filename).splitlines() if line.strip()]
        declared = int(lines[0].split()[-1])
        if len(lines[2:]) != declared:
            raise ValueError(f"{filename}公交线路数量不符")
        ids = set()
        for line in lines[2:]:
            route, count, *nodes = map(int, line.split())
            if route in ids or count != len(nodes) or len(nodes) < 2:
                raise ValueError(f"{filename}公交线路节点数量或编号不符")
            ids.add(route)
            if any(pair not in pairs for pair in zip(nodes, nodes[1:])):
                raise ValueError(f"{filename}线路{route}包含不存在的有向路段")
            triples = list(zip(nodes, nodes[1:], nodes[2:]))
            if len(nodes) > 3 and nodes[0] == nodes[-1]:
                triples.append((nodes[-2], nodes[0], nodes[1]))
            for turn in triples:
                protected[turn].append(f"{filename}:{route}")
            routes += 1
    return dict(protected), routes


class TurnGraph:
    def __init__(self, rows, access, banned=(), protected=None):
        self.edges = {(r["from_node"], r["to_node"]): r for r in rows if access.get((r["from_node"], r["to_node"])) == 0}
        self.banned = set(banned)
        self.protected = protected or {}
        self.out = defaultdict(list)
        for frm, to in self.edges:
            self.out[frm].append(to)
        self.route_count = 0

    @classmethod
    def load(cls, scenario):
        rows = geometry(scenario)
        pairs = {(r["from_node"], r["to_node"]) for r in rows}
        access = {}
        for line in read_text(scenario / "data/ManageR1.txt").splitlines()[1:]:
            if not line.strip():
                continue
            values = list(map(int, line.split()))
            if len(values) != 10 or tuple(values[:2]) in access or values[3] not in (0, 1, 2, 3):
                raise ValueError("ManageR1维数、唯一性或小汽车通行码不符")
            access[tuple(values[:2])] = values[3]
        if set(access) != pairs:
            raise ValueError("ManageR1与GEOM有向路段集合不符")
        protected, count = bus_turns(scenario, pairs)
        graph = cls(rows, access, restrictions(scenario / "data/manageN.txt"), protected)
        graph.route_count = count
        return graph

    def detour(self, turn, bans, limit_km):
        start, goal = tuple(turn[:2]), tuple(turn[1:])
        # A node-only route would miss both entry restrictions and exit direction.
        heap, distance, parent = [(0.0, start)], {start: 0.0}, {}
        while heap:
            cost, state = heapq.heappop(heap)
            if cost != distance[state]:
                continue
            if state == goal:
                path = [state]
                while path[-1] != start:
                    path.append(parent[path[-1]])
                path.reverse()
                return {"states": [list(p) for p in path], "nodes": [path[0][0]] + [p[1] for p in path], "travel_after_entry_km": cost}
            prev, via = state
            for to in self.out[via]:
                edge = (via, to)
                road = self.edges[edge]
                if to == prev or (prev, via, to) in bans or not road["included"]:
                    continue  # No U-turn or virtual connector in the replacement.
                next_cost = cost + road["length_km"]
                if next_cost <= limit_km + 1e-9 and next_cost < distance.get(edge, math.inf):
                    distance[edge], parent[edge] = next_cost, state
                    heapq.heappush(heap, (next_cost, edge))
        return None

    def validate(self, additions, max_extra_km=2.0, max_ratio=3.0):
        turns = [tuple(t) for t in additions]
        if not turns or len(turns) != len(set(turns)):
            raise ValueError("禁转集合为空或重复")
        if not (math.isfinite(max_extra_km) and max_extra_km >= 0 and math.isfinite(max_ratio) and max_ratio >= 1):
            raise ValueError("绕行上限非法")
        checks = []
        bans = self.banned | set(turns)
        for turn in turns:
            violations = []
            if len(turn) != 3 or len(set(turn)) != 3 or any(type(n) is not int or n < 0 for n in turn):
                raise ValueError("禁转必须为三个不同非负整数节点")
            incoming, outgoing = turn[:2], turn[1:]
            if incoming not in self.edges or outgoing not in self.edges:
                violations.append("进口或出口不是允许小汽车通行的有向路段")
            if turn in self.banned:
                violations.append("该转向在原场景已经禁止")
            if turn in self.protected:
                violations.append("该转向被既有公交线路使用")
            replacement = None
            if not violations:
                direct = self.edges[outgoing]["length_km"]
                replacement = self.detour(turn, bans, min(direct + max_extra_km, direct * max_ratio))
                if replacement is None:
                    violations.append("所有新增禁转同时生效后，找不到满足上限的转向状态替代路径")
                else:
                    replacement.update(extra_km=replacement["travel_after_entry_km"] - direct,
                                       ratio=replacement["travel_after_entry_km"] / direct if direct else None)
            checks.append({"turn": list(turn), "eligible": not violations, "violations": violations,
                           "bus_routes": self.protected.get(turn, []), "replacement": replacement})
        return {"eligible": all(c["eligible"] for c in checks), "turns": checks,
                "max_extra_km": max_extra_km, "max_ratio": max_ratio,
                "baseline_rules": len(self.banned), "bus_routes_checked": self.route_count,
                "baseline_inactive_rules": sum(t[:2] not in self.edges or t[1:] not in self.edges for t in self.banned),
                "claim": "每条被删除的边状态转移均有合法替代，因而保持该小汽车图原有边状态可达关系；不等同完整软件或现实路权证明",
                "limits": ["替代路径不借道虚拟连接线、不使用即时掉头", "未核实车牌分组、道路宽度、标志设置及现实公交运行时刻"]}


def read_turn_volumes(path):
    """Read the software's text junction matrices without mistaking totals for turns."""
    turns, via, columns = {}, None, None
    for line in read_text(path).splitlines():
        match = re.search(r"No\.(\d+)", line)
        if match:
            via, columns = int(match[1]), None
            continue
        fields = line.split()
        if via is None or not fields:
            continue
        if not fields[0].isdigit() and len(fields) > 2 and all(f.isdigit() for f in fields[1:-1]):
            columns = list(map(int, fields[1:-1]))
        elif columns is not None and fields[0].isdigit():
            if len(fields) != len(columns) + 2:
                raise ValueError("转向矩阵行列维数不符")
            frm = int(fields[0])
            values = list(map(float, fields[1:]))
            if any(not math.isfinite(v) or v < 0 for v in values):
                raise ValueError("转向矩阵存在非法流量")
            for to, value in zip(columns, values[:-1]):
                key = (frm, via, to)
                if key in turns:
                    raise ValueError("转向矩阵存在重复条目")
                turns[key] = value
    if not turns:
        raise ValueError("未读取到转向矩阵")
    return turns
