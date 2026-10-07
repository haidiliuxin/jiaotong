import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from traffic_agent.startup import environment, local_proxy_bypass
from traffic_agent.task import Task


class StartupTests(unittest.TestCase):
    def test_local_bypass_preserves_external_proxy_configuration(self):
        # Windows environment names are case-insensitive; do not overwrite our
        # NO_PROXY fixture by also assigning an empty lower-case spelling.
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://unreachable.invalid:1', 'NO_PROXY': 'company.local'}, clear=True):
            local_proxy_bypass()
            local_proxy_bypass()
            self.assertEqual(os.environ['HTTP_PROXY'], 'http://unreachable.invalid:1')
            self.assertEqual(os.environ['NO_PROXY'].split(',').count('127.0.0.1'), 1)
            self.assertIn('company.local', os.environ['NO_PROXY'])
            self.assertIn('localhost', os.environ['no_proxy'])

    def test_occupied_port_blocks_start_and_missing_source_is_actionable(self):
        with tempfile.TemporaryDirectory() as folder, socket.socket() as server:
            server.bind(('127.0.0.1', 0))
            server.listen()
            with patch('traffic_agent.startup.check', return_value={'errors': [], 'preflight_passed': True}):
                result = environment(Task.from_dict({}), Path(folder), Path(folder), server.getsockname()[1])
            self.assertFalse(result['passed'])
            self.assertTrue(result['output']['writable'])
            self.assertTrue(any('端口' in e for e in result['errors']))
            with patch('traffic_agent.startup.check', side_effect=FileNotFoundError('source missing')):
                result = environment(Task.from_dict({}), Path(folder), Path(folder))
            self.assertFalse(result['passed'])
            self.assertTrue(any('源数据' in e for e in result['errors']))
