"""Re-run one failed native scenario in a new evidence directory, never in place."""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import input_fingerprints, prepare, save_json
from traffic_agent.runner import DEFAULT_SOURCE, execute, now, runtime_lock, setup_runtime


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def replay(failed_job, output, physical='case_002', action_name='high_fixed_full_singletons_p1_1'):
    failed_job, output = failed_job.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError('诊断输出已存在，不覆盖')
    state = read(failed_job/'state.json')
    if (state.get('status') != 'failed' or state.get('kind') not in (
            'prospective_network_guard_challenge',
            'prospective_network_guard_challenge_native_recovery')):
        raise ValueError('只接受本轮原生失败挑战')
    if re.fullmatch(r'case_\d{3}', physical) is None or physical not in state.get('error', ''):
        raise ValueError('指定物理场景不是本次失败场景')
    failed_name = physical
    native_log = read(failed_job/'logs'/failed_name/'run.json')
    if native_log['status'] != 'failed' or 'M6AssignVehicle.exe' not in native_log['error']:
        raise ValueError('没有预期的原生分配失败记录')
    action = next(t for t in state['trace'] if t['name'] == action_name)
    source = read(failed_job/'source_inputs.json')
    if input_fingerprints(DEFAULT_SOURCE) != source:
        raise ValueError('当前案例源输入与失败任务不同')
    output.mkdir(parents=True, exist_ok=False)
    scenario = output/'scenarios/replay/基础路网'
    scenario.parent.mkdir(parents=True)
    plan = dict(source_job=failed_job.name, failed_physical=failed_name,
                action=action, source_inputs=source, started=now(),
                purpose='仅诊断原生文件故障；不补写原失败任务、不计作已预注册挑战结果')
    save_json(output/'plan.json', plan)
    try:
        with runtime_lock(failed_job.parent.parent):
            edits = prepare(DEFAULT_SOURCE, scenario, action['changes'], action['factor'])
            expected = read(failed_job/'logs'/failed_name/'inputs_before.json')
            actual = input_fingerprints(scenario)
            if actual != expected:
                raise ValueError('重建场景与原失败场景的输入摘要不一致')
            save_json(output/'input_check.json', dict(exact_match=True, input_fingerprints=actual,
                                                      edits=edits, checked_at=now()))
            runtime = setup_runtime(failed_job.parent.parent)
            events = []
            def event(name, data):
                events.append(dict(time=now(), event=name, data=data))
                save_json(output/'events.json', events)
            rows, manifest = execute(scenario, runtime, output/'logs/replay', 900, event)
        result = dict(status='completed', row_count=len(rows), native_modules=len(manifest['modules']),
                      input_exact=True, finished=now(), manifest=manifest)
    except BaseException as exc:
        result = dict(status='failed', error=f'{type(exc).__name__}: {exc}', finished=now())
        save_json(output/'result.json', result)
        raise
    save_json(output/'result.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('failed_job', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--physical', default='case_002')
    parser.add_argument('--action', default='high_fixed_full_singletons_p1_1')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(replay(args.failed_job, args.output, args.physical, args.action),
                     ensure_ascii=False, default=str))
