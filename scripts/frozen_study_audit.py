"""Audit a trusted sealed region study with the Python code captured at run time."""
import json
import subprocess
import sys
from pathlib import Path


AUDIT = (
    "import json,sys; "
    "from pathlib import Path; "
    "sys.stdout.reconfigure(encoding='utf-8'); "
    "sys.path.insert(0,sys.argv[1]); "
    "from traffic_agent.study_audit import verify; "
    "print(json.dumps(verify(Path(sys.argv[2]),archived=True,write=False),ensure_ascii=False))"
)


def verify_frozen_study(job):
    """Keep later source updates from changing the archived study's audit rules."""
    job = Path(job).resolve()
    snapshot = job / 'code_snapshot'
    if not (snapshot / 'traffic_agent/study_audit.py').is_file():
        raise ValueError('任务缺少运行时封存的研究核验代码')
    completed = subprocess.run(
        [sys.executable, '-B', '-c', AUDIT, str(snapshot), str(job)],
        capture_output=True, text=True, encoding='utf-8', timeout=900,
    )
    if completed.returncode:
        raise RuntimeError('封存算法复核失败：' + completed.stderr.strip()[-3000:])
    result = json.loads(completed.stdout)
    if not result.get('passed'):
        raise ValueError('封存算法未通过严格复核')
    return result
