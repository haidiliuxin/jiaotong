"""Run the preregistered three-arm backtracking challenge, never overwrite historical jobs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.challenge_runner import ChallengeRunner


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(f'COMPLETED {ChallengeRunner().run()}', flush=True)
