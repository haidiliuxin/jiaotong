"""Independently check the teaching-case pilot preregistration and sealed job."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verify_experiment import verify as verify_job
from traffic_agent.data import PROJECT, input_fingerprints, save_json
from traffic_agent.runner import validate_source
from traffic_agent.study_runner import sha


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def verify(job, registration, write=False):
    from scripts.benchmark_teaching_transfer import SOURCE, VERSION, task

    job, registration = Path(job).resolve(), Path(registration).resolve()
    frozen = read(registration/'protocol.json')
    state = read(job/'state.json')
    source = read(job/'source_inputs.json')
    if (frozen['version'] != VERSION or frozen['source'] != str(SOURCE.resolve())
            or frozen['task'] != task().as_dict() or state['task'] != frozen['task']
            or frozen['source_inputs'] != source or input_fingerprints(SOURCE) != source
            or validate_source(SOURCE) != frozen['source_validation']):
        raise ValueError('教学案例、任务卡或源输入与预登记不一致')
    for relative, expected in frozen['code_sha256'].items():
        snapshot = (registration/'code_snapshot'/relative).resolve()
        if (not snapshot.is_relative_to(registration/'code_snapshot')
                or sha(snapshot) != expected or sha(PROJECT/relative) != expected):
            raise ValueError(f'运行代码或快照改变：{relative}')
    first = datetime.fromisoformat(state['runs'][0]['execution']['started']).timestamp()
    if (datetime.fromisoformat(frozen['registered_at']).timestamp() >= first
            or (registration/'protocol.json').stat().st_mtime >= first):
        raise ValueError('试验登记晚于首个原生模型')
    if state['status'] != 'completed':
        raise ValueError('迁移试验没有完整完成')
    archived = (job/'archive_manifest.json').is_file()
    checked = verify_job(job, archived=archived, write=False)
    outcome = read(registration/'outcome.json')
    if (outcome['status'] != 'verified' or Path(outcome['job']).resolve() != job
            or not checked['passed'] or not archived):
        raise ValueError('任务封存、结果或独立核验不完整')
    report = dict(passed=True, version=VERSION, job=str(job),
                  registration=str(registration),
                  source_links=frozen['source_validation']['directed_links'],
                  source_zones=frozen['source_validation']['zones'],
                  physical_scenes=checked['completed_scenarios'],
                  native_module_calls=checked['module_executions'],
                  repeat_exact=all(checked['repeat_arrays_exact'].values()),
                  registration_before_native=True, code_snapshot_exact=True,
                  meaning='只证明此软件教学案例上的可复核运行，不等于跨城市实测或官方得分')
    if write:
        save_json(registration/'verification.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job', type=Path)
    parser.add_argument('registration', type=Path)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(verify(args.job, args.registration, args.write),
                     ensure_ascii=False, indent=2))
