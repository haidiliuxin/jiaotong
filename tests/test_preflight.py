import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from traffic_agent.preflight import check
from traffic_agent.runner import MODULES
from traffic_agent.task import Task


class PreflightTests(unittest.TestCase):
    def test_pointer_is_not_a_ready_model_and_check_creates_no_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'programs').mkdir()
            for name in MODULES:
                (root/'programs'/name).write_bytes(b'MZtest fixture')
            (root/'programs'/MODULES[0]).write_bytes(b'version https://git-lfs.github.com/spec/v1\n')
            with patch('traffic_agent.preflight.validate_source', return_value={}), patch('traffic_agent.preflight.subprocess.run') as launch:
                result = check(Task.from_dict({}), root/'source', root)
            self.assertFalse(result['preflight_passed'])
            self.assertEqual(result['programs'][MODULES[0]], 'lfs_pointer')
            self.assertFalse(result['compiler']['required'])
            launch.assert_not_called()
            self.assertFalse((root/'runtime').exists())

    def test_missing_compiler_is_specific_to_native_bridge_tasks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            task = Task.from_dict(dict(scenario='circulation', turn=[1, 2, 3]))
            with patch('traffic_agent.preflight.validate_source', return_value={}), patch('traffic_agent.preflight.shutil.which', return_value=None), patch('traffic_agent.preflight.subprocess.run') as launch:
                result = check(task, root/'source', root)
            self.assertTrue(result['compiler']['required'])
            self.assertTrue(any('MinGW gcc' in e for e in result['errors']))
            self.assertIn('NodeAssignModel_motor.exe', result['programs'])
            launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
