"""Preregister and run one cross-dataset parking-agent pilot on teaching case 4."""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.agent import Agent
from traffic_agent.archive import write_archive
from traffic_agent.data import PROJECT, input_fingerprints, save_json
from traffic_agent.runner import EXPERIMENTS, now, validate_source
from traffic_agent.task import Task
from traffic_agent.study_runner import sha
from scripts.verify_experiment import verify

VERSION = 'teaching-case4-cross-dataset-pilot-v1'
SOURCE = PROJECT/'TranStar城市交通版V4.8 - 试用版/examples/教学案例4/基础路网'


def task():
    return Task.from_dict(dict(
        request='在独立教学案例4上用新基准诊断停车干扰，闭环调整并核对三档小汽车需求',
        scenario='parking', search_mode='robust',
        backtrack_on_singleton_failure=True,
        recommendation_version='tolerance-v2',
        max_links=3, max_runs=11, max_feedback=3,
        demand_factors=[0.9, 1.0, 1.1], repeat_baseline=True,
        max_new_network_oversaturated_km=0.1))


def protocol():
    source_validation = validate_source(SOURCE)
    code = sorted((PROJECT/'traffic_agent').glob('*.py')) + [PROJECT/'traffic_agent/knowledge.json',
        PROJECT/'scripts/benchmark_teaching_transfer.py',
        PROJECT/'scripts/verify_experiment.py',
        PROJECT/'scripts/verify_teaching_transfer.py']
    hashes = {path.relative_to(PROJECT).as_posix(): sha(path) for path in code}
    return dict(version=VERSION, registered_at=now(), source=str(SOURCE.resolve()),
                source_validation=source_validation,
                source_inputs=input_fingerprints(SOURCE),
                task=task().as_dict(), code_sha256=hashes,
                git_commit=subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=PROJECT,
                                          capture_output=True, text=True, check=True).stdout.strip(),
                dataset_choice='使用软件自带的教学案例4；提供的初始输出曾显示一处可诊断候选，但实际选区只依据本次新原生基准',
                fixed_outcome_rule='若无候选、原生失败、硬约束失败或审计不通过，保留原状；不换数据集、不把不完整结果写成得分提升',
                boundary='另一套软件教学数据上的流程迁移试验，不是另一城市实测、异机复现或官方得分')


def run():
    frozen = protocol()
    agent = Agent(task(), source=SOURCE, root=EXPERIMENTS)
    registration = EXPERIMENTS/'transfer_protocols'/agent.job_id
    registration.mkdir(parents=True, exist_ok=False)
    save_json(registration/'protocol.json', frozen)
    for relative in frozen['code_sha256']:
        destination = registration/'code_snapshot'/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT/relative, destination)
    print(f'PREREGISTERED {registration}', flush=True)
    print(f'STUDY {agent.job}', flush=True)
    outcome = dict(job=str(agent.job), registration=str(registration), status='running',
                   new_model_calls=None)
    try:
        agent.run()
        state = json.loads((agent.job/'state.json').read_text(encoding='utf-8'))
        outcome.update(status=state['status'], completed_scenarios=len(state['runs']),
                       new_model_calls=sum(len(r['execution']['modules']) for r in state['runs']))
        if any(sha(PROJECT/name) != digest for name, digest in frozen['code_sha256'].items()):
            raise ValueError('运行期间算法或核验代码改变，不出具迁移完成结论')
        if input_fingerprints(SOURCE) != frozen['source_inputs']:
            raise ValueError('教学案例原输入在试验期间改变')
        if state['status'] == 'completed':
            checked = verify(agent.job, write=True)
            sealed = write_archive(agent.job)
            portable = verify(agent.job, archived=True, write=False)
            if not checked['passed'] or not portable['passed']:
                raise ValueError('跨数据集任务审计未通过')
            outcome.update(status='verified', verification=checked,
                           sealed_files=len(sealed['files']))
        print(f"TRANSFER_RESULT {outcome['status']} {agent.job}", flush=True)
    except BaseException as exc:
        outcome.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        if (agent.job/'state.json').is_file():
            saved = json.loads((agent.job/'state.json').read_text(encoding='utf-8'))
            outcome['completed_scenarios'] = len(saved.get('runs', []))
            outcome['native_module_calls_logged'] = sum(
                len(json.loads(path.read_text(encoding='utf-8')).get('modules', []))
                for path in (agent.job/'logs').glob('*/run.json'))
        raise
    finally:
        outcome['finished_at'] = now()
        save_json(registration/'outcome.json', outcome)
    return outcome


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='只验证任务卡、数据和代码，不启动仿真')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if args.check:
        record = protocol()
        print(json.dumps(dict(version=record['version'], source=record['source'],
                              validation=record['source_validation'],
                              source_files=len(record['source_inputs']),
                              code_files=len(record['code_sha256']), task=record['task']),
                         ensure_ascii=False, indent=2))
    else:
        run()
