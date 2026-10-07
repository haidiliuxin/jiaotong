"""Strictly audit, seal, and export an already completed guarded challenge."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.archive import write_archive
from traffic_agent.guard_challenge import KIND as V1_KIND
from traffic_agent.guard_challenge_resilient import KIND as V2_KIND
from traffic_agent.guard_challenge_resilient_v3 import KIND as V3_KIND
from traffic_agent.guard_challenge_runner import render_report as render_v1
from traffic_agent.guard_challenge_resilient_runner import render_report as render_v2
from traffic_agent.guard_challenge_resilient_runner_v3 import render_report as render_v3
from traffic_agent.study_audit import verify
from scripts.report_guard_challenge import export


def finalize(job, output, wait_seconds):
    deadline = time.monotonic() + wait_seconds
    while True:
        state = json.loads((job/'state.json').read_text(encoding='utf-8'))
        if state.get('kind') not in (V1_KIND, V2_KIND, V3_KIND):
            raise ValueError('仅接受已经启动的全网安全门槛同预算挑战')
        if state['status'] == 'failed':
            raise RuntimeError(state.get('error', '原生任务失败；不发布为通过'))
        if state['status'] == 'completed':
            selection = json.loads((job/'selection.json').read_text(encoding='utf-8'))
            report = job/'report.md'
            expected = {V1_KIND: render_v1, V2_KIND: render_v2,
                        V3_KIND: render_v3}[state['kind']](state, selection)
            if report.is_file() and report.read_text(encoding='utf-8') == expected:
                break
        if time.monotonic() >= deadline:
            raise TimeoutError('任务尚未完成；保留原运行进程和证据，不发布结果')
        time.sleep(min(5, max(0, deadline-time.monotonic())))
    if output.exists():
        raise FileExistsError('成果目录已存在，不覆盖')
    if (job/'archive_manifest.json').exists():
        verification = verify(job, archived=True, write=False)
    else:
        verification = verify(job, write=True)
        verification['sealed_files'] = len(write_archive(job)['files'])
    print(json.dumps({k: v for k, v in verification.items() if k != 'runs'},
                     ensure_ascii=False, indent=2), flush=True)
    print(f'EXPORTED {export(job, output)}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--wait-seconds', type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.wait_seconds <= 21600:
        parser.error('等待上限为0至21600秒')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    finalize(args.job.resolve(), args.output.resolve(), args.wait_seconds)
