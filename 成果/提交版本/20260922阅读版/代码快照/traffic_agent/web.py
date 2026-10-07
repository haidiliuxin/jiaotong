"""Loopback-only workbench; execution uses the same agent as the CLI."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .agent import Agent, plan
from .task import Task, parse_request

STATIC = Path(__file__).with_name("static")
JOB_ID = re.compile(r"\d{8}_\d{6}_[0-9a-f]{6}")
ARTIFACTS = {"report.html": "text/html", "report.md": "text/plain", "metrics.csv": "text/csv",
             "state.json": "application/json", "plan.json": "application/json", "events.jsonl": "text/plain"}


def load_job(path):
    state = json.loads((path / "state.json").read_text(encoding="utf-8"))
    entries = []
    if (path / "events.jsonl").exists():
        for line in (path / "events.jsonl").read_text(encoding="utf-8").splitlines()[-40:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # The writer may currently be appending the last line.
    from .decision_analysis import decision_analysis
    from .tradeoffs import tradeoffs, selection_rule
    return {"state": state, "events": entries, "analysis": decision_analysis(state),
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
                      "/style.css": ("style.css", "text/css")}
            if path in static:
                name, mime = static[path]
                return self.respond(200, (STATIC / name).read_bytes(), mime)
            if path == "/api/jobs":
                jobs = []
                for state_file in sorted((root / "jobs").glob("*/state.json"), reverse=True):
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                    jobs.append({k: state.get(k) for k in ("id", "status", "started", "run_count", "recommendation")})
                return self.respond(200, jobs)
            parts = path.strip("/").split("/")
            if len(parts) in (3, 4) and parts[:2] == ["api", "jobs"] and JOB_ID.fullmatch(parts[2]):
                job = root / "jobs" / parts[2]
                if len(parts) == 4 and parts[3] == 'map' and (job/'state.json').is_file():
                    from .map_data import map_data
                    try:
                        query = parse_qs(urlparse(self.path).query)
                        return self.respond(200, map_data(job, query.get('run', [''])[0]))
                    except (ValueError, OSError, KeyError) as exc:
                        return self.respond(400, {'error': str(exc)})
                if len(parts) == 3 and (job / "state.json").exists():
                    return self.respond(200, load_job(job))
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
                    return self.respond(200, plan(task))
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

    return Handler


def serve(source, root, port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(source, root))
    print(f"本地交通仿真工作台 http://127.0.0.1:{port} （Ctrl+C停止网页服务）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
