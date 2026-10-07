"""Private hard-closure runtime and independently checked native input transforms."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath

from .data import PROJECT, numeric, read_text, save_json
from .native_access_bridge import MODES, TRANSFORMED
from .runner import setup_runtime


def digest(data):
    return hashlib.sha256(data).hexdigest()


def install_runtime(job):
    runtime = setup_runtime(job)
    programs, native = runtime / "programs", runtime / "native_programs"
    native.mkdir(exist_ok=False)
    hashes = {}
    for mode in MODES:
        filename = f"NodeAssignModel_{mode}.exe"
        shutil.copy2(programs / filename, native / filename)
        hashes[mode] = digest((native / filename).read_bytes())
    wrapper = PROJECT / "scripts/native_assignment_wrapper.c"
    shutil.copy2(wrapper, programs / wrapper.name)
    completed = subprocess.run(["gcc", "-municode", "-static", "-Os", wrapper.name, "-o", "native_assignment_wrapper.exe"],
                              cwd=programs, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=60)
    (job / "wrapper_compile_stdout.bin").write_bytes(completed.stdout)
    (job / "wrapper_compile_stderr.bin").write_bytes(completed.stderr)
    if completed.returncode:
        raise RuntimeError("硬闭边入口编译失败，需要PATH中的MinGW gcc；参见wrapper_compile_stderr.bin")
    for mode in MODES:
        shutil.copy2(programs / "native_assignment_wrapper.exe", programs / f"NodeAssignModel_{mode}.exe")
    metadata = dict(native_executable_hashes=hashes,
                   wrapper_source_sha256=digest(wrapper.read_bytes()),
                   wrapper_sha256=digest((programs / "native_assignment_wrapper.exe").read_bytes()))
    return runtime, metadata


def install(job):
    runtime, metadata = install_runtime(job)
    profile = dict(version=2, **metadata,
                   bridge_source_sha256=digest((PROJECT / "traffic_agent/native_access_bridge.py").read_bytes()),
                   limits="只将显式新增闭边从车型图移除；原有禁转仍沿用原分配流程")
    save_json(job / "hard_access_profile.json", profile)
    return runtime, profile


def configure(job, runtime, name, changes):
    closed = [c["closed_direction"] for c in changes if c.get("kind") == "oneway"]
    config = dict(runtime=str(runtime), native_programs=str(runtime / "native_programs"),
                  archive=str(job / "bridge_runs" / name), policy_closures=closed)
    path = job / "bridge_configurations" / f"{name}.json"
    save_json(path, config)
    return {"TRAFFIC_AGENT_PYTHON": sys.executable,
            "TRAFFIC_AGENT_BRIDGE_SCRIPT": str(PROJECT / "traffic_agent/native_access_bridge.py"),
            "TRAFFIC_AGENT_BRIDGE_CONFIG": str(path)}


def verify_transform(folder):
    """Rebuild the expected derived bytes from preserved original inputs."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 2:
        raise ValueError("缺少可独立核验的转换原始输入")
    scratch = folder / "scenario"
    mode = manifest["mode"]
    changed_names = set(TRANSFORMED) | {f"cache/Rwm_{mode}.dat"}
    originals = {name: ((folder / "original_inputs" / name) if name in changed_names else (scratch / name)).read_bytes()
                 for name in manifest["input_hashes"]}
    if any(digest(data) != manifest["input_hashes"][name] for name,data in originals.items()):
        raise ValueError("删除前原始输入与本轮读取摘要不符")
    ft = [tuple(map(int,s.split())) for s in originals["cache/FT.dat"].splitlines()]
    closed = {tuple(p) for p in manifest["policy_closures"]}
    kept = [i for i,pair in enumerate(ft) if pair not in closed]
    if not closed <= set(ft) or len(set(ft)) != len(ft) or not kept:
        raise ValueError("闭边集合不在唯一原图内")
    if kept != json.loads((folder / "kept_original_indices.json").read_text(encoding="utf-8")):
        raise ValueError("保留索引没有对应原图的新增闭边")
    expected = dict(originals)
    for name in ("cache/FT.dat", "cache/tv.dat", f"cache/Rwm_{mode}.dat"):
        lines = originals[name].splitlines(keepends=True)
        if len(lines) != len(ft):
            raise ValueError("原始边数组长度不符")
        expected[name] = b"".join(lines[i] for i in kept)
    nodes = [int(v) for v in originals["cache/rnode.dat"].split()]
    counts = [int(v) for v in originals["cache/n_r.dat"].split()]
    tv = [int(v) for v in originals["cache/tv.dat"].split()]
    origins = [n for n,c in zip(nodes,counts[1:]) for _ in range(c)]
    if counts[0] != len(nodes) or list(zip(origins,[nodes[v-1] for v in tv])) != ft:
        raise ValueError("原始内部图与FT不一致")
    lines = originals["cache/n_r.dat"].splitlines(keepends=True)
    for i,node in enumerate(nodes):
        removed = sum(u==node for u,v in closed)
        if removed:
            lines[i+1] = f"{counts[i+1]-removed}\r\n".encode("ascii")
    expected["cache/n_r.dat"] = b"".join(lines)
    lines = originals["data/number.txt"].splitlines(keepends=True)
    if int(lines[0]) != len(ft) or int(lines[1]) != len(nodes):
        raise ValueError("原始图规模字段错误")
    lines[0] = f"{len(kept)}\r\n".encode("ascii")
    expected["data/number.txt"] = b"".join(lines)
    if any((scratch / name).read_bytes() != data for name,data in expected.items()):
        raise ValueError("派生图或OD发生预定闭边之外的更改")
    before = json.loads((folder / "native_inputs.json").read_text(encoding="utf-8"))
    if before != {name:digest(data) for name,data in expected.items()}:
        raise ValueError("原生求解开始前的输入摘要不一致")
    return manifest, ft, kept


def verify_run(job, name, scenario, changes, profile, recorded_scenario=None):
    from .oneway import FLOW_FILES
    from .data import geometry
    files = sorted((job / "bridge_runs" / name).glob("*/manifest.json"))
    if len(files) != 20:
        raise ValueError("需要四种车型各五轮原生求解")
    closed = [c["closed_direction"] for c in changes if c.get("kind") == "oneway"]
    grouped = {mode: [] for mode in MODES}
    outer_ft = [tuple(map(int,s.split())) for s in read_text(scenario / "cache/FT.dat").splitlines()]
    for file in files:
        folder = file.parent
        m, ft, kept = verify_transform(folder)
        mode = m["mode"]
        if (m["status"] != "completed" or m["returncode"] or m["policy_closures"] != closed or ft != outer_ft
                or (PureWindowsPath(m["outer_scenario"]) != PureWindowsPath(recorded_scenario) if recorded_scenario
                    else Path(m["outer_scenario"]).resolve() != scenario.resolve())
                or m["native_executable_sha256"] != profile["native_executable_hashes"][mode]
                or m["bridge_source_sha256"] != profile["bridge_source_sha256"]):
            raise ValueError("原生调用、策略或转换版本不符")
        raw = folder / f"scenario/cache/volume_{mode}.dat"
        if digest(raw.read_bytes()) != m["native_output_sha256"]:
            raise ValueError("原生输出变化")
        values = numeric(raw)
        if len(values) != len(kept) or min(values) < 0:
            raise ValueError("原生输出维数或数值错误")
        full = [0.0] * len(ft)
        for i,v in zip(kept,values):
            full[i] = v
        expanded = folder / "expanded_volume.dat"
        if full != numeric(expanded) or digest(expanded.read_bytes()) != m["expanded_output_sha256"]:
            raise ValueError("返回完整序列没有保持原生求解输出")
        od = numeric(folder / f"scenario/cache/Assign_{mode}.od")
        if sum(od) != m["assigned_od_sum"]:
            raise ValueError("本轮OD摘要错误")
        grouped[mode].append((m["started"], full, od))
    geom = geometry(scenario)
    if ft != outer_ft:
        raise ValueError("返回序列的原始端点与外层不一致")
    index = {pair:i for i,pair in enumerate(ft)}
    result = {}
    for mode,samples in grouped.items():
        samples.sort()
        if len(samples) != 5:
            raise ValueError("车型调用不是五轮")
        od_file = {"car":"ODCar.tsd","taxi":"ODTaxi.tsd","motor":"ODMotor.tsd","truck":"ODTruck.tsd"}[mode]
        demand = numeric(scenario / "result" / od_file)
        if any(s[2] != [float(f"{v*w/15:.6f}") for v in demand] for s,w in zip(samples,(5,4,3,2,1))):
            raise ValueError("OD单元不符合六位小数的分轮权重")
        od_error = max(abs(sum(s[2][i] for s in samples)-v) for i,v in enumerate(demand))
        # Six-place rounding over five rounds permits at most 2.5e-6 per cell.
        if od_error > 2.50001e-6:
            raise ValueError("分轮OD累计误差超出序列化精度")
        final = numeric(scenario / "result" / FLOW_FILES[mode])
        total = [sum(s[1][i] for s in samples) for i in range(len(ft))]
        error = max(abs(total[index[(r['from_node'],r['to_node'])]]-final[r['index']]) for r in geom)
        if error > .00501:
            raise ValueError("最终流量不是五轮原生输出累加")
        result[mode] = dict(calls=5, od_cells=len(demand), max_cell_od_sum_difference=od_error,
                            max_volume_sum_difference=error, transform_inputs_verified=True)
    return dict(passed=True, native_calls=20, modes=result, policy_closures=closed, transform_version=2)
