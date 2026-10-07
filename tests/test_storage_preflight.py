import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from traffic_agent.storage import estimate, ensure_space
from traffic_agent.startup import environment
from traffic_agent.task import Task
from traffic_agent.agent import Agent


class StorageTests(unittest.TestCase):
    def test_estimate_grows_with_budget_and_accounts_for_private_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);source=root/'source';runtime=root/'runtime-source'
            source.mkdir();runtime.mkdir();(source/'input').write_bytes(b'0'*100);(runtime/'exe').write_bytes(b'0'*200)
            output=root/'output';output.mkdir()
            small=Task.from_dict(dict(max_runs=2,demand_factors=[1],repeat_baseline=False));large=Task.from_dict(dict(max_runs=4,demand_factors=[1],repeat_baseline=False))
            a=estimate([small],source,output,runtime_source=runtime);b=estimate([large],source,output,runtime_source=runtime)
            self.assertGreater(b['estimated_required_bytes'],a['estimated_required_bytes'])
            self.assertEqual(a['runtime_copy_bytes'],200)
            (output/'runtime').mkdir()
            reused=estimate([small],source,output,runtime_source=runtime)
            self.assertEqual(reused['runtime_copy_bytes'],0)
            bridge=estimate([Task.from_dict(dict(scenario='oneway'))],source,output,runtime_source=runtime)
            self.assertEqual(bridge['runtime_copy_bytes'],200)
            self.assertGreater(bridge['scenario_copy_multiplier'],a['scenario_copy_multiplier'])
            aggregate=estimate([small,large],source,output,runtime_source=runtime)
            self.assertEqual(aggregate['budget_runs'],6)

    def test_low_disk_blocks_without_creating_output_or_launching_native(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);output=root/'not-created'
            with patch('traffic_agent.storage.shutil.disk_usage',return_value=SimpleNamespace(free=1)):
                with self.assertRaisesRegex(RuntimeError,'磁盘空间不足'):
                    ensure_space([Task.from_dict({})],root,output)
            self.assertFalse(output.exists())

    def test_startup_and_scene_preparation_check_current_space(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch('traffic_agent.startup.check',return_value=dict(errors=[],preflight_passed=True)), \
                    patch('traffic_agent.storage.shutil.disk_usage',return_value=SimpleNamespace(free=1)):
                result=environment(Task.from_dict({}),root,root)
            self.assertFalse(result['passed'])
            self.assertFalse(result['output']['storage']['passed'])
            agent=Agent(Task.from_dict({}),root/'source',root/'output')
            with patch('traffic_agent.agent.ensure_space',side_effect=RuntimeError('磁盘空间不足')), \
                    patch('traffic_agent.agent.prepare') as prepare,patch('traffic_agent.agent.execute') as execute:
                with self.assertRaisesRegex(RuntimeError,'磁盘空间不足'):agent.simulation('baseline',[])
                prepare.assert_not_called();execute.assert_not_called()

    def test_dispatch_checks_aggregate_budget_before_agents_or_model_session(self):
        from traffic_agent.dispatch import Dispatcher
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);manager=Dispatcher(root,root,Mock())
            self.addCleanup(manager.close)
            ticket=dict(result=dict(status='ready',matcher='model',experiments=[dict(task=Task.from_dict({}).as_dict(),title='group')]),
                        fingerprints={},time=__import__('time').monotonic(),job=None,revision=0)
            manager.tickets['offline-ticket']=ticket
            with patch('traffic_agent.dispatch.input_fingerprints',return_value={}), \
                    patch('traffic_agent.dispatch.ensure_space',side_effect=RuntimeError('磁盘空间不足')) as check, \
                    patch('traffic_agent.dispatch.Agent') as agent,patch('traffic_agent.dispatch.client_for') as model:
                with self.assertRaisesRegex(RuntimeError,'磁盘空间不足'):manager.launch('offline-ticket')
                check.assert_called_once();agent.assert_not_called();model.assert_not_called()
