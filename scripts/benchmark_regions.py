"""Run the preregistered three-region study, never overwrite historical jobs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.study_runner import StudyRunner


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(f'COMPLETED {StudyRunner().run()}', flush=True)
