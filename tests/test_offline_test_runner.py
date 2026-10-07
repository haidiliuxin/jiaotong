"""A failed or unavailable child process must never produce a green CI run."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.run_offline_tests import run_commands


class OfflineTestRunnerTests(unittest.TestCase):
    def test_failure_is_reported_and_later_command_still_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = root / "later.txt"
            commands = [
                ("failure", [sys.executable, "-c", "raise SystemExit(7)"]),
                ("later", [sys.executable, "-c",
                           "from pathlib import Path; Path('later.txt').write_text('ran')"]),
            ]
            with contextlib.redirect_stdout(io.StringIO()) as output:
                result = run_commands(root, commands)
            self.assertNotEqual(result, 0)
            self.assertEqual(marker.read_text(), "ran")
            self.assertIn("failure", output.getvalue())

    def test_missing_executable_is_a_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            commands = [("missing", [str(Path(folder) / "absent-program")])]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertNotEqual(run_commands(Path(folder), commands), 0)

    def test_all_successful_commands_return_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_commands(Path(folder), [
                    ("success", [sys.executable, "-c", "raise SystemExit(0)"]),
                ]), 0)


if __name__ == "__main__":
    unittest.main()
