"""Run only approved TranStar tools, with a private runtime and OS-held lock."""
import contextlib
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .data import OUTPUTS, PROJECT, input_fingerprints, numeric, results, save_json

MODULES = ("M3DataRoadNetwork.exe", "M3DataBusNetwork.exe", "M3DataTrafficManage.exe",
           "M6AssignVehicle.exe", "M6TrafficLoadLinkVeh.exe", "M6TrafficLoadNodeVeh.exe", "M6NodeQueueDelay.exe")
DEFAULT_SOURCE = PROJECT / "南京市案例数据库/南京案例/基础路网"
RUNTIME_SOURCE = PROJECT / "方案1流程验证/runtime"
EXPERIMENTS = PROJECT / "智能体实验"


def now():
    return datetime.now(timezone.utc).isoformat()


@contextlib.contextmanager
def runtime_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "runtime.lock").open("a+b") as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("另一个智能体任务正在使用计算环境；请等其完成") from exc
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def setup_runtime(root):
    if os.name != "nt":
        raise RuntimeError("实际仿真需要Windows及已授权的TranStar；其他平台可运行任务检查与测试")
    runtime = root / "runtime"
    marker = runtime / "agent_runtime_ready.json"
    if not runtime.exists():
        shutil.copytree(RUNTIME_SOURCE, runtime)
        save_json(marker, {"copied_from": str(RUNTIME_SOURCE), "created": now()})
    if not marker.exists():
        raise RuntimeError(f"运行环境复制未完成，请检查{runtime}；不使用可能不完整的副本")
    for name in MODULES:
        exe = runtime / "programs" / name
        if not exe.is_file():
            raise RuntimeError(f"模型程序缺失或仍为Git LFS指针: {name}")
        with exe.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise RuntimeError(f"模型程序不是PE文件或仍为Git LFS指针: {name}")
    return runtime


def validate_source(source):
    from .data import geometry, read_text
    if not (source / "data").is_dir() or not (source / "params").is_dir():
        raise ValueError("场景应为包含data、params、cache、result的TranStar目录")
    geometry(source)
    for name in ("ODCar.tsd", "ODMotor.tsd", "ODTaxi.tsd", "ODTruck.tsd"):
        values = numeric(source / "result" / name)
        zones = int(read_text(source / "data/Chnn.txt").splitlines()[0].split()[-1])
        if len(values) != zones ** 2 or min(values) < 0:
            raise ValueError(f"{name}非332类方阵或包含负需求")
    # This adapter only claims the experimentally established fixed incremental pipeline.
    params = read_text(source / "params/Mparam631.txt").splitlines()
    values = [float(x.strip()) for x in params if x.strip() and x.strip().replace(".", "", 1).isdigit()]
    if values[:6] != [0, 0, 0, 1, 0, 0] or values[6:7] != [5]:
        raise ValueError("当前适配器只验证了5次多路径增量加载；请先单独验证其他分配方法")
    return {"directed_links": len(geometry(source)), "zones": zones,
            "assignment": "multipath_incremental_5_iterations", "equilibrium_claim": False}


def execute(scenario, runtime, logs, timeout, event, required_outputs=(), assignment_module="M6AssignVehicle.exe", bridge_environment=None):
    # The alternative shipped binary is available only to explicit diagnostic callers.
    # User task fields and the production agent cannot choose arbitrary executables.
    if assignment_module not in ("M6AssignVehicle.exe", "M6AssignVehicle1.exe"):
        raise ValueError("未允许的分配模块")
    assignment = runtime / "programs" / assignment_module
    if not assignment.is_file() or assignment.read_bytes()[:2] != b"MZ":
        raise ValueError("分配程序缺失或仍为Git LFS指针")
    modules = tuple(assignment_module if name == "M6AssignVehicle.exe" else name for name in MODULES)
    environment = None
    if bridge_environment is not None:
        if assignment_module != "M6AssignVehicle1.exe" or set(bridge_environment) != {"TRAFFIC_AGENT_PYTHON", "TRAFFIC_AGENT_BRIDGE_SCRIPT", "TRAFFIC_AGENT_BRIDGE_CONFIG"}:
            raise ValueError("未允许的桥接环境")
        environment = dict(os.environ, **bridge_environment)
    logs.mkdir(parents=True, exist_ok=False)
    manifest = {"scenario": str(scenario), "status": "running", "started": now(), "modules": []}
    manifest_path = logs / "run.json"
    before = input_fingerprints(scenario)
    save_json(logs / "inputs_before.json", before)
    config = runtime / "files/runinfo_vc6.txt"
    original = config.read_bytes()
    started_ns = time.time_ns()
    active = None
    try:
        config.write_bytes(("scenario_dir\r\n" + scenario.as_posix() + "\r\n").encode("gb18030"))
        for module in modules:
            record = {"module": module, "started": now(), "status": "running"}
            manifest["modules"].append(record)
            save_json(manifest_path, manifest)
            event("tool_start", {"tool": module, "scenario": scenario.parent.name})
            started = time.monotonic()
            with (logs / f"{module}.stdout.bin").open("wb") as out, (logs / f"{module}.stderr.bin").open("wb") as err:
                active = subprocess.Popen([str(runtime / "programs" / module)], cwd=runtime / "programs",
                                          stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                          creationflags=subprocess.CREATE_NO_WINDOW, env=environment)
                record["pid"] = active.pid
                save_json(manifest_path, manifest)
                try:
                    code = active.wait(timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    record["status"] = "timeout"
                    raise RuntimeError(f"{module}超过{timeout}秒，任务失败") from exc
            record.update(returncode=code, elapsed_seconds=round(time.monotonic() - started, 3), finished=now())
            errors = []
            for stream in ("stdout", "stderr"):
                text = (logs / f"{module}.{stream}.bin").read_bytes().decode("gb18030", errors="replace")
                (logs / f"{module}.{stream}.txt").write_text(text, encoding="utf-8")
                if "[error]" in text.lower():
                    errors.append(stream)
            if code or errors:
                record["status"] = "failed"
                raise RuntimeError(f"{module}失败，退出码{code}，错误流{errors}，参见{logs}")
            record["status"] = "completed"
            save_json(manifest_path, manifest)
            event("tool_end", record)
        fresh = {}
        for relative in [f"result/{name}" for name in OUTPUTS.values()] + ["cache/CapLinkVehMan.dat", "cache/Assign_car.od"] + list(required_outputs):
            path = scenario / relative
            if not path.is_file() or path.stat().st_size == 0 or path.stat().st_mtime_ns < started_ns - 2_000_000_000:
                raise ValueError(f"缺失、空或陈旧输出: {relative}")
            fresh[relative] = {"bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
        after = input_fingerprints(scenario)
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        if changed:
            raise ValueError(f"运行期间输入被模型改写，统一对比条件失效: {changed}")
        rows = results(scenario)
        source_od = numeric(scenario / "result/ODCar.tsd")
        assigned_od = numeric(scenario / "cache/Assign_car.od")
        if len(source_od) != len(assigned_od) or min(assigned_od) < 0 or sum(source_od) <= 0:
            raise ValueError("内部小汽车分配OD校验失败")
        manifest.update(status="completed", fresh_outputs=fresh, input_changes_during_run=changed,
                        assigned_car_od_sum=sum(assigned_od), source_car_od_sum=sum(source_od),
                        assigned_to_source_ratio=sum(assigned_od) / sum(source_od), result_rows=len(rows))
        return rows, manifest
    except BaseException as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if active is not None and active.poll() is None:
            subprocess.run(["taskkill", "/PID", str(active.pid), "/T", "/F"], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
            active.wait(timeout=10)
        config.write_bytes(original)
        manifest["finished"] = now()
        save_json(manifest_path, manifest)
