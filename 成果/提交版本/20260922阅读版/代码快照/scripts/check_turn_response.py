"""Fail explicitly when a completed model run did not suppress a requested turn."""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.circulation_agent import turn_response


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    parser.add_argument("scenario")
    args = parser.parse_args()
    job = args.job.resolve()
    state = json.loads((job / "state.json").read_text(encoding="utf-8"))
    record = next(r for r in state["runs"] if r["name"] == args.scenario)
    if record["execution"]["status"] != "completed" or not any(c.get("kind") == "turn" for c in record["changes"]):
        raise ValueError("目标必须为已完成且实际新增禁转的场景")
    base = next(r for r in state["runs"] if r["demand_factor"] == record["demand_factor"] and not r["changes"])
    response = turn_response(SimpleNamespace(job=job), base, record)
    print(json.dumps({k: response[k] for k in ("passed", "cache_rules_exact", "cached_rule_count", "movements")}, ensure_ascii=False, indent=2))
    return 0 if response["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
