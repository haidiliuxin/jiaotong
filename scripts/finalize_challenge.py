"""Finish one already-started challenge: wait, strictly audit, seal, export.

This is a bounded pipeline join, not a model scheduler. It never launches another
simulation, resumes a failed job, edits its outcomes, or overwrites a report.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.archive import write_archive
from traffic_agent.challenge import KIND
from traffic_agent.challenge_runner import render_report
from traffic_agent.study_audit import verify
from scripts.report_challenge import export


def finalize(job, output, wait_seconds):
    deadline = time.monotonic()+wait_seconds
    while True:
        state = json.loads((job/'state.json').read_text(encoding='utf-8'))
        if state.get('kind') != KIND:
            raise ValueError('只接受已经启动的三策略挑战')
        if state['status'] == 'failed':
            raise RuntimeError(state.get('error', '模型任务失败；不封存为通过'))
        if state['status'] == 'completed':
            selection = json.loads((job/'selection.json').read_text(encoding='utf-8'))
            report = job/'report.md'
            if report.is_file() and report.read_text(encoding='utf-8') == render_report(state, selection):
                break  # The runner writes its final report just after final state.
        if time.monotonic() >= deadline:
            raise TimeoutError('仍未完成；保留原运行进程和证据，不发布结果')
        time.sleep(min(5, max(0, deadline-time.monotonic())))
    if output.exists():
        raise FileExistsError('输出目录已存在，不自动改写已导出的成果')
    if (job/'archive_manifest.json').exists():
        result = verify(job, archived=True, write=False)
    else:
        result = verify(job, write=True)
        result['sealed_files'] = len(write_archive(job)['files'])
    print(json.dumps({k:v for k,v in result.items() if k != 'runs'},ensure_ascii=False,indent=2),flush=True)
    print(f'EXPORTED {export(job, output)}',flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description='等待单个已启动挑战结束，再严格核验和导出')
    parser.add_argument('job',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--wait-seconds',type=int,default=0)
    args=parser.parse_args()
    if not 0 <= args.wait_seconds <= 21600:
        parser.error('等待上限为0至21600秒')
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    finalize(args.job.resolve(),args.output.resolve(),args.wait_seconds)
