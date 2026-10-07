"""Run the unchanged guarded comparison with bounded vehicle-cache recovery."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.guard_challenge_resilient_runner_v3 import VehicleCacheGuardChallengeRunner


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(f'COMPLETED {VehicleCacheGuardChallengeRunner().run()}', flush=True)
