"""Bounded full-job retry for the observed native Rwm_car.dat open failure.

Every attempt starts at a new baseline and retains a separate task directory.
Policy failures, incomplete evidence, and other native errors never trigger a
retry. A successful attempt is strictly audited and sealed as one complete job.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.archive import write_archive
from traffic_agent.data import PROJECT, input_fingerprints, save_json
from traffic_agent.guard_challenge import KIND
from traffic_agent.guard_challenge_runner import GuardChallengeRunner, REGISTRY
from traffic_agent.runner import DEFAULT_SOURCE, now
from traffic_agent.study_audit import verify
from traffic_agent.study_runner import sha


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def runtime_hashes():
    paths = sorted((PROJECT/'traffic_agent').glob('*.py')) + [
        PROJECT/'scripts/benchmark_guard_challenge.py', REGISTRY,
        PROJECT/'scripts/supervise_guard_challenge.py']
    return {path.relative_to(PROJECT).as_posix(): sha(path) for path in paths}


def retryable_native_failure(job):
    """Return narrow failure evidence or None; never inspect policy outcomes."""
    state = read(job/'state.json')
    if state.get('kind') != KIND or state.get('status') != 'failed':
        return None
    if 'M6AssignVehicle.exe失败' not in state.get('error', ''):
        return None
    failed = []
    for path in sorted((job/'logs').glob('*/run.json')):
        manifest = read(path)
        if manifest.get('status') != 'failed':
            continue
        modules = manifest.get('modules', [])
        if not modules or modules[-1].get('module') != 'M6AssignVehicle.exe' or modules[-1].get('status') != 'failed':
            return None
        stream = path.parent/'M6AssignVehicle.exe.stdout.bin'
        raw = stream.read_bytes()
        message = raw.decode('gb18030', errors='replace')
        if ('[error]' not in message.lower() or 'Rwm_car.dat' not in message
                or '在打开以下数据文件' not in message):
            return None
        failed.append(dict(physical=path.parent.name,
                           log=path.relative_to(job).as_posix(),
                           stdout_sha256=sha(stream),
                           native_error=manifest.get('error')))
    if len(failed) != 1:
        return None
    return failed[0]


def supervise(output, max_attempts):
    output = output.resolve()
    if output.exists():
        raise FileExistsError('监督记录目录已存在，不覆盖')
    if not 1 <= max_attempts <= 3:
        raise ValueError('最多尝试1至3次完整任务')
    dirty = subprocess.run(['git', 'status', '--porcelain', '--',
                            'traffic_agent', 'config', 'scripts/benchmark_guard_challenge.py',
                            'scripts/supervise_guard_challenge.py'],
                           cwd=PROJECT, capture_output=True, text=True, check=True)
    if dirty.stdout.strip():
        raise ValueError('先提交控制器、协议和监督脚本；不以工作区临时修改启动重试')
    hashes = runtime_hashes()
    source_inputs = input_fingerprints(DEFAULT_SOURCE)
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=PROJECT,
                            capture_output=True, text=True, check=True).stdout.strip()
    output.mkdir(parents=True, exist_ok=False)
    record = dict(kind='guard_challenge_bounded_native_retry_v1', status='running',
                  started=now(), max_attempts=max_attempts, code_commit=commit,
                  code_sha256=hashes, source_inputs=source_inputs,
                  retry_rule='仅M6AssignVehicle.exe报告无法打开Rwm_car.dat时从零创建新任务；治理不合格或其他故障不重试',
                  attempts=[])
    save_json(output/'supervision.json', record)
    native_hashes = None
    for index in range(1, max_attempts+1):
        if runtime_hashes() != hashes or input_fingerprints(DEFAULT_SOURCE) != source_inputs:
            record.update(status='failed', error='监督期间源码或案例输入改变', finished=now())
            save_json(output/'supervision.json', record)
            raise ValueError(record['error'])
        runner = GuardChallengeRunner()
        entry = dict(number=index, job=str(runner.job), started=now(), status='running')
        record['attempts'].append(entry)
        save_json(output/'supervision.json', record)
        try:
            job = runner.run()
        except BaseException as exc:
            entry.update(status='failed', finished=now(), error=f'{type(exc).__name__}: {exc}')
            if runner.job.is_dir() and (runner.job/'state.json').is_file():
                context_path = runner.job/'execution_context.json'
                if context_path.is_file():
                    context = read(context_path)
                    expected_snapshot = {k: v for k, v in hashes.items()
                                         if k != 'scripts/supervise_guard_challenge.py'}
                    entry['code_snapshot_exact'] = all(context['code_sha256'].get(k) == v
                                                       for k, v in expected_snapshot.items())
                    entry['source_inputs_exact'] = read(runner.job/'source_inputs.json') == source_inputs
                    native_hashes = native_hashes or context['native_sha256']
                    entry['native_modules_exact'] = context['native_sha256'] == native_hashes
                entry['native_retry_evidence'] = retryable_native_failure(runner.job)
            save_json(output/'supervision.json', record)
            if (index < max_attempts and entry.get('native_retry_evidence')
                    and entry.get('code_snapshot_exact') and entry.get('source_inputs_exact')
                    and entry.get('native_modules_exact')):
                continue
            record.update(status='failed', error='没有满足事前固定的完整重试条件或次数已用尽', finished=now())
            save_json(output/'supervision.json', record)
            raise
        entry.update(status='solver_completed', finished=now())
        save_json(output/'supervision.json', record)
        try:
            audit = verify(job, write=True)
            archive = write_archive(job)
            verify(job, archived=True, write=False)
        except BaseException as exc:
            entry.update(status='audit_failed', audit_error=f'{type(exc).__name__}: {exc}')
            record.update(status='failed', error='完整求解后的严格复核失败；不重试以挑选有利结果', finished=now())
            save_json(output/'supervision.json', record)
            raise
        entry.update(status='sealed', physical_scenes=audit['physical_scenes'],
                     native_module_calls=audit['native_module_calls'],
                     sealed_files=len(archive['files']))
        record.update(status='completed', result_job=str(job), finished=now())
        save_json(output/'supervision.json', record)
        return record
    raise AssertionError('监督循环意外结束')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--max-attempts', type=int, default=2)
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    result = supervise(args.output, args.max_attempts)
    print(json.dumps(dict(status=result['status'], attempts=len(result['attempts']),
                          result_job=result['result_job']), ensure_ascii=False))
