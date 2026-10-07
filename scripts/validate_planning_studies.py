"""One explicitly requested online plan and at most two fresh diagnosis runs.

The service owns credentials. This driver never reads or receives the API key.
An existing output directory is refused; errors are retained without retry.
"""
import argparse
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import save_json
from traffic_agent.runner import now

REQUEST = "长江村路和红景路高峰可能拥堵。先只做这两条道路的现状体检，分别定位问题，不执行治理，不做敏感性，不复跑，总共最多2次仿真。说明后续治理的备选方向与取舍，但本次不要执行这些治理措施。"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-api", action="store_true")
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live_api:
        parser.error("This check costs one model request; explicitly pass --live-api")
    args.output.mkdir(parents=True, exist_ok=False)
    record = dict(started=now(), request=REQUEST, status="started", api_requests_attempted=0,
                  native_run_budget=2 if args.native else 0, native_launched=False)
    def api(path, body=None):
        request = Request(f"http://127.0.0.1:{args.port}" + path,
                          json.dumps(body).encode() if body is not None else None,
                          {"Content-Type": "application/json"})
        with urlopen(request, timeout=65) as response:
            return json.load(response)
    try:
        record["api_requests_attempted"] = 1
        save_json(args.output/"acceptance.json", record)
        proposed = api("/api/dispatch/preview", dict(matcher="model", request=REQUEST))
        save_json(args.output/"plan.json", proposed)
        record["model_usage"] = proposed.get("model_usage")
        if proposed.get("matched_at", "") < record["started"]:
            raise ValueError("返回的是已存在的缓存规划，不能作为本次新模型调用证据")
        if proposed["status"] != "ready":
            raise ValueError("模型没有形成可执行诊断规划；保存返回结果，不重试")
        tasks = [e["task"] for e in proposed["experiments"]]
        if (len(tasks) != 2 or {t["road_name"] for t in tasks} != {"长江村路", "红景路"}
                or any(t["scenario"] != "diagnosis" or t["demand_factors"] != [1.0]
                       or t["repeat_baseline"] or t["max_runs"] != 1 for t in tasks)):
            raise ValueError("线上规划不满足两道路、无治理、两次上限，未启动原生执行")
        record["status"] = "planning_verified"
        if args.native:
            launched = api("/api/dispatch/run", {"ticket": proposed["ticket"]})
            if launched.get("reused"):
                raise ValueError("返回已有任务，不能当作本次新原生实验")
            record.update(native_launched=True, job_ids=launched["job_ids"])
            save_json(args.output/"acceptance.json", record)
            print(json.dumps(dict(job_ids=launched["job_ids"], model_usage=record["model_usage"])), flush=True)
            deadline = time.monotonic()+1800
            while time.monotonic() < deadline:
                rows = api("/api/dispatch")
                dispatch = next(r for r in rows if r["job_id"] == launched["id"])
                if dispatch["status"] not in ("starting", "running"):
                    save_json(args.output/"dispatch.json", dispatch)
                    if dispatch["status"] != "completed":
                        raise ValueError("原生任务没有全部完成，保留状态，不重试")
                    summaries = []
                    for job_id in launched["job_ids"]:
                        state = api("/api/jobs/"+job_id)["state"]
                        if (state["status"] != "completed" or state["run_count"] != 1
                                or any(r["changes"] for r in state["runs"])
                                or not state.get("diagnostic_observations")):
                            raise ValueError("实际执行与诊断规划不一致")
                        summaries.append(dict(id=job_id, status=state["status"], run_count=state["run_count"],
                                              observations=state["diagnostic_observations"],
                                              executed_modules=sum(len(r["execution"].get("modules", [])) for r in state["runs"])))
                    record.update(status="verified", native=summaries)
                    break
                time.sleep(3)
            else:
                raise TimeoutError("达到等待上限；保留正在运行任务，不终止或重启")
    except Exception as exc:
        record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        record["finished"] = now()
        save_json(args.output/"acceptance.json", record)
    print(json.dumps(record, ensure_ascii=False), flush=True)
    return 0 if record["status"] in ("verified", "planning_verified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
