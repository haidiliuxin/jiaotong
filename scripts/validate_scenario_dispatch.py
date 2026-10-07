"""Opt-in bounded live check: two classifications, at most one native job.

Run after local tests, against the local hall server. Never contains or logs keys.
"""
import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import save_json
from traffic_agent.runner import now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-api", action="store_true", required=True)
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("验收记录已存在；不覆盖，不自动重跑")
    opener = build_opener(ProxyHandler({}))
    def api(path, body=None):
        req = Request(f"http://127.0.0.1:{args.port}" + path,
                      json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                      {"Content-Type": "application/json"})
        try:
            with opener.open(req, timeout=130) as response:
                return json.load(response)
        except HTTPError as exc:
            raise ValueError(json.load(exc).get("error", "本地请求失败")) from None
    report = dict(started=now(), purpose="情景匹配与批次调度有限验收", api_request_limit=2,
                  cases=[], native_job=None, status="running")
    save_json(args.output, report)
    try:
        first = None
        for text, expected in (("路边车辆乱停造成通行干扰，请评估停车治理并检查邻区影响；总预算3次；最多治理1条；不做敏感性；不复跑", "ready"),
                               ("道路施工占用一条车道，评估施工期间交通组织", "needs_validation")):
            result = api("/api/dispatch/preview", dict(request=text, matcher="model"))
            report["cases"].append(result)
            save_json(args.output, report)
            print(json.dumps(dict(status=result["status"], scenario=result["scenario"], model_usage=result["model_usage"]), ensure_ascii=False), flush=True)
            if result["status"] != expected:
                raise ValueError("代表情景未返回预期分类状态；停止后续验收，不自动重试")
            if first is None:
                first = result
        if args.native:
            launched = api("/api/dispatch/run", {"ticket": first["ticket"]})
            report["native_job"] = launched["id"]
            save_json(args.output, report)
            print("native_job=" + launched["id"], flush=True)
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                # Read-only status polling; no model API or additional native launch.
                records = api("/api/dispatch")
                state = next((r for r in records if r.get("job_id") == launched["id"]), None)
                if state and state["status"] not in ("starting", "created", "running"):
                    report["native_status"] = state["status"]
                    job = api("/api/jobs/" + launched["id"])["state"]
                    report["run_count"] = job["run_count"]
                    report["recommendation"] = job.get("recommendation")
                    report["decisions"] = job.get("decisions")
                    if state["status"] not in ("completed", "no_candidate"):
                        raise ValueError("原生任务未正常完成；保留执行证据")
                    break
                time.sleep(3)
            else:
                raise ValueError("验收等待超时；原生任务未被停止，请查运行记录")
        report["status"] = "completed"
    except Exception as exc:
        report.update(status="failed", error=str(exc)[:500])
    finally:
        report["finished"] = now()
        save_json(args.output, report)
    print(json.dumps({k:report.get(k) for k in ("status", "native_job", "native_status", "run_count", "error")}, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
