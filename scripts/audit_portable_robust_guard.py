"""Seal one completed, preregistered three-demand v5 parking task without models."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import PROJECT, input_fingerprints, save_json
from traffic_agent.portable import MANIFEST, digest, verify_bundle
from traffic_agent.runner import DEFAULT_SOURCE, now
from traffic_agent.task import Task


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def audit(root, job_id, environment, repo_card, evidence):
    root, environment, repo_card, evidence = [p.resolve() for p in
                                               (root, environment, repo_card, evidence)]
    job = (root/'智能体实验/jobs'/job_id).resolve()
    if job.parent != (root/'智能体实验/jobs').resolve() or not job.is_dir():
        raise ValueError('任务不在指定便携包的jobs目录内')
    previous = read(environment)
    manifest = read(root/MANIFEST)
    if (previous['status'] != 'passed' or Path(previous['extracted_to']).resolve() != root
            or previous['checks']['final_integrity']['manifest_sha256'] != digest(root/MANIFEST)):
        raise ValueError('环境验收记录与便携包不匹配')
    runtime_card = root/'智能体实验/acceptance_requests'/repo_card.name
    card = Task.from_dict(read(repo_card))
    state = read(job/'state.json')
    if (not runtime_card.is_file() or digest(runtime_card) != digest(repo_card)
            or state['task'] != card.as_dict()):
        raise ValueError('已提交任务卡、包内副本与真实任务参数不一致')
    if (card.scenario != 'parking' or card.search_mode != 'robust'
            or card.demand_factors != [.9, 1., 1.1]
            or card.max_new_network_oversaturated_km != .1
            or card.max_runs != 11 or card.max_feedback != 2 or not card.repeat_baseline):
        raise ValueError('任务不满足事前固定的三需求安全门槛协议')
    calls = sum(len(run['execution']['modules']) for run in state['runs'])
    if state['status'] != 'completed' or len(state['runs']) != 11 or calls != 77:
        raise ValueError('任务未完成11场景、77次原生顶层调用')
    if (job/'archive_manifest.json').exists() or evidence.exists():
        raise ValueError('任务已封存或证据目录存在，不覆盖')
    evidence.mkdir(parents=True)
    record = evidence/'acceptance.json'
    result = dict(status='auditing',started=now(),scope='同机便携新目录的既有三需求任务核验；不重跑模型',
                  root=str(root),job_id=job_id,job=str(job),
                  environment=str(environment),environment_sha256=digest(environment),
                  task_card=str(repo_card),task_sha256=digest(repo_card),
                  runtime_task_card=str(runtime_card),runtime_code_commit=manifest['code_commit'],
                  observed_model_calls=calls,new_model_calls=0,commands=[],
                  script_sha256=digest(Path(__file__)))
    save_json(record,result)
    try:
        result['initial_integrity'] = verify_bundle(root)
        source = root/DEFAULT_SOURCE.relative_to(PROJECT)
        if input_fingerprints(source) != manifest['source_inputs']:
            raise ValueError('便携包原始案例输入与清单不一致')
        result['source_inputs_unchanged'] = True
        python = root/'python/python.exe'
        for mode in ('seal', 'archive'):
            command = [str(python),'-B',str(root/'scripts/verify_experiment.py'),str(job),'--'+mode]
            with (evidence/f'{mode}.stdout.bin').open('xb') as out, \
                 (evidence/f'{mode}.stderr.bin').open('xb') as err:
                completed = subprocess.run(command,cwd=root,stdout=out,stderr=err,
                                           timeout=600,creationflags=subprocess.CREATE_NO_WINDOW)
            result['commands'].append(dict(argv=command,returncode=completed.returncode,finished=now()))
            save_json(record,result)
            if completed.returncode:
                raise ValueError(f'包内{mode}核验失败，已保留原始输出')
        v = read(job/'portable_verification.json')
        if (not v['passed'] or not v.get('portable_archive_verified')
                or v['completed_scenarios'] != 11 or v['module_executions'] != 77
                or not all(v['repeat_arrays_exact'].values())
                or not v.get('active_search_audit',{}).get('complete_demand_recommendation_verified')):
            raise ValueError('独立核验未证明完整三需求搜索与复跑')
        result['verification'] = v
        result['job_archive_sha256'] = digest(job/'archive_manifest.json')
        if input_fingerprints(source) != manifest['source_inputs']:
            raise ValueError('核验后原始案例输入发生变化')
        result['final_integrity'] = verify_bundle(root)
        result['status'] = 'passed'
    except BaseException as exc:
        result.update(status='failed',error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        result['finished'] = now()
        save_json(record,result)
        print(json.dumps(dict(record=str(record),status=result['status'],
                              observed_model_calls=calls,new_model_calls=0,
                              error=result.get('error')),ensure_ascii=False),flush=True)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('job_id')
    parser.add_argument('environment',type=Path)
    parser.add_argument('repo_card',type=Path)
    parser.add_argument('evidence',type=Path)
    args=parser.parse_args()
    audit(args.root,args.job_id,args.environment,args.repo_card,args.evidence)
