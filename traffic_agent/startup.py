"""One local entry point: dependencies, writable output, port and evidence."""
import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from .data import PROJECT, save_json
from .preflight import check
from .runner import DEFAULT_SOURCE, EXPERIMENTS, now
from .task import Task
from .storage import estimate


def local_proxy_bypass():
    # Changes this Python process and its children only, not Windows or the shell.
    for key in ('NO_PROXY', 'no_proxy'):
        parts = [x.strip() for x in os.environ.get(key, '').split(',') if x.strip()]
        os.environ[key] = ','.join(dict.fromkeys(parts + ['127.0.0.1', 'localhost', '::1']))


def environment(task, source, root, port=None):
    errors = []
    if sys.version_info[:2] != (3, 12):
        errors.append('请使用Python 3.12；其他版本尚未作为交付环境验证')
    try:
        result = check(task, source)
        errors += result['errors']
    except (OSError, ValueError) as exc:
        result = dict(preflight_passed=False, errors=[str(exc)])
        errors.append(f'源数据检查失败：{exc}')
    output = dict(path=str(root), writable=False, free_bytes=None)
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=root) as stream:
            stream.write(b'output-check')
            stream.flush()
        output.update(writable=True, free_bytes=shutil.disk_usage(root).free)
        output['storage'] = estimate([task], source, root)
        if not output['storage']['passed']:
            storage = output['storage']
            errors.append(f"磁盘空间不足：可用{storage['free_bytes']/1024**3:.2f}GiB，按任务预算估算需{storage['estimated_required_bytes']/1024**3:.2f}GiB")
    except OSError as exc:
        errors.append(f'输出目录不可写：{exc}')
    if port is not None:
        if not 1 <= port <= 65535:
            errors.append('端口应在1至65535之间')
        else:
            try:
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', port))
            except OSError:
                errors.append(f'端口{port}不可用；请更换-Port，或在原服务窗口按Ctrl+C停止服务')
    # Source content hashes distinguish an uncommitted working tree from HEAD.
    paths = sorted((PROJECT/'traffic_agent').rglob('*.py')) + [PROJECT/'start.ps1']
    hashes = {p.relative_to(PROJECT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=PROJECT, capture_output=True, text=True).stdout.strip() if shutil.which('git') else None
    return dict(checked_at=now(), passed=not errors, errors=errors, dependencies=result, output=output,
                python=sys.version, executable=sys.executable, platform=sys.platform, code_commit=commit,
                working_code_sha256=hashes, task=task.as_dict(), port=port,
                proxy_scope='本Python进程及子进程；仅本地地址绕过代理，不更改系统设置',
                warning='依赖检查不代表模型授权或实际响应通过；磁盘估算会在新场景前复检，不能保证原生输出大小或其他进程占用')


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('check', 'serve', 'run'), default='serve')
    parser.add_argument('--task', type=Path, default=PROJECT/'examples/parking_quickstart_v2.json')
    parser.add_argument('--port', type=int, default=8769)
    parser.add_argument('--compiler-bin', type=Path)
    args = parser.parse_args()
    local_proxy_bypass()
    if args.compiler_bin:
        os.environ['PATH'] = str(args.compiler_bin.resolve()) + os.pathsep + os.environ.get('PATH', '')
    try:
        task = Task.from_dict(json.loads(args.task.read_text(encoding='utf-8-sig')))
        result = environment(task, DEFAULT_SOURCE, EXPERIMENTS, args.port if args.mode == 'serve' else None)
        import uuid
        record = EXPERIMENTS/'startup'/f'{uuid.uuid4().hex}.json'
        save_json(record, result)
        print(json.dumps(dict(startup_record=str(record), **result), ensure_ascii=False, indent=2), flush=True)
        if not result['passed']:
            return 2
        if args.mode == 'serve':
            from .web import serve
            print(f'打开 http://127.0.0.1:{args.port}；在本窗口按Ctrl+C停止服务。', flush=True)
            serve(DEFAULT_SOURCE, EXPERIMENTS, args.port)
        elif args.mode == 'run':
            from .agent import Agent
            agent = Agent(task)
            print(f'JOB {agent.job}', flush=True)
            result['job'] = str(agent.job)
            save_json(record, result)
            agent.run()
            result.update(finished_at=now(), job_status=agent.state['status'])
            save_json(record, result)
            return 0 if agent.state['status'] == 'completed' else 2
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'启动/运行失败：{exc}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
