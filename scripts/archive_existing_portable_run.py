"""Audit a completed portable parking or preregistered extension job without models."""
import argparse
from datetime import datetime
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import PROJECT, input_fingerprints, save_json
from traffic_agent.portable import MANIFEST, digest, verify_bundle
from traffic_agent.runner import DEFAULT_SOURCE, now


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def extension_requirements(root, state, protocol_path, name):
    """Bind publication to the frozen task; semantic proof remains the verifier's job."""
    protocol = load(protocol_path)
    if protocol.get('kind') != 'portable_extension_acceptance_v1' or name not in ('oneway', 'turn'):
        raise ValueError('不支持的扩展验收协议')
    manifest = load(root/MANIFEST)
    if (Path(protocol['runtime_root']).resolve() != root
            or protocol['runtime_manifest_sha256'] != digest(root/MANIFEST)
            or protocol['runtime_code'] != manifest['code_commit']):
        raise ValueError('扩展协议与运行包不同')
    items = [item for item in protocol['tasks'] if item['name'] == name]
    if len(items) != 1:
        raise ValueError('扩展协议任务不唯一')
    item = items[0]
    if datetime.fromisoformat(protocol['registered_at']) >= datetime.fromisoformat(state['started']):
        raise ValueError('扩展协议必须早于任务运行')
    if state['task'] != item['task']:
        raise ValueError('实际任务与事前协议不同')
    runtime_task = Path(item['runtime_task_file']).resolve()
    if not runtime_task.is_relative_to(root/'智能体实验/acceptance_requests'):
        raise ValueError('任务文件不在运行包验收目录')
    if digest(runtime_task) != item['task_sha256'] or load(runtime_task) != state['task']:
        raise ValueError('冻结任务文件发生变化')
    native_expected = 260 if name == 'oneway' else 520
    if (item['expected_scenarios'], item['expected_top_level_calls'], item['expected_nested_native_calls']) != (13, 91, native_expected):
        raise ValueError('扩展协议调用预算不符')
    task = state['task']
    if (task['scenario'] != ('oneway' if name == 'oneway' else 'circulation')
            or task['demand_factors'] != [0.9, 1.0, 1.1] or not task['repeat_baseline']
            or task['max_runs'] != 13 or task['recommendation_version'] != 'tolerance-v2'):
        raise ValueError('任务不符合完整扩展验收要求')
    key = 'hard_access_evidence' if name == 'oneway' else 'turn_access_evidence'
    runs = state['runs']
    if len(runs) != 13 or any(not r.get(key, {}).get('passed') for r in runs):
        raise ValueError('车型求解证据不完整')
    native_calls = sum(r[key].get('native_calls', 0) for r in runs)
    if native_calls != native_expected:
        raise ValueError('车型求解次数与事前协议不符')
    return dict(protocol=str(protocol_path), protocol_sha256=digest(protocol_path),
                task_sha256=item['task_sha256'], extension=name,
                observed_nested_native_calls=native_calls, expected_scenarios=13, expected_top_level_calls=91)


def audit(root, job_id, environment, evidence, protocol=None, extension=None):
    job = root/'智能体实验/jobs'/job_id
    if job.resolve().parent != (root/'智能体实验/jobs').resolve():
        raise ValueError('任务必须位于运行包jobs目录内')
    previous = load(environment)
    if previous['status'] != 'passed' or Path(previous['extracted_to']).resolve() != root:
        raise ValueError('需要该运行目录通过环境检查的记录')
    manifest = load(root/MANIFEST)
    if previous['checks']['final_integrity']['manifest_sha256'] != digest(root/MANIFEST):
        raise ValueError('运行包与环境验收清单不同')
    state = load(job/'state.json')
    if state['status'] != 'completed':
        raise ValueError('任务尚未完成，不进行封存')
    if bool(protocol) != bool(extension):
        raise ValueError('扩展验收必须同时给出协议与类型')
    requirements = extension_requirements(root, state, protocol, extension) if protocol else {}
    expected_scenes = requirements.get('expected_scenarios', 4)
    expected_calls = requirements.get('expected_top_level_calls', 28)
    if len(state['runs']) != expected_scenes:
        raise ValueError('任务尚未完成规定场景，不进行封存')
    calls = sum(len(r['execution']['modules']) for r in state['runs'])
    if calls != expected_calls or not state['task']['repeat_baseline']:
        raise ValueError('任务不符合规定调用次数及基准复跑要求')
    if (job/'archive_manifest.json').exists():
        raise ValueError('任务已封存，不覆盖原始验收；使用verify_experiment.py --archive复核')
    evidence.mkdir(parents=True, exist_ok=True)
    record = evidence/'acceptance.json'
    with record.open('x', encoding='utf-8') as out:
        json.dump({'status':'starting','started':now()},out)
    result = dict(status='auditing', started=now(), scope='同机便携目录的既有任务核验，不是异机实跑',
                  new_model_calls=0, observed_model_calls=calls, job_id=job_id, job=str(job), root=str(root),
                  environment=str(environment), environment_sha256=digest(environment),
                  script_sha256=digest(Path(__file__)), code_commit=manifest['code_commit'], commands=[], **requirements)
    try:
        result['initial_integrity'] = verify_bundle(root)
        if input_fingerprints(root/DEFAULT_SOURCE.relative_to(PROJECT)) != manifest['source_inputs']:
            raise ValueError('源案例发生变化')
        result['source_inputs_unchanged'] = True
        for mode in ('seal','archive'):
            command = [str(root/'python/python.exe'),'-B',str(root/'scripts/verify_experiment.py'),str(job),'--'+mode]
            with (evidence/f'{mode}.stdout.bin').open('xb') as out, (evidence/f'{mode}.stderr.bin').open('xb') as err:
                completed = subprocess.run(command,cwd=root,stdout=out,stderr=err,timeout=300,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
            result['commands'].append(dict(argv=command,returncode=completed.returncode,finished=now()))
            if completed.returncode:
                raise ValueError(f'{mode}核验失败，已保留现场')
        result['verification'] = load(job/'portable_verification.json')
        verification = result['verification']
        if (not verification['passed'] or not verification.get('portable_archive_verified')
                or verification['completed_scenarios'] != expected_scenes
                or verification['module_executions'] != expected_calls
                or not verification.get('repeat_arrays_exact')
                or not all(verification['repeat_arrays_exact'].values())):
            raise ValueError('独立核验结论不满足验收要求')
        result['job_archive_sha256'] = digest(job/'archive_manifest.json')
        result['final_integrity'] = verify_bundle(root)
        result['status'] = 'passed'
    except BaseException as exc:
        result.update(status='failed',error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        result['finished'] = now()
        save_json(record,result)
        print(json.dumps({'record':str(record),'status':result['status'],'new_model_calls':0},ensure_ascii=False))
    return result


if __name__ == '__main__':
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('job_id')
    parser.add_argument('environment',type=Path)
    parser.add_argument('evidence',type=Path)
    parser.add_argument('--protocol',type=Path)
    parser.add_argument('--extension',choices=('oneway','turn'))
    args = parser.parse_args()
    audit(args.root.resolve(),args.job_id,args.environment.resolve(),args.evidence.resolve(),
          args.protocol.resolve() if args.protocol else None,args.extension)
