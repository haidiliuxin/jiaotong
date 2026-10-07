"""Native cleanup fault injection uses temporary config and no real processes."""
import contextlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from traffic_agent import runner


class FakeProcess:
    pid = 43210

    def __init__(self, timeout=True, cleanup_timeout=False, poll_error=False):
        self.timeout = timeout
        self.cleanup_timeout = cleanup_timeout
        self.poll_error = poll_error
        self.returncode = None
        self.waits = 0

    def wait(self, timeout):
        self.waits += 1
        if (self.waits == 1 and self.timeout) or (self.waits > 1 and self.cleanup_timeout):
            raise subprocess.TimeoutExpired("native-module", timeout)
        self.returncode = 0
        return 0

    def poll(self):
        if self.poll_error:
            raise OSError("process status unavailable")
        return self.returncode


class RunnerCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.config = self.runtime / "files/runinfo_vc6.txt"
        self.config.parent.mkdir(parents=True)
        self.original = b"original config\r\n"
        self.config.write_bytes(self.original)
        programs = self.runtime / "programs"
        programs.mkdir()
        (programs / "M6AssignVehicle.exe").write_bytes(b"MZsynthetic-fixture")
        self.scenario = self.root / "scenario"
        for relative in ("result/ODCar.tsd", "cache/CapLinkVehMan.dat", "cache/Assign_car.od"):
            path = self.scenario / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"1\n")
        self.logs = self.root / "logs"
        self.marker = self.runtime / "agent_runtime_cleanup_required.json"
        self.process = FakeProcess()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(runner, "MODULES", ("M6AssignVehicle.exe",)))
        stack.enter_context(patch.object(runner, "OUTPUTS", {}))
        stack.enter_context(patch.object(runner, "input_fingerprints", return_value={"data/input": "sha"}))
        stack.enter_context(patch.object(runner, "results", return_value=[{"flow": 1}]))
        self.popen = stack.enter_context(patch.object(runner.subprocess, "Popen", side_effect=lambda *a, **k: self.process))
        self.stop = stack.enter_context(patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess("taskkill", 0)))

    def execute(self):
        return runner.execute(self.scenario, self.runtime, self.logs, 1, lambda *args: None)

    def manifest(self):
        return json.loads((self.logs / "run.json").read_text(encoding="utf-8"))

    def assert_original_timeout(self):
        caught = self.assertRaisesRegex(RuntimeError, "M6AssignVehicle.exe.*1")
        with caught:
            self.execute()
        self.assertIsInstance(caught.exception.__cause__, subprocess.TimeoutExpired)
        return caught.exception

    def test_taskkill_timeout_preserves_module_timeout_and_finalizes_evidence(self):
        self.process.cleanup_timeout = True
        self.stop.side_effect = subprocess.TimeoutExpired("taskkill", 30)
        self.assert_original_timeout()
        manifest = self.manifest()
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["modules"][0]["status"], "timeout")
        self.assertIn("finished", manifest)
        self.assertEqual({e["stage"] for e in manifest["cleanup"]["errors"]}, {"stop", "wait"})
        self.assertFalse(manifest["cleanup"]["config_restored"])
        self.assertFalse(manifest["cleanup"]["process_resolved"])
        marker = json.loads(self.marker.read_text(encoding="utf-8"))
        self.assertEqual(marker["pid"], 43210)
        self.assertEqual(Path(marker["config_backup"]).read_bytes(), self.original)
        self.assertNotEqual(self.config.read_bytes(), self.original)
        self.assertEqual(self.process.waits, 2)

    def test_parent_exit_does_not_prove_tree_stopped_after_taskkill_timeout(self):
        self.stop.side_effect = subprocess.TimeoutExpired("taskkill", 30)
        self.assert_original_timeout()
        self.assertFalse(self.manifest()["cleanup"]["process_resolved"])
        self.assertNotEqual(self.config.read_bytes(), self.original)
        self.assertTrue(self.marker.exists())

    def test_parent_exit_before_cleanup_does_not_clear_timed_out_tree(self):
        self.process.poll = lambda: 0
        self.stop.return_value = subprocess.CompletedProcess("taskkill", 128)
        self.assert_original_timeout()
        self.assertFalse(self.manifest()["cleanup"]["process_resolved"])
        self.assertNotEqual(self.config.read_bytes(), self.original)
        self.assertTrue(self.marker.exists())
        self.stop.assert_not_called()

    def test_wait_timeout_after_successful_taskkill_blocks_restoration(self):
        self.process.cleanup_timeout = True
        self.assert_original_timeout()
        self.assertEqual(self.manifest()["cleanup"]["errors"][0]["stage"], "wait")
        self.assertTrue(self.marker.exists())
        self.assertNotEqual(self.config.read_bytes(), self.original)

    def test_nonzero_taskkill_is_recorded_and_wait_still_attempted(self):
        self.stop.return_value = subprocess.CompletedProcess("taskkill", 5)
        self.assert_original_timeout()
        self.assertEqual(self.process.waits, 2)
        self.assertIn("5", self.manifest()["cleanup"]["errors"][0]["error"])
        self.assertTrue(self.marker.exists())

    def test_confirmed_stop_restores_exact_config_and_removes_marker(self):
        self.assert_original_timeout()
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse(self.marker.exists())
        cleanup = self.manifest()["cleanup"]
        self.assertTrue(cleanup["process_resolved"])
        self.assertTrue(cleanup["config_restored"])
        self.assertEqual(cleanup["errors"], [])

    def deny_restore(self):
        real_write = Path.write_bytes

        def write(path, content):
            if path == self.config and content == self.original:
                raise PermissionError("restore denied")
            return real_write(path, content)

        return patch.object(Path, "write_bytes", write)

    def test_restore_failure_preserves_primary_error_and_persists_failure(self):
        with self.deny_restore():
            error = self.assert_original_timeout()
        self.assertEqual(self.manifest()["cleanup"]["errors"][0]["stage"], "restore_config")
        self.assertIn("finished", self.manifest())
        self.assertTrue(self.marker.exists())
        self.assertTrue(any("restore denied" in note for note in error.__notes__))

    def test_final_manifest_failure_preserves_primary_error_and_recovery_marker(self):
        real_save = runner.save_json

        def save(path, value):
            if path == self.logs / "run.json" and "finished" in value:
                raise PermissionError("manifest denied")
            real_save(path, value)

        with patch.object(runner, "save_json", side_effect=save):
            error = self.assert_original_timeout()
        marker = json.loads(self.marker.read_text(encoding="utf-8"))
        self.assertTrue(any(e["stage"] == "final_manifest" for e in marker["cleanup"]["errors"]))
        self.assertTrue(any("manifest denied" in note for note in error.__notes__))
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_marker_failure_before_launch_leaves_config_untouched(self):
        real_save = runner.save_json

        def save(path, value):
            if path == self.marker:
                raise PermissionError("marker denied")
            real_save(path, value)

        with patch.object(runner, "save_json", side_effect=save):
            with self.assertRaisesRegex(PermissionError, "marker denied"):
                self.execute()
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual(self.process.waits, 0)
        self.popen.assert_not_called()

    def test_cleanup_marker_blocks_setup_lock_and_direct_execution(self):
        self.marker.write_text('{"config_backup":"backup.bin"}', encoding="utf-8")
        for action in (lambda: runner.setup_runtime(self.root), self.acquire_lock, self.execute):
            with self.subTest(action=action):
                with self.assertRaisesRegex(RuntimeError, "agent_runtime_cleanup_required.json"):
                    action()
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse(self.logs.exists())

    def acquire_lock(self):
        with runner.runtime_lock(self.root):
            self.fail("blocked runtime lock yielded")

    def test_global_lock_blocks_unresolved_private_job_runtime(self):
        marker = self.root / "jobs/previous-bridge/runtime/agent_runtime_cleanup_required.json"
        marker.parent.mkdir(parents=True)
        marker.write_text('{"pid":43210,"config_backup":"backup.bin"}', encoding="utf-8")
        self.assertFalse(self.marker.exists())
        with self.assertRaisesRegex(RuntimeError, "agent_runtime_cleanup_required.json") as caught:
            self.acquire_lock()
        self.assertIn(str(marker), str(caught.exception))
        marker.unlink()
        # Rejection releases the OS lock, and manual resolution permits later work.
        with runner.runtime_lock(self.root):
            pass

    def test_global_lock_does_not_scan_nested_scenario_runtime_markers(self):
        marker = self.root / "jobs/old-job/scenarios/archive/runtime/agent_runtime_cleanup_required.json"
        marker.parent.mkdir(parents=True)
        marker.write_text("{}", encoding="utf-8")
        with runner.runtime_lock(self.root):
            pass

    def test_successful_run_restores_config_and_returns_completed_manifest(self):
        self.process.timeout = False
        rows, manifest = self.execute()
        self.assertEqual(rows, [{"flow": 1}])
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(self.manifest(), manifest)
        self.assertTrue(manifest["cleanup"]["config_restored"])
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_failed_second_launch_does_not_kill_completed_previous_pid(self):
        self.process.timeout = False
        self.popen.side_effect = [self.process, OSError("launch denied")]
        with patch.object(runner, "MODULES", ("M6AssignVehicle.exe", "M6NodeQueueDelay.exe")):
            with self.assertRaisesRegex(OSError, "launch denied"):
                self.execute()
        self.stop.assert_not_called()
        self.assertTrue(self.manifest()["cleanup"]["process_resolved"])
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse(self.marker.exists())

    def test_restore_failure_after_success_is_a_failed_run(self):
        self.process.timeout = False
        with self.deny_restore():
            with self.assertRaisesRegex(RuntimeError, "restore denied"):
                self.execute()
        self.assertEqual(self.manifest()["status"], "failed")
        self.assertTrue(self.marker.exists())

    def test_poll_failure_does_not_skip_stop_wait_or_final_manifest(self):
        self.process.poll_error = True
        self.assert_original_timeout()
        manifest = self.manifest()
        self.assertEqual(manifest["cleanup"]["errors"][0]["stage"], "poll")
        self.assertEqual(self.process.waits, 2)
        self.assertTrue(manifest["cleanup"]["config_restored"])
        self.assertIn("finished", manifest)


if __name__ == "__main__":
    unittest.main()
