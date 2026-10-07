"""Same-origin model settings and scenario dispatch endpoints."""
import json
import threading
from urllib.parse import urlparse

from .dispatch import Dispatcher
from .llm_client import client_for, ModelError
from .llm_settings import Settings, redact
from .natural_plan import capabilities


def with_dispatch(handler, source, root):
    settings = Settings()
    manager = Dispatcher(source, root, settings)
    test_lock = threading.Lock()

    class DispatchHandler(handler):
        dispatcher = manager

        def _local(self):
            port = self.server.server_address[1]
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in allowed or self.headers.get("Origin") not in (None, *("http://" + h for h in allowed)):
                self.respond(403, {"error": "仅接受本地同源请求"})
                return False
            return True

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/dispatch.js", "/dispatch.css", "/planner_view.js", "/choice_view.js"):
                from .web import STATIC
                return self.respond(200, (STATIC / path[1:]).read_bytes(), "text/javascript" if path.endswith(".js") else "text/css")
            if path in ("/api/settings/llm", "/api/dispatch", "/api/planning-attempts", "/api/planning-quality", "/api/planning-capabilities"):
                if not self._local():
                    return
                try:
                    if path == '/api/planning-quality':
                        from .planning_attempt import quality
                        return self.respond(200,quality(root))
                    if path == '/api/planning-capabilities':
                        return self.respond(200, capabilities())
                    if path == "/api/planning-attempts":
                        from .planning_attempt import listing
                        return self.respond(200, listing(manager.root))
                    return self.respond(200, settings.public() if path == "/api/settings/llm" else manager.listing())
                except (ValueError, OSError):
                    return self.respond(400, {"error": "本机配置或调度记录不可读"})
            return super().do_GET()

        def _mutation(self):
            if not self._local():
                return
            if self.headers.get_content_type() != "application/json":
                return self.respond(415, {"error": "请求必须为 application/json"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    raise ValueError("请求长度无效")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("请求必须为对象")
                path = urlparse(self.path).path
                if path == "/api/settings/llm" and self.command == "PUT":
                    result = settings.update(body)
                elif path == '/api/settings/llm/reset' and self.command == 'POST':
                    if body:
                        raise ValueError('损坏配置重置不接受额外字段')
                    result = settings.reset_corrupt()
                elif path == "/api/settings/llm/test" and self.command == "POST":
                    if not test_lock.acquire(blocking=False):
                        raise ValueError("连接测试正在进行；未追加请求")
                    try:
                        result = client_for(*settings.credentials()).test()
                    finally:
                        test_lock.release()
                elif path == '/api/dispatch/preflight' and self.command == 'POST':
                    if set(body)!={'request'}:
                        raise ValueError('本地检查仅接收自然语言描述')
                    from .natural_plan import preflight
                    result=preflight(body['request'],manager.source)
                elif path == "/api/dispatch/preview" and self.command == "POST":
                    result = manager.preview(body)
                elif path == "/api/dispatch/run" and self.command == "POST":
                    if set(body) != {"ticket"}:
                        raise ValueError("启动仅接受已经匹配的任务卡")
                    result = manager.launch(body["ticket"])
                else:
                    return self.respond(404, {"error": "未知接口"})
                return self.respond(200, result)
            except ModelError as exc:
                result = dict(error=redact(str(exc))[:500])
                if exc.requests is not None:
                    from .planning_attempt import safe_usage
                    requests = [safe_usage(row) or dict(tokens=None) for row in exc.requests]
                    tokens = sum(row.get('tokens') or 0 for row in requests)
                    unknown = any(row.get('tokens') is None for row in requests)
                    result.update(requests=requests)
                    result['error'] += f"；连接探针已尝试 {len(requests)} 次请求，已知 {tokens} tokens" + ('，部分用量未知' if unknown else '')
                return self.respond(400, result)
            except (ValueError, TypeError, KeyError, RuntimeError) as exc:
                return self.respond(400, {"error": redact(str(exc))[:500]})
            except OSError:
                return self.respond(503, {"error": "本地存储不可用，请检查目录权限"})

        def do_POST(self):
            if urlparse(self.path).path.startswith(("/api/settings/", "/api/dispatch")):
                return self._mutation()
            return super().do_POST()

        def do_PUT(self):
            return self._mutation()

    return DispatchHandler
