"""Run existing TranStar model executables in an isolated runtime and record evidence."""
import argparse
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(scenario, modules, timeout):
    scenario = Path(scenario).resolve()
    if not scenario.is_relative_to(ROOT) or not (scenario / "data").is_dir():
        raise ValueError("Scenario must be a prepared working copy inside this experiment")
    runtime = ROOT / "runtime"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logs = ROOT / "runs" / (scenario.parent.name + "_" + stamp)
    logs.mkdir(parents=True, exist_ok=False)
    (runtime / "files/runinfo_vc6.txt").write_bytes(
        ("scenario_dir\r\n" + scenario.as_posix() + "\r\n").encode("gb18030")
    )
    manifest = {"scenario": str(scenario), "started": datetime.now().isoformat(), "modules": []}
    manifest_path = logs / "run.json"
    before = {str(p.relative_to(scenario)): [p.stat().st_mtime_ns, p.stat().st_size]
              for p in scenario.rglob("*") if p.is_file()}
    (logs / "files_before.json").write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
    for module in modules:
        exe = runtime / "programs" / module
        if exe.parent != runtime / "programs" or not exe.is_file():
            raise ValueError(f"Unknown model module: {module}")
        record = {"module": module, "started": datetime.now().isoformat(), "status": "running"}
        manifest["modules"].append(record)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        started = time.monotonic()
        print("START", module, flush=True)
        with (logs / (module + ".stdout.bin")).open("wb") as out, (logs / (module + ".stderr.bin")).open("wb") as err:
            process = subprocess.Popen([str(exe)], cwd=exe.parent, stdin=subprocess.DEVNULL,
                                       stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
            record["pid"] = process.pid
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                code = process.wait(timeout=timeout)
                record.update(returncode=code, status="exited")
            except subprocess.TimeoutExpired:
                # Stop this task's process tree only; never terminate unrelated model runs.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=err, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
                record.update(status="timeout")
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        record["finished"] = datetime.now().isoformat()
        for kind in ["stdout", "stderr"]:
            raw = (logs / (module + f".{kind}.bin")).read_bytes()
            text = raw.decode("gb18030", errors="replace")
            (logs / (module + f".{kind}.txt")).write_text(text, encoding="utf-8")
            if "[error]" in text.lower():
                record["reported_error"] = True
            if text:
                print(kind, text[-5000:], flush=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print("END", module, record, flush=True)
        if record.get("returncode") != 0 or record.get("reported_error"):
            break
    after = {str(p.relative_to(scenario)): [p.stat().st_mtime_ns, p.stat().st_size]
             for p in scenario.rglob("*") if p.is_file()}
    changed = {k: v for k, v in after.items() if before.get(k) != v}
    manifest.update(finished=datetime.now().isoformat(), changed_files=changed,
                    note="Exit status alone does not establish valid model results; inspect output and freshness.")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("MANIFEST", manifest_path, "CHANGED", len(changed), flush=True)
    if any(m.get("returncode") != 0 or m.get("reported_error") for m in manifest["modules"]):
        raise RuntimeError(f"Model execution failed; inspect {manifest_path}")
    return manifest_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("modules", nargs="+")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    run(args.scenario, args.modules, args.timeout)
