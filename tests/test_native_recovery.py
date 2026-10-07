"""Bounded native retries preserve failures and never create policy observations."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from traffic_agent.agent import Agent
from traffic_agent.guard_challenge_resilient_runner import ResilientGuardChallengeRunner
from traffic_agent.native_recovery import native_open_fault, relocate_failure, verify_retries
from traffic_agent import guard_challenge, guard_challenge_resilient, guard_challenge_resilient_v3
from traffic_agent import native_recovery_v3
from traffic_agent.runner import MODULES


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class NativeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.job = Path(self.temp.name).resolve()/'job'
        self.name = 'baseline'
        self.scenario = self.job/'scenarios'/self.name/'基础路网'
        self.scenario.mkdir(parents=True)
        self.logs = self.job/'logs'/self.name
        self.logs.mkdir(parents=True)
        self.inputs = {'data/manageL.txt': 'frozen-sha'}
        self.started = '2026-09-23T00:00:00+00:00'
        self.finished = '2026-09-23T00:01:00+00:00'
        self.success_started = '2026-09-23T00:02:00+00:00'
        self.manifest = dict(scenario=str(self.scenario), status='failed',
                             started=self.started, finished=self.finished,
                             modules=[dict(module=m, status='completed' if i < 3 else 'failed',
                                           returncode=0 if i < 3 else 1)
                                      for i, m in enumerate(MODULES[:4])])
        write_json(self.logs/'run.json', self.manifest)
        write_json(self.logs/'inputs_before.json', self.inputs)
        self.stream = self.logs/'M6AssignVehicle.exe.stdout.bin'
        self.stream.write_bytes((
            '[error]在打开以下数据文件或执行以下程序时出错\n'
            f'[error]{self.scenario.as_posix()}/cache/Rwm_car.dat\n').encode('gb18030'))

    @patch('traffic_agent.native_recovery.input_fingerprints')
    def test_classify_relocate_and_verify_exact_retry(self, fingerprints):
        fingerprints.return_value = self.inputs
        evidence = native_open_fault(self.job, self.name)
        self.assertEqual(evidence['cache_file'], 'Rwm_car.dat')
        locations = relocate_failure(self.job, self.name, 1)
        self.scenario.mkdir(parents=True)
        self.logs.mkdir(parents=True)
        write_json(self.logs/'inputs_before.json', self.inputs)
        success = dict(scenario=str(self.scenario), status='completed',
                       started=self.success_started, finished='2026-09-23T00:03:00+00:00',
                       modules=[dict(module=m, status='completed', returncode=0) for m in MODULES])
        write_json(self.logs/'run.json', success)
        state = dict(native_retries=[dict(evidence, attempt=1, locations=locations)],
                     runs=[dict(name=self.name, execution=success)])
        result = verify_retries(self.job, state)
        self.assertEqual(result['failed_attempts'], 1)
        self.assertEqual(result['failed_native_module_calls'], 4)
        self.assertEqual(result['affected_scenes'], 1)
        self.assertFalse((self.job/'scenarios'/self.name/'基础路网'/'cache/Rwm_car.dat').exists())

        write_json(self.logs/'inputs_before.json', {'data/manageL.txt': 'changed'})
        with self.assertRaisesRegex(ValueError, '输入一致'):
            verify_retries(self.job, state)

    @patch('traffic_agent.native_recovery.input_fingerprints')
    def test_other_error_or_module_never_retryable(self, fingerprints):
        fingerprints.return_value = self.inputs
        self.assertIsNotNone(native_open_fault(self.job, self.name))
        self.stream.write_bytes(b'[error]other.dat')
        self.assertIsNone(native_open_fault(self.job, self.name))
        self.manifest['modules'][-1]['module'] = 'M6TrafficLoadLinkVeh.exe'
        write_json(self.logs/'run.json', self.manifest)
        self.assertIsNone(native_open_fault(self.job, self.name))

    def test_attempt_limit_and_path_guard(self):
        with self.assertRaisesRegex(ValueError, '上限'):
            relocate_failure(self.job, self.name, 3)
        with self.assertRaisesRegex(ValueError, '越界'):
            relocate_failure(self.job, '..', 1)

    def test_revised_protocol_keeps_task_and_controller_frozen(self):
        task = guard_challenge.challenge_task()
        old = guard_challenge.protocol(task, [])
        new = guard_challenge_resilient.protocol(task, [])
        self.assertIs(guard_challenge_resilient.candidate_actions, guard_challenge.candidate_actions)
        self.assertIs(guard_challenge_resilient.challenge_task, guard_challenge.challenge_task)
        self.assertEqual(new['version'], guard_challenge_resilient.VERSION)
        self.assertEqual(new['native_recovery']['per_scene_extra_attempts'], 2)
        self.assertEqual(new['native_recovery']['total_extra_attempts'], 20)
        new.pop('native_recovery')
        new['version'] = old['version']
        new['limitations'].pop()
        self.assertEqual(new, old)

    @patch('traffic_agent.native_recovery_v3.input_fingerprints')
    def test_v3_recovers_observed_motor_cache_fault_without_policy_change(self, fingerprints):
        fingerprints.return_value = self.inputs
        self.stream.write_bytes(self.stream.read_bytes().replace(b'Rwm_car.dat', b'Rwm_motor.dat'))
        evidence = native_recovery_v3.native_open_fault(self.job, self.name)
        self.assertEqual(evidence['cache_file'], 'Rwm_motor.dat')
        locations = native_recovery_v3.relocate_failure(self.job, self.name, 1)
        self.scenario.mkdir(parents=True)
        self.logs.mkdir(parents=True)
        write_json(self.logs/'inputs_before.json', self.inputs)
        success = dict(scenario=str(self.scenario), status='completed',
                       started=self.success_started, finished='2026-09-23T00:03:00+00:00',
                       modules=[dict(module=m, status='completed', returncode=0) for m in MODULES])
        write_json(self.logs/'run.json', success)
        state = dict(native_retries=[dict(evidence, attempt=1, locations=locations)],
                     runs=[dict(name=self.name, execution=success)])
        self.assertEqual(native_recovery_v3.verify_retries(self.job, state)['failed_native_module_calls'], 4)
        self.assertIs(guard_challenge_resilient_v3.candidate_actions, guard_challenge.candidate_actions)
        old = guard_challenge.protocol(guard_challenge.challenge_task(), [])
        new = guard_challenge_resilient_v3.protocol(guard_challenge.challenge_task(), [])
        self.assertEqual(len(new['native_recovery']['allowed_cache_files']), 10)
        new.pop('native_recovery')
        new['version'] = old['version']
        new['limitations'].pop()
        self.assertEqual(new, old)

    @patch('traffic_agent.guard_challenge_resilient_runner.relocate_failure')
    @patch('traffic_agent.guard_challenge_resilient_runner.native_open_fault')
    @patch.object(Agent, 'simulation')
    def test_runner_retry_counts_only_successful_scene(self, simulation, classifier, relocate):
        runner = ResilientGuardChallengeRunner(root=self.job)
        runner.job = self.job/'challenge'
        runner.job.mkdir()
        classifier.return_value = dict(logical_name='baseline', failed_native_modules=4)
        relocate.return_value = dict(logs='native_attempts/baseline/attempt_1/logs',
                                     scenario='native_attempts/baseline/attempt_1/scenario')
        write_json(runner.job/'native_attempts/baseline/attempt_1/logs/inputs_before.json', self.inputs)
        write_json(runner.job/'logs/baseline/inputs_before.json', self.inputs)

        def run_once(*args):
            runner.state['run_count'] += 1
            if runner.state['run_count'] == 1 and simulation.call_count == 1:
                raise RuntimeError('native failure')
            return ['real-rows'], dict(name='baseline')

        simulation.side_effect = run_once
        rows, record = runner.simulation('baseline', [], 1.0)
        self.assertEqual((rows, record), (['real-rows'], dict(name='baseline')))
        self.assertEqual(simulation.call_count, 2)
        self.assertEqual(runner.state['run_count'], 1)
        self.assertEqual(len(runner.state['native_retries']), 1)
        self.assertEqual(json.loads((runner.job/'events.jsonl').read_text(encoding='utf-8'))['event'], 'native_retry')


if __name__ == '__main__':
    unittest.main()
