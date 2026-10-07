"""Check completed runs, fresh outputs, fixed inputs and baseline repeatability."""
import json
from pathlib import Path
from analyze_results import numeric

ROOT = Path(__file__).resolve().parent
SCENARIOS = ["baseline", "parking_level2", "parking_level1", "baseline_repeat"]
RESULTS = ["SpdLinkVeh.tsd", "VolLinkVeh.tsd", "VCLinkVeh.tsd", "TimeLink.tsd"]


def verify():
    all_runs = [(p, json.loads(p.read_text(encoding="utf-8"))) for p in (ROOT / "runs").glob("*/run.json")]
    report = {"runs": {}, "repeatability": {}, "interventions": {}, "passed": True}
    for name in SCENARIOS:
        path = ROOT / name / "基础路网"
        completed = [(p, r) for p, r in all_runs if Path(r["scenario"]) == path and r.get("finished")
                     and any(m["module"] == "M6AssignVehicle.exe" for m in r["modules"])]
        if not completed:
            raise ValueError(f"No completed assignment for {name}")
        p, record = max(completed, key=lambda item: item[1]["started"])
        success = all(m.get("returncode") == 0 and not m.get("reported_error") for m in record["modules"])
        fresh = {f: str(Path("result") / f) in record["changed_files"] for f in RESULTS}
        record_info = {"manifest": str(p), "all_modules_successful": success, "fresh_outputs": fresh,
                       "assignment_seconds": next(m["elapsed_seconds"] for m in record["modules"] if m["module"] == "M6AssignVehicle.exe")}
        report["runs"][name] = record_info
        report["passed"] &= success and all(fresh.values())
    for name in ["parking_level2", "parking_level1", "baseline_repeat"]:
        diffs = []
        for folder in ["data", "params"]:
            for p in (ROOT / "baseline/基础路网" / folder).iterdir():
                q = ROOT / name / "基础路网" / folder / p.name
                if p.is_file() and (not q.exists() or p.read_bytes() != q.read_bytes()):
                    diffs.append(str(Path(folder) / p.name))
        od_fixed = all(p.read_bytes() == (ROOT / name / "基础路网/result" / p.name).read_bytes()
                       for p in (ROOT / "baseline/基础路网/result").glob("OD*.tsd"))
        report["interventions"][name] = {"input_files_differing": diffs, "od_files_identical": od_fixed}
        expected = [] if name == "baseline_repeat" else [str(Path("data/manageL.txt"))]
        report["passed"] &= diffs == expected and od_fixed
    for f in RESULTS:
        a = numeric(ROOT / "baseline/基础路网/result" / f)
        b = numeric(ROOT / "baseline_repeat/基础路网/result" / f)
        if len(a) != len(b):
            raise ValueError("Repeat result array length mismatch")
        diffs = [abs(x - y) for x, y in zip(a, b)]
        report["repeatability"][f] = {"row_count": len(a), "different_numeric_rows": sum(d != 0 for d in diffs),
                                         "max_absolute_difference": max(diffs), "numeric_exact_match": all(d == 0 for d in diffs)}
    report["exact_repeatability"] = all(r["numeric_exact_match"] for r in report["repeatability"].values())
    # Numerical repeatability is reported explicitly; differing valid results need investigation.
    report["passed"] &= report["exact_repeatability"]
    (ROOT / "analysis/verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise ValueError("Verification concerns remain; see analysis/verification.json")


if __name__ == "__main__":
    verify()
