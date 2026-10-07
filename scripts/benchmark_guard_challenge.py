"""Run the preregistered, guarded two-arm comparison on three baseline-selected regions."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.guard_challenge_runner import GuardChallengeRunner


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(f'COMPLETED {GuardChallengeRunner().run()}', flush=True)
