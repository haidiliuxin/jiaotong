"""Loopback-only workbench; execution uses the same agent as the CLI."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .agent import Agent, plan
from .source_context import display_dataset
from .task import Task, parse_request

STATIC = Path(__file__).with_name("static")
JOB_ID = re.compile(r"\d{8}_\d{6}_[0-9a-f]{6}")
ARTIFACTS = {"report.html": "text/html", "report.md": "text/plain", "metrics.csv": "text/csv",
             "dispatch.json": "application/json",
             "state.json": "application/json", "plan.json": "application/json", "events.jsonl": "text/plain"}
UNREADABLE_JOB = "任务记录不可读，请选择其他历史任务"
RECORD_ERRORS = (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError, OverflowError)


def check_text_list(value):
    if not isinstance(value, list) or any(not isinstance(text, str) for text in value):
        raise ValueError(UNREADABLE_JOB)


def check_comparison(value):
    if not isinstance(value, dict):
        raise ValueError(UNREADABLE_JOB)
    check_text_list(value.get("violations"))
    if 'delta' in value and (not isinstance(value['delta'], dict)
            or any(not isinstance(delta, dict) for delta in value['delta'].values())):
        raise ValueError(UNREADABLE_JOB)


def read_job_state(path):
    """Validate saved record structure without changing historical evidence."""
    state = json.loads((path / "state.json").read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or state.get("id") != path.name
            or not JOB_ID.fullmatch(path.name)
            or not isinstance(state.get("status"), str) or not state["status"]
            or not isinstance(state.get("started"), str)
            or type(state.get("run_count")) is not int or state["run_count"] < 0
            or not isinstance(state.get("task"), dict)
            or (state.get("recommendation") is not None and not isinstance(state["recommendation"], str))):
        raise ValueError(UNREADABLE_JOB)
    for key in ("runs", "decisions", "sensitivity"):
        if not isinstance(state.get(key), list) or any(not isinstance(row, dict) for row in state[key]):
            raise ValueError(UNREADABLE_JOB)
    task = state["task"]
    factors = task.get("demand_factors")
    if (not isinstance(task.get("request"), str)
            or not isinstance(factors, list) or not factors
            or any(type(factor) not in (int, float) or factor <= 0 for factor in factors)
            or any(type(task.get(key)) is not int or task[key] <= 0 for key in ("max_runs", "max_links"))):
        raise ValueError(UNREADABLE_JOB)
    if task.get("turn") is not None and not isinstance(task["turn"], list):
        raise ValueError(UNREADABLE_JOB)
    if task.get("links") is not None and (not isinstance(task["links"], list)
            or any(not isinstance(pair, list) for pair in task["links"])):
        raise ValueError(UNREADABLE_JOB)
    if state["status"] == "needs_validation":
        check_text_list(state.get("missing"))
    for run in state["runs"]:
        if (not isinstance(run.get("name"), str)
                or type(run.get("demand_factor")) not in (int, float)
                or not isinstance(run.get("changes"), list)
                or any(not isinstance(change, dict) for change in run["changes"])
                or not isinstance(run.get("execution"), dict)
                or (run.get("metrics") is not None and not isinstance(run["metrics"], dict))):
            raise ValueError(UNREADABLE_JOB)
        if run.get("metrics") is not None and any(
                not isinstance(run["metrics"].get(area), dict) for area in ("core", "neighbor", "network")):
            raise ValueError(UNREADABLE_JOB)
        if "comparison" in run:
            check_comparison(run["comparison"])
        response = run.get("turn_response")
        if response is not None:
            if not isinstance(response, dict):
                raise ValueError(UNREADABLE_JOB)
            movements = response.get("movements") or []
            if not isinstance(movements, list):
                raise ValueError(UNREADABLE_JOB)
            for movement in movements:
                if not isinstance(movement, dict) or not isinstance(movement.get("turn"), list):
                    raise ValueError(UNREADABLE_JOB)
                # The earlier native TVolNode writer saved car-only scalars.
                # Four-mode flows are consumed only with the bridge profile.
                if state.get('turn_access_profile') is not None and (
                        not isinstance(movement.get('flows'), dict)
                        or any(not isinstance(movement['flows'].get(mode), dict)
                               for mode in ('car', 'taxi', 'motor', 'truck'))):
                    raise ValueError(UNREADABLE_JOB)
    for decision in state["decisions"]:
        if (not isinstance(decision.get("observed_run"), str)
                or not isinstance(decision.get("reason"), str)
                or not isinstance(decision.get("violations"), list)
                or any(not isinstance(text, str) for text in decision["violations"])
                or ("execution_reason" in decision and not isinstance(decision["execution_reason"], str))):
            raise ValueError(UNREADABLE_JOB)
    for item in state["sensitivity"]:
        check_comparison(item.get("comparison"))
        adaptation = item.get("adaptation")
        if adaptation is not None:
            if not isinstance(adaptation, dict):
                raise ValueError(UNREADABLE_JOB)
            if "comparison" in adaptation:
                check_comparison(adaptation["comparison"])
    if 'planner_trace' in state and (not isinstance(state['planner_trace'], list)
            or any(not isinstance(row, dict) for row in state['planner_trace'])):
        raise ValueError(UNREADABLE_JOB)
    for key, child in (("search_trace", "ranking"), ("diagnostic_observations", "top_roads")):
        rows = state.get(key) or []
        if (not isinstance(rows, list) or any(not isinstance(row, dict)
                or not isinstance(row.get(child), list)
                or any(not isinstance(entry, dict) for entry in row[child]) for row in rows)):
            raise ValueError(UNREADABLE_JOB)
    selection = state.get("selection_analysis")
    if selection is not None:
        if not isinstance(selection, dict):
            raise ValueError(UNREADABLE_JOB)
        check_text_list(selection.get("equivalent_candidates"))
        candidates = selection.get("candidates")
        if (not isinstance(candidates, list) or any(not isinstance(candidate, dict)
                or not isinstance(candidate.get("complexity"), list) or len(candidate["complexity"]) < 2
                or not isinstance(candidate.get("difference_from_selected"), list) for candidate in candidates)):
            raise ValueError(UNREADABLE_JOB)
    # Python's JSON reader accepts NaN/Infinity, while the API deliberately does not.
    json.dumps(state, allow_nan=False)
    return state


def load_job(path):
    state = read_job_state(path)
    entries = []
    skipped = 0
    if (path / "events.jsonl").exists():
        for line in (path / "events.jsonl").read_text(encoding="utf-8").splitlines()[-40:]:
            try:
                entry = json.loads(line)
                if (not isinstance(entry, dict) or not isinstance(entry.get("time"), str)
                        or not isinstance(entry.get("event"), str)
                        or not isinstance(entry.get("data"), (dict, list))):
                    raise ValueError(UNREADABLE_JOB)
                data, kind = entry["data"], entry["event"]
                if kind in ("completion_check", "model_policy_assessment", "search_assessment"):
                    if not isinstance(data, dict):
                        raise ValueError(UNREADABLE_JOB)
                    flag, texts = (("complete", "unanswered") if kind == "completion_check"
                                   else ("eligible", "violations"))
                    if (type(data.get(flag)) is not bool
                            or (not data[flag] and (not isinstance(data.get(texts), list)
                                or any(not isinstance(text, str) for text in data[texts])))):
                        raise ValueError(UNREADABLE_JOB)
                json.dumps(entry, allow_nan=False)
                entries.append(entry)
            except (ValueError, TypeError):
                # Partial writes and malformed event rows are excluded, never rewritten.
                skipped += 1
    from .decision_analysis import decision_analysis
    from .tradeoffs import tradeoffs, selection_rule
    from .planning_review import coverage
    from .conclusion_review import assess_state
    from .professional_review import review as professional_review
    return {"state": state, "dataset": display_dataset(state.get("source")), "goal_review":coverage(state),
            "conclusion_review":assess_state(state),
            "professional_review":professional_review(state),
            "events": entries, "events_skipped": skipped, "analysis": decision_analysis(state),
            "decision_review": decision_analysis(state, 'tolerance-v2'),
            "tradeoffs": tradeoffs(state), "selection_rule": selection_rule(state['task'])}


def make_handler(source, root):
    source, root = Path(source).resolve(), Path(root).resolve()

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, body, content_type="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            static = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                      "/map.js": ("map.js", "text/javascript"),
                      "/map_view.js": ("map_view.js", "text/javascript"),
                      "/style.css": ("style.css", "text/css")}
            if path in static:
                name, mime = static[path]
                return self.respond(200, (STATIC / name).read_bytes(), mime)
            if path == "/api/jobs":
                jobs = []
                for state_file in sorted((root / "jobs").glob("*/state.json"), reverse=True):
                    try:
                        state = read_job_state(state_file.parent)
                        jobs.append({k: state.get(k) for k in ("id", "status", "started", "run_count", "recommendation")})
                    except RECORD_ERRORS:
                        jobs.append(dict(id=state_file.parent.name, status="unreadable", started=None,
                                         run_count=None, recommendation=None, error=UNREADABLE_JOB))
                return self.respond(200, jobs)
            parts = path.strip("/").split("/")
            if len(parts) in (3, 4) and parts[:2] == ["api", "jobs"] and JOB_ID.fullmatch(parts[2]):
                job = root / "jobs" / parts[2]
                if len(parts) == 4 and parts[3] == 'map' and (job/'state.json').is_file():
                    from .map_data import map_data
                    try:
                        read_job_state(job)
                    except RECORD_ERRORS:
                        return self.respond(409, {"error": UNREADABLE_JOB})
                    try:
                        query = parse_qs(urlparse(self.path).query)
                        return self.respond(200, map_data(job, query.get('run', [''])[0]))
                    except (ValueError, OSError, KeyError) as exc:
                        return self.respond(400, {'error': str(exc)})
                if len(parts) == 3 and (job / "state.json").exists():
                    try:
                        return self.respond(200, load_job(job))
                    except RECORD_ERRORS:
                        return self.respond(409, {"error": UNREADABLE_JOB})
                if len(parts) == 4 and parts[3] in ARTIFACTS and (job / parts[3]).is_file():
                    return self.respond(200, (job / parts[3]).read_bytes(), ARTIFACTS[parts[3]])
            self.respond(404, {"error": "没有找到该任务或报告"})

        def do_POST(self):
            port = self.server.server_address[1]
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if host not in allowed or (origin and origin not in {"http://" + h for h in allowed}):
                return self.respond(403, {"error": "仅接受本地同源任务请求"})
            if self.headers.get_content_type() != "application/json":
                return self.respond(415, {"error": "任务必须为application/json"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("任务请求长度无效")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("任务请求必须为对象")
                if "task" in body:
                    task = Task.from_dict(body["task"])
                else:
                    task = parse_request(body.get("request", ""))
                    overrides = body.get("overrides", {})
                    if not isinstance(overrides, dict):
                        raise ValueError("任务配置必须为对象")
                    task = Task.from_dict(dict(task.as_dict(), **overrides))
                if self.path == "/api/plan":
                    return self.respond(200, plan(task, source))
                if self.path != "/api/run":
                    return self.respond(404, {"error": "未知操作"})
                agent = Agent(task, source, root)
                def work():
                    try:
                        agent.run()
                    except Exception:
                        pass  # Agent persists the failure and concrete diagnostic message.
                threading.Thread(target=work, name=agent.job_id, daemon=False).start()
                self.respond(202, {"id": agent.job_id})
            except (ValueError, TypeError, KeyError) as exc:
                self.respond(400, {"error": str(exc)})

        def log_message(self, message, *args):
            pass

    from .reference_map import with_reference_map
    from .dispatch_web import with_dispatch
    return with_dispatch(with_reference_map(Handler, source, root), source, root)


def serve(source, root, port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(source, root))
    print(f"本地交通仿真工作台 http://127.0.0.1:{port} （Ctrl+C停止网页服务）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.RequestHandlerClass.dispatcher.close()
        server.server_close()
