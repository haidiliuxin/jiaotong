"""Bounded case gates, before any chargeable call or native launch."""
import copy
import io
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock,patch

from scripts.validate_observed_planning import PROGRAMME_REQUEST,CASES,validate_preview,verify_case_states,validate_preflight
from traffic_agent.natural_plan import compose,limits,preflight,capabilities
from traffic_agent.planning_budget import starting_with
from test_natural_plan import experiment,model_reply


class ProgrammeAcceptanceTests(unittest.TestCase):
    def preview(self,reverse=False):
        studies=[experiment(['parking'],road='七东线'),experiment(['parking','oneway'],road='长江村路')]
        if reverse:studies.reverse()
        context=dict(matched_roads=['七东线','长江村路'],road_names=['七东线','长江村路'],targets={})
        client=Mock(request=Mock(return_value=model_reply(studies)))
        return compose(PROGRAMME_REQUEST,Path('.'),client,context)

    def test_each_order_fits_native_and_model_budget_without_extra_comparisons(self):
        for reverse in (False,True):
            with self.subTest(reverse=reverse):
                preview=self.preview(reverse)
                self.assertEqual(preview['status'],'ready')
                self.assertEqual(preview['decision_permissions']['clarification_needs'],[])
                validate_preview(preview,'programme')
                tasks=[e['task'] for e in preview['experiments']]
                self.assertEqual(sum(t['max_runs'] for t in tasks),6)
                self.assertEqual(starting_with(tasks)+1,5)
                self.assertEqual(preview['model_budget']['minimum_required'],5)
                self.assertEqual(limits(PROGRAMME_REQUEST)['demand_factors'],[1.0])

    def test_omitted_study_changed_road_extra_repetition_or_demand_is_rejected(self):
        original=self.preview()
        for changed in ('omitted','road','repeat','demand','budget'):
            value=copy.deepcopy(original)
            if changed=='omitted':value['experiments'].pop()
            elif changed=='road':value['experiments'][1]['task']['road_name']='其他路'
            elif changed=='repeat':value['experiments'][0]['task']['repeat_baseline']=True
            elif changed=='demand':value['experiments'][0]['task']['demand_factors']=[1,1.1]
            else:value['experiments'][0]['task']['max_runs']=3
            with self.subTest(changed=changed),self.assertRaises(ValueError):validate_preview(value,'programme')

    def test_incomplete_task_cannot_be_a_successful_programme(self):
        with self.assertRaisesRegex(ValueError,'incomplete'):
            verify_case_states({'a':dict(status='incomplete'), 'b':dict(status='queued')},'programme')
        with self.assertRaisesRegex(ValueError,'Study set'):
            verify_case_states({'a':dict(status='completed')},'programme')

    def offline_preflight(self,case='programme'):
        roads=['七东线','长江村路'] if case=='programme' else []
        with patch('traffic_agent.natural_plan.context_for',return_value=dict(matched_roads=roads,road_names=set(roads),targets={})), \
                patch('traffic_agent.data.input_fingerprints',return_value={'road.dat':'a'*64}):
            return preflight(CASES[case]['request'],Path('source'))

    def test_local_preflight_and_paid_planner_use_identical_permissions(self):
        for case in CASES:
            checked=self.offline_preflight(case)
            validate_preflight(checked,case)
            self.assertNotIn('experiments',checked)
            self.assertNotIn('ticket',checked)
            self.assertFalse(checked['model_requested'])
        self.assertEqual(self.offline_preflight()['decision_permissions'],self.preview()['decision_permissions'])

    def test_bad_preflight_stops_live_driver_before_preview_or_native_launch(self):
        from scripts.validate_observed_planning import main
        checked=self.offline_preflight()
        # Preserve the real v1 fault as an offline fault injection.
        checked['decision_permissions'].update(clarification_needs=['unresolved_location'],
                                              clarification_quotes={'unresolved_location':['两条道路分成独立研究组']},
                                              intents=['execute','needs_input','unsupported'])
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'new_evidence'
            def http(request,**kwargs):
                if request.full_url.endswith('/api/planning-capabilities'):
                    self.assertEqual(request.get_method(),'GET');result=capabilities()
                else:
                    self.assertTrue(request.full_url.endswith('/api/dispatch/preflight'))
                    self.assertEqual(json.loads(request.data),{'request':PROGRAMME_REQUEST});result=checked
                return io.BytesIO(json.dumps(result).encode())
            with patch('scripts.validate_observed_planning.urlopen',side_effect=http) as network, \
                    patch('traffic_agent.planning_attempt.listing',return_value=[]),patch('builtins.print'), \
                    patch('sys.argv',['validator','--live-api','--native','--case','programme','--output',str(output)]):
                self.assertEqual(main(),1)
            self.assertEqual(network.call_count,2)  # Two local reads; zero model requests.
            record=json.loads((output/'acceptance.json').read_text('utf-8'))
            self.assertEqual(record['new_model_calls'],0)
            self.assertFalse(record['initial_preview_submitted'])
            self.assertFalse(record['native_launched'])
            self.assertEqual(record['input_preflight'],checked)

    def test_preflight_endpoint_needs_no_key_or_job_and_rejects_remote_origin(self):
        from traffic_agent.dispatch_web import with_dispatch
        class Base:
            def respond(self,status,body,*args):self.response=(status,body)
        with tempfile.TemporaryDirectory() as tmp,patch('traffic_agent.dispatch_web.Settings') as settings, \
                patch('traffic_agent.natural_plan.context_for',return_value=dict(matched_roads=['七东线','长江村路'],targets={})), \
                patch('traffic_agent.data.input_fingerprints',return_value={'road.dat':'b'*64}):
            handler=with_dispatch(Base,Path(tmp)/'source',Path(tmp))()
            handler.path='/api/dispatch/preflight';handler.command='POST'
            handler.server=Mock(server_address=('127.0.0.1',8795))
            body=json.dumps({'request':PROGRAMME_REQUEST}).encode()
            handler.rfile=io.BytesIO(body);handler.headers=Message()
            handler.headers['Host']='127.0.0.1:8795';handler.headers['Content-Type']='application/json'
            handler.headers['Content-Length']=str(len(body));handler.do_POST()
            self.assertEqual(handler.response[0],200)
            validate_preflight(handler.response[1],'programme')
            settings.return_value.credentials.assert_not_called();settings.return_value.public.assert_not_called()
            self.assertEqual(handler.dispatcher.tickets,{})
            self.assertEqual(list(Path(tmp).iterdir()),[])
            handler.headers['Origin']='https://remote.example';handler.do_POST()
            self.assertEqual(handler.response[0],403)

    def test_preflight_detects_source_change_without_requesting_model(self):
        with patch('traffic_agent.natural_plan.context_for',return_value=dict(matched_roads=[],targets={})), \
                patch('traffic_agent.data.input_fingerprints',side_effect=[{'road':'a'},{'road':'b'}]):
            with self.assertRaisesRegex(ValueError,'源场景已变化'):
                preflight(CASES['regions']['request'],Path('source'))


if __name__=='__main__':unittest.main()
