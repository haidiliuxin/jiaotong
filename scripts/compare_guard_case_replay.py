"""Read-only compare the recovered case with the failed input and probe output."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import OUTPUTS
from traffic_agent.runner import MODULES, now


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def compare(failed, probe, trial, output):
    failed, probe, trial, output = (p.resolve() for p in (failed, probe, trial, output))
    if output.exists():
        raise FileExistsError('比较报告已存在，不覆盖')
    original_state, trial_state = read(failed/'state.json'), read(trial/'state.json')
    if original_state['status'] != 'failed' or trial_state['status'] not in ('running', 'completed'):
        raise ValueError('必须是原失败任务与正在执行或完成的新任务')
    name = 'case_002'
    action_name = 'high_fixed_full_singletons_p1_1'
    original_action = next(a for a in original_state['trace'] if a['name'] == action_name)
    trial_action = next(a for a in trial_state['trace'] if a['name'] == action_name)
    if (trial_action.get('physical_run') != name or original_action['changes'] != trial_action['changes']
            or original_action['factor'] != trial_action['factor']):
        raise ValueError('重启任务的措施、需求或物理场景并非原失败目标')
    original_inputs = read(failed/'logs'/name/'inputs_before.json')
    probe_inputs = read(probe/'input_check.json')
    trial_inputs = read(trial/'logs'/name/'inputs_before.json')
    if original_inputs != trial_inputs or not probe_inputs['exact_match'] or probe_inputs['input_fingerprints'] != original_inputs:
        raise ValueError('三次场景输入摘要不一致')
    original_native, trial_native = (read(root/'logs'/name/'run.json') for root in (failed, trial))
    probe_native = read(probe/'logs/replay/run.json')
    if (original_native['status'] != 'failed' or trial_native['status'] != 'completed'
            or probe_native['status'] != 'completed'
            or any(tuple(m['module'] for m in manifest['modules']) != MODULES or
                   any(m['status'] != 'completed' or m['returncode'] != 0 for m in manifest['modules'])
                   for manifest in (trial_native, probe_native))):
        raise ValueError('原失败或两次成功的原生执行状态不符')
    arrays = {}
    for filename in OUTPUTS.values():
        probe_path = probe/'scenarios/replay/基础路网/result'/filename
        trial_path = trial/'scenarios'/name/'基础路网/result'/filename
        probe_sha, trial_sha = sha(probe_path), sha(trial_path)
        if probe_sha != trial_sha:
            raise ValueError(f'原始结果不同：{filename}')
        arrays[filename] = dict(probe_sha256=probe_sha, trial_sha256=trial_sha,
                                bytes=trial_path.stat().st_size, exact=True)
    report = dict(kind='guard_native_failure_exact_input_output_comparison_v1',
                  checked_at=now(), failed_job=failed.name, probe=probe.name,
                  trial_job=trial.name, physical_run=name, factor=trial_action['factor'],
                  changes=trial_action['changes'], input_fingerprints_exact=True,
                  probe_and_trial_completed_native_modules=len(trial_native['modules']),
                  arrays=arrays, all_raw_arrays_exact=True, new_model_calls=0,
                  limitation='只比对同输入已完成结果；首次原生失败原因未知，新挑战尚须最终严格核验')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('failed', type=Path)
    parser.add_argument('probe', type=Path)
    parser.add_argument('trial', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    report = compare(args.failed, args.probe, args.trial, args.output)
    print(json.dumps(dict(exact=report['all_raw_arrays_exact'],
                          arrays=len(report['arrays']), trial_job=report['trial_job']),
                     ensure_ascii=False))
