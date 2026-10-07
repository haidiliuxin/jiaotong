"""Diagnostic adapter: remove explicitly added closures from each native routing graph.

Existing model restrictions and OD handling remain those of the shipped solver.
This is not a claim that every pre-existing traffic rule is respected.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import numeric, read_text, save_json
from traffic_agent.oneway import access_rows
from traffic_agent.runner import now

MODES = {"car": 3, "taxi": 3, "motor": 4, "truck": 6}
TRANSFORMED = ("data/number.txt", "cache/n_r.dat", "cache/FT.dat", "cache/tv.dat")


def bridge(mode, config):
    if mode not in MODES:
        raise ValueError("未知原生车型")
    runtime = Path(config["runtime"])
    scenario = Path(read_text(runtime / "files/runinfo_vc6.txt").splitlines()[1])
    output = Path(config["archive"]) / f"{time.time_ns()}_{mode}_{os.getpid()}"
    scratch = output / "scenario"
    scratch.mkdir(parents=True, exist_ok=False)
    manifest = dict(schema_version=2, bridge_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    status="running", started=now(), mode=mode, outer_scenario=str(scenario),
                    policy_closures=config["policy_closures"], pid=os.getpid())
    save_json(output / "manifest.json", manifest)
    active = None
    try:
        closed = {tuple(pair) for pair in config["policy_closures"]}
        rights = access_rows(scenario / "data/ManageR1.txt")
        if any(pair not in rights or rights[pair][MODES[mode]] != 2 for pair in closed):
            raise ValueError("显式新增禁行必须与场景路权码2一致")
        ft = [tuple(map(int, s.split())) for s in read_text(scenario / "cache/FT.dat").splitlines() if s.strip()]
        nodes = [int(v) for v in numeric(scenario / "cache/rnode.dat")]
        tv = [int(v) for v in numeric(scenario / "cache/tv.dat")]
        counts = [int(v) for v in numeric(scenario / "cache/n_r.dat")]
        origins = [node for node, count in zip(nodes, counts[1:]) for _ in range(count)]
        if len(set(ft)) != len(ft) or counts[0] != len(nodes) or len(counts) != len(nodes)+1 or any(count < 0 for count in counts[1:]):
            raise ValueError("内部路由图不能唯一定位")
        if any(not 1 <= v <= len(nodes) for v in tv) or list(zip(origins,[nodes[v-1] for v in tv])) != ft or not closed <= set(ft):
            raise ValueError("内部路由图与FT不一致")
        kept = [i for i,pair in enumerate(ft) if pair not in closed]
        if not kept:
            raise ValueError("不允许空路由图")
        parameters = read_text(scenario / f"params/AsignParameter_{mode}.dat").splitlines()
        if parameters[:3] != [f"Assign_{mode}.od", f"Rwm_{mode}.dat", f"volume_{mode}.dat"]:
            raise ValueError("分配文件名不符合已核对原生车型")
        files = ("data/number.txt", f"params/AsignParameter_{mode}.dat", "cache/NodeInZone.dat",
            "cache/Shortest_No.dat", "cache/rnode.dat", "cache/tv.dat", "cache/n_r.dat", "cache/szn.dat",
            f"cache/Assign_{mode}.od", f"cache/Rwm_{mode}.dat", "cache/FT.dat")
        raw = {name: (scenario / name).read_bytes() for name in files}
        if any((scenario / name).read_bytes() != data for name,data in raw.items()):
            raise ValueError("读取窗口内分配输入发生变化")
        for name,data in raw.items():
            path = scratch / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            if name in TRANSFORMED or name == f"cache/Rwm_{mode}.dat":
                original = output / "original_inputs" / name
                original.parent.mkdir(parents=True, exist_ok=True)
                original.write_bytes(data)
        for name in ("cache/FT.dat", "cache/tv.dat", f"cache/Rwm_{mode}.dat"):
            lines = raw[name].splitlines(keepends=True)
            if len(lines) != len(ft):
                raise ValueError("边数组维数错误")
            (scratch / name).write_bytes(b"".join(lines[i] for i in kept))
        count_lines = raw["cache/n_r.dat"].splitlines(keepends=True)
        number_lines = raw["data/number.txt"].splitlines(keepends=True)
        if int(number_lines[0]) != len(ft) or int(number_lines[1]) != len(nodes):
            raise ValueError("number中的边数和节点数错误")
        number_lines[0] = f"{len(kept)}\r\n".encode("ascii")
        (scratch / "data/number.txt").write_bytes(b"".join(number_lines))
        removals = {}
        for u,v in closed:
            removals[u] = removals.get(u, 0) + 1
        for i,node in enumerate(nodes):
            if node in removals:
                count_lines[i+1] = f"{counts[i+1]-removals[node]}\r\n".encode("ascii")
        (scratch / "cache/n_r.dat").write_bytes(b"".join(count_lines))
        child_programs = output / "runtime/programs"
        child_programs.mkdir(parents=True)
        # The native solver invokes the vendor's startup check relative to cwd.
        # Preserve its program and date files unmodified; never bypass that check.
        for filename in ("M1-StarNet.exe", "date1.txt", "date2.txt"):
            shutil.copy2(runtime / "programs" / filename, child_programs / filename)
        manifest["vendor_support_hashes"] = {filename: hashlib.sha256((child_programs / filename).read_bytes()).hexdigest()
                                             for filename in ("M1-StarNet.exe", "date1.txt", "date2.txt")}
        child_config = output / "runtime/files/runinfo_vc6.txt"
        child_config.parent.mkdir()
        child_config.write_bytes(("scenario_dir\r\n"+scratch.as_posix()+"\r\n").encode("gb18030"))
        executable = Path(config["native_programs"]) / f"NodeAssignModel_{mode}.exe"
        kept_set = set(kept)
        manifest.update(input_rows=len(ft), native_rows=len(kept), removed_indices=[i for i in range(len(ft)) if i not in kept_set],
            input_hashes={name: hashlib.sha256(data).hexdigest() for name,data in raw.items()},
            native_executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            assigned_od_sum=sum(numeric(scratch / f"cache/Assign_{mode}.od")))
        save_json(output / "kept_original_indices.json", kept)
        save_json(output / "manifest.json", manifest)
        before = {p.relative_to(scratch).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in scratch.rglob("*") if p.is_file()}
        save_json(output / "native_inputs.json", before)
        stamp = time.time_ns()
        with (output / "stdout.bin").open("wb") as out, (output / "stderr.bin").open("wb") as err:
            active = subprocess.Popen([str(executable)], cwd=child_programs, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                      creationflags=subprocess.CREATE_NO_WINDOW)
            manifest["native_pid"] = active.pid
            save_json(output / "manifest.json", manifest)
            manifest["returncode"] = active.wait(timeout=60)
        target = scratch / f"cache/volume_{mode}.dat"
        if manifest["returncode"] or any(b"[error]" in (output / f"{s}.bin").read_bytes().lower() for s in ("stdout","stderr")) or not target.is_file() or target.stat().st_mtime_ns < stamp:
            raise ValueError("原生分配未生成合格的新输出")
        if any(hashlib.sha256((scratch / p).read_bytes()).hexdigest() != h for p,h in before.items()):
            raise ValueError("原生分配改写了派生输入")
        values = numeric(target)
        lines = target.read_bytes().splitlines(keepends=True)
        if len(values) != len(kept) or len(lines) != len(kept) or min(values) < 0:
            raise ValueError("原生输出与实际输入图不匹配")
        expanded = [b"0\r\n"] * len(ft)
        for i,line in zip(kept,lines):
            expanded[i] = line
        raw_expanded = b"".join(expanded)
        (output / "expanded_volume.dat").write_bytes(raw_expanded)
        destination = scenario / f"cache/volume_{mode}.dat"
        temporary = destination.with_suffix(".bridge.tmp")
        temporary.write_bytes(raw_expanded)
        temporary.replace(destination)
        manifest.update(status="completed", native_output_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                        expanded_output_sha256=hashlib.sha256(raw_expanded).hexdigest())
    except BaseException as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if active is not None and active.poll() is None:
            active.kill()
            active.wait(timeout=10)
        manifest["finished"] = now()
        save_json(output / "manifest.json", manifest)


if __name__ == "__main__":
    config = json.loads(Path(os.environ["TRAFFIC_AGENT_BRIDGE_CONFIG"]).read_text(encoding="utf-8"))
    bridge(sys.argv[1], config)
