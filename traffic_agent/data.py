"""Byte-preserving input editing and fixed-scope traffic measurements."""
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
OUTPUTS = {"speed_kmh": "SpdLinkVeh.tsd", "flow_pcu_h": "VolLinkVeh.tsd",
           "vc": "VCLinkVeh.tsd", "time_s": "TimeLink.tsd"}
JSON_REPLACE_DELAYS = (.01, .02, .04, .08, .16, .32, .5, .5)


def save_json(path, value):
    """Publish a complete snapshot, tolerating short Windows reader locks.

    Only replacing the already-written file is retried (1.63 s total backoff).
    Model calls, native tools and the caller's state transition are not replayed.
    """
    path = Path(path)
    content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(len(JSON_REPLACE_DELAYS)+1):
            try:
                temporary.replace(path)
                return
            except OSError as exc:
                if (os.name != 'nt' or getattr(exc, 'winerror', None) not in (5, 32, 33)
                        or attempt == len(JSON_REPLACE_DELAYS)):
                    raise
                time.sleep(JSON_REPLACE_DELAYS[attempt])
    finally:
        # Never remove another writer's temporary file or mask the original error.
        try:temporary.unlink(missing_ok=True)
        except OSError:pass


def read_text(path):
    raw = Path(path).read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("gb18030")


def numeric(path):
    lines = [line.strip() for line in read_text(path).splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"空数值文件: {path}")
    if lines[0].startswith("{"):
        lines = lines[1:]
    values = [float(v) for line in lines for v in line.split()]
    if not values or not all(math.isfinite(v) for v in values):
        raise ValueError(f"空数组或非有限数值: {path}")
    return values


def geometry(scenario):
    rows, seen = [], set()
    management = {}
    for line in read_text(scenario / "data/manageL.txt").splitlines():
        g = line.split()
        if len(g) == 5 and g[0].isdigit() and g[1].isdigit():
            pair = (int(g[0]), int(g[1]))
            if pair in management or not 0 <= int(g[3]) <= 4:
                raise ValueError("重复管理路段或非法停车类别")
            management[pair] = int(g[3])
    for line in read_text(scenario / "data/geom.txt").splitlines():
        if not line.strip():
            continue
        g = line.split(maxsplit=8)
        if len(g) < 8:
            raise ValueError("GEOM字段不足")
        pair = (int(g[0]), int(g[1]))
        if pair in seen:
            raise ValueError(f"重复有向路段，不能按端点唯一匹配: {pair}")
        seen.add(pair)
        length, lanes = float(g[2]) / 1000, float(g[4])
        if not math.isfinite(length) or not math.isfinite(lanes) or length < 0 or lanes < 0:
            raise ValueError("非法路段长度或车道数")
        rows.append(dict(index=len(rows), from_node=pair[0], to_node=pair[1], length_km=length,
                         road_class=int(g[3]), lanes=lanes, name=g[8] if len(g) > 8 else "",
                         parking=management.get(pair), included=1 <= int(g[3]) <= 6 and lanes > 0 and length > 0))
    if not rows:
        raise ValueError("路网为空")
    return rows


def results(scenario):
    rows = geometry(scenario)
    for key, filename in OUTPUTS.items():
        values = numeric(scenario / "result" / filename)
        if len(values) != len(rows):
            raise ValueError(f"{filename}行数与GEOM不一致")
        for row, value in zip(rows, values):
            row[key] = value
    for row in rows:
        if row["included"] and (row["speed_kmh"] <= 0 or min(row["flow_pcu_h"], row["vc"], row["time_s"]) < 0):
            raise ValueError(f"评价路段出现非法输出: {row['index']}")
    ft = [tuple(map(int, line.split())) for line in read_text(scenario / "cache/FT.dat").splitlines() if line.strip()]
    cap = numeric(scenario / "cache/CapLinkVehMan.dat")
    if len(ft) != len(cap) or len(set(ft)) != len(ft):
        raise ValueError("FT与管理修正容量不能唯一匹配")
    capacities = dict(zip(ft, cap))
    for row in rows:
        value = capacities.get((row["from_node"], row["to_node"]))
        row["adjusted_capacity_pcu_h"] = value
        row["adjusted_load_ratio"] = row["flow_pcu_h"] / value if value and value > 0 else None
    return rows


def metrics(rows):
    valid = [r for r in rows if r["included"]]
    distance = sum(r["length_km"] for r in valid)
    vkt = sum(r["flow_pcu_h"] * r["length_km"] for r in valid)
    hours = sum(r["flow_pcu_h"] * r["length_km"] / r["speed_kmh"] for r in valid)
    flags = sum(r["time_s"] >= 1e6 for r in valid)
    return {"directed_links": len(valid), "directed_length_km": distance,
            "weighted_speed_kmh": vkt / hours if hours else None,
            "running_time_pcu_h": hours, "transport_pcu_km": vkt,
            "oversaturated_km": sum(r["length_km"] for r in valid if r["vc"] > 1),
            "timelink_flagged_links": flags,
            "timelink_sum_pcu_h": None if flags else sum(r["flow_pcu_h"] * r["time_s"] / 3600 for r in valid)}


def neighborhood(rows, core, hops):
    reached = {n for r in rows if r["index"] in core for n in (r["from_node"], r["to_node"])}
    for _ in range(hops):
        expanded = set(reached)
        for r in rows:
            if r["included"] and (r["from_node"] in reached or r["to_node"] in reached):
                expanded.update((r["from_node"], r["to_node"]))
        reached = expanded
    return [r["index"] for r in rows if r["included"] and r["index"] not in core
            and r["from_node"] in reached and r["to_node"] in reached]


def parking_candidates(rows, task):
    """Complete candidate population; presentation truncation happens in diagnose."""
    candidates = [r.copy() for r in rows if r["included"] and r["road_class"] in (2, 3)
                  and r["parking"] in (3, 4) and 0.1 <= r["length_km"] <= 1.5
                  and r["flow_pcu_h"] >= 500 and 1 < r["vc"] <= 4 and r["speed_kmh"] < 40
                  and r["name"] and "匝道" not in r["name"] and not re.fullmatch(r"\d+路", r["name"])
                  and (task.road_name is None or r["name"] == task.road_name)]
    for r in candidates:
        r["priority_score"] = r["flow_pcu_h"] * r["length_km"] / r["speed_kmh"]
    candidates.sort(key=lambda r: (-r["priority_score"], r["index"]))
    return candidates


def diagnose(rows, task):
    candidates = parking_candidates(rows, task)
    if task.links:
        by_pair = {(r["from_node"], r["to_node"]): r for r in candidates}
        if any(tuple(p) not in by_pair for p in task.links):
            raise ValueError("指定路段未通过停车治理候选规则，或与道路名称约束不一致；不自动替换研究对象")
        selected = [by_pair[tuple(p)] for p in task.links]
    elif candidates:
        seed = candidates[0]
        nearby = neighborhood(rows, [seed["index"]], task.neighbor_hops)
        selected = [seed] + [r for r in candidates[1:] if r["index"] in nearby][:task.max_links - 1]
    else:
        selected = []
    return {"candidates": candidates[:30], "selected": selected,
            "rule": "城市2/3类道路，停车3/4，长0.1-1.5km，q>=500，1<软件VC<=4，v<40；按qL/v排序；自动选点限种子两端的指定跳数邻域",
            "cause": "停车干扰为待试验检验的成因假设，不是实测因果结论"}


def scopes(rows, core, hops):
    return {"core": core, "neighbor": neighborhood(rows, core, hops),
            "network": [r["index"] for r in rows if r["included"]]}


def scoped_metrics(rows, scope):
    return {name: metrics([rows[i] for i in indices]) for name, indices in scope.items()}


def input_fingerprints(scenario):
    # These fingerprints prove input equivalence across compared simulation runs.
    paths = [p for folder in ("data", "params") for p in (scenario / folder).rglob("*") if p.is_file()]
    paths += list((scenario / "result").glob("OD*.tsd"))
    return {p.relative_to(scenario).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def changed_input_files(changes, demand_factor=1.0):
    files = set()
    for edit in changes:
        kind = edit.get("kind", "parking")
        if kind not in ("parking", "turn", "oneway"):
            raise ValueError("未知治理措施类型")
        files.add({"turn": "data/manageN.txt", "oneway": "data/ManageR1.txt", "parking": "data/manageL.txt"}[kind])
    if demand_factor != 1:
        files.add("result/ODCar.tsd")
    return files


def prepare(source, destination, changes, demand_factor=1.0, extra_outputs=()):
    changed_input_files(changes, demand_factor)
    turns = [edit["turn"] for edit in changes if edit.get("kind") == "turn"]
    directions = [edit["closed_direction"] for edit in changes if edit.get("kind") == "oneway"]
    one_way_audit = None
    if directions:
        if len(directions) != 1 or turns:
            raise ValueError("目前仅验证单条方向，不能混合新增禁转或多方向关闭")
        from .oneway import OneWayNetwork
        one_way_audit = OneWayNetwork.load(source).validate(directions[0])
        if not one_way_audit["eligible"]:
            raise ValueError("单向组织未通过公交和全车型边界接入检查")
    turn_audit = None
    if turns:
        from .circulation import TurnGraph
        # A caller cannot bypass bus/access checks by submitting a hand-written edit.
        turn_audit = TurnGraph.load(source).validate(turns)
        if not turn_audit["eligible"]:
            raise ValueError("新增禁转未通过公交保护和转向状态绕行检查")
    if destination.exists():
        raise ValueError("拒绝覆盖已有场景")
    shutil.copytree(source, destination)
    # Only known evaluation outputs are removed, exclusively in this new scenario copy.
    # They must be newly generated; inherited caches/OD are retained as model inputs.
    for name in OUTPUTS.values():
        (destination / "result" / name).unlink(missing_ok=True)
    for name in ("CapLinkVehMan.dat", "Assign_car.od"):
        (destination / "cache" / name).unlink(missing_ok=True)
    for name in extra_outputs:
        path = destination / name
        if not path.resolve().is_relative_to(destination.resolve()) or Path(name).is_absolute():
            raise ValueError("输出清理路径越出新场景目录")
        path.unlink(missing_ok=True)
    path = destination / "data/manageL.txt"
    raw = path.read_bytes()
    edit_log = []
    for edit in changes:
        if edit.get("kind") in ("turn", "oneway"):
            continue
        frm, to, old, new = (edit[k] for k in ("from_node", "to_node", "old_parking", "parking"))
        if type(new) is not int or new not in (0, 1, 2) or old not in (3, 4):
            raise ValueError("停车治理参数不在白名单内")
        pattern = rb"(?m)^(" + str(frm).encode() + rb"[ \t]+" + str(to).encode() + rb"[ \t]+\d+[ \t]+)" + str(old).encode() + rb"([ \t]+\d+[ \t]*\r?$)"
        raw, count = re.subn(pattern, lambda m: m[1] + str(new).encode() + m[2], raw)
        if count != 1:
            raise ValueError(f"路段{frm}->{to}原值或唯一性校验失败")
        edit_log.append(dict(edit, file="data/manageL.txt", evidence="parking.categories"))
    path.write_bytes(raw)
    if directions:
        from .oneway import close_motor_direction
        close_motor_direction(destination / "data/ManageR1.txt", directions[0])
        edit_log.append({"kind": "oneway", "closed_direction": directions[0], "file": "data/ManageR1.txt",
                         "validation": one_way_audit, "evidence": "ManageR1 four motor codes 0->2; reverse unchanged"})
    if turns:
        from .circulation import append_restrictions
        append_restrictions(destination / "data/manageN.txt", turns)
        edit_log.append({"kind": "turn", "file": "data/manageN.txt", "turns": turns,
                         "evidence": "ManageN table 2-11; car access ManageR1; BusNodeBus/BRT", "validation": turn_audit})
    if demand_factor != 1.0:
        od = destination / "result/ODCar.tsd"
        original = od.read_bytes()
        header, body = original.split(b"\n", 1)
        if not header.lstrip().startswith(b"{"):
            raise ValueError("小汽车OD必须为带TSD头的矩阵")
        values = numeric(od)
        zones = int(read_text(destination / "data/Chnn.txt").splitlines()[0].split()[-1])
        if len(values) != zones * zones or min(values) < 0:
            raise ValueError("OD维数或非负性校验失败")
        token = rb"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
        body = re.sub(token, lambda m: format(float(m[0]) * demand_factor, ".10g").encode("ascii"), body)
        od.write_bytes(header + b"\n" + body)
        changed = numeric(od)
        if not math.isclose(sum(changed), sum(values) * demand_factor, rel_tol=1e-8, abs_tol=1e-6):
            raise ValueError("OD缩放总量检查失败")
        edit_log.append({"file": "result/ODCar.tsd", "factor": demand_factor,
                         "sum_before_pcu_h": sum(values), "sum_after_pcu_h": sum(changed),
                         "evidence": "demand.sensitivity", "other_modes": "unchanged"})
    return edit_log
