"""Read-only verify a sealed historical study with its own captured algorithm.

The live source can evolve without silently applying new semantics to old jobs.
Only import Python modules from the job's hashed code_snapshot; the frozen
study audit still checks every captured code hash and original TranStar output.
"""
import argparse
import json
import sys
from pathlib import Path


def verify_frozen(job):
    job = job.resolve()
    snapshot = (job/'code_snapshot').resolve()
    if not snapshot.is_relative_to(job) or not (snapshot/'traffic_agent/study_audit.py').is_file():
        raise ValueError('任务缺少内部算法快照')
    if 'traffic_agent' in sys.modules:
        raise RuntimeError('当前解释器已载入非快照算法，禁止混用')
    sys.path.insert(0, str(snapshot))
    from traffic_agent import study_audit
    if not Path(study_audit.__file__).resolve().is_relative_to(snapshot):
        raise RuntimeError('未从任务快照加载核验器')
    result = study_audit.verify(job, archived=True, write=False)
    result['auditor_source'] = str(Path(study_audit.__file__).resolve())
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job', type=Path)
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    result = verify_frozen(args.job)
    print(json.dumps({k: v for k, v in result.items() if k != 'runs'},
                     ensure_ascii=False, indent=2))
