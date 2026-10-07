"""Run the frozen guarded comparison with bounded native cache-open recovery."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.guard_challenge_resilient_runner import ResilientGuardChallengeRunner


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(f'COMPLETED {ResilientGuardChallengeRunner().run()}', flush=True)
