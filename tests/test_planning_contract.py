"""Local transport/contract checks. Every provider response is synthetic."""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from traffic_agent.llm_client import ChatCompletionsClient, ResponsesClient, ModelError
from traffic_agent.llm_settings import DEFAULTS
from traffic_agent.natural_plan import TOOL, compose, capabilities
from traffic_agent.planning_contract import VERSION, canonical_proposal
from traffic_agent.planning_attempt import PlanningAttempt, listing, safe_usage
from test_natural_plan import model_reply, experiment, CATALOG


def wire_value():
    return json.loads(model_reply([experiment(['parking'])])['calls'][0]['arguments'])


def response(value, wire='chat_completions', **changes):
    usage=dict(total_tokens=1000,prompt_tokens=800,completion_tokens=200,
               completion_tokens_details=dict(reasoning_tokens=50),prompt_tokens_details=dict(cached_tokens=400))
    if wire=='chat_completions':
        data=dict(id='SYNTHETIC_RESPONSE',model='synthetic',choices=[dict(finish_reason='tool_calls',message=dict(
            role='assistant',reasoning_content='PRIVATE_HIDDEN_REASONING',tool_calls=[dict(
                id='synthetic_call',type='function',function=dict(name='compose_traffic_plan',arguments=json.dumps(value)))]))],usage=usage)
    else:
        data=dict(status='completed',output=[dict(type='function_call',call_id='synthetic_call',
            name='compose_traffic_plan',arguments=json.dumps(value))],
            usage=dict(input_tokens=800,output_tokens=200,output_tokens_details=dict(reasoning_tokens=50)))
    data.update(changes)
    return io.BytesIO(json.dumps(data).encode('utf-8'))


class PlanningContractTests(unittest.TestCase):
    def test_schema_encodes_nonempty_mutually_exclusive_decisions(self):
        root=TOOL['parameters']
        self.assertNotIn('anyOf',root)
        self.assertEqual(set(root['properties']),{'goal','reasoning','decision'})
        branches=root['properties']['decision']['anyOf']
        self.assertEqual([b['properties']['intent']['enum'][0] for b in branches],['execute','needs_input','unsupported'])
        for branch in branches:
            self.assertFalse(branch['additionalProperties'])
            self.assertEqual(set(branch['required']),set(branch['properties']))
            for name,schema in branch['properties'].items():
                if name!='intent':self.assertEqual(schema['minItems'],1)

    def test_both_wires_compile_regions_without_native_execution_or_extra_requests(self):
        for wire,cls in [('chat_completions',ChatCompletionsClient),('responses',ResponsesClient)]:
            with self.subTest(wire=wire),tempfile.TemporaryDirectory() as temp:
                opener=Mock();opener.open.return_value=response(wire_value(),wire)
                client=cls(dict(DEFAULTS,wire_api=wire),'UNIT_TEST_ONLY',opener)
                attempt=PlanningAttempt(client,Path(temp),'两个区域')
                catalog=dict(CATALOG,matched_roads=[])
                plan=compose('比较两个不同区域的停车治理，不比较强度，最多治理1条；需求系数：1；最多3次仿真，不复跑。',Path('source'),attempt,catalog)
                attempt.finish(plan['status'])
                self.assertEqual(plan['status'],'ready')
                self.assertEqual(plan['composition_protocol'],VERSION)
                self.assertEqual(len(plan['experiments']),1)
                task=plan['experiments'][0]['task']
                self.assertEqual(task['comparison_regions'],2)
                self.assertEqual(task['max_runs'],3)
                self.assertEqual(task['max_links'],1)
                opener.open.assert_called_once()
                body=json.loads(opener.open.call_args.args[0].data)
                self.assertFalse(body['parallel_tool_calls'])
                self.assertEqual(len(body['tools']),1)
                saved=json.loads(attempt.path.read_text('utf-8'))
                self.assertFalse(saved['native_started'])
                self.assertEqual(saved['proposal']['intent'],'execute')
                self.assertEqual(saved['proposal'],listing(Path(temp))[0]['proposal'])
                self.assertEqual(saved['usage']['tokens'],1000)
                self.assertEqual(saved['usage']['input_tokens'],800)
                self.assertEqual(saved['usage']['output_tokens'],200)
                self.assertEqual(saved['usage']['reasoning_tokens'],50)
                self.assertEqual(saved['usage']['tool_call_count'],1)
                self.assertEqual(len(saved['usage']['request_schema_sha256']),64)
                self.assertEqual(saved['usage']['request_size_bytes'],len(opener.open.call_args.args[0].data))
                for secret in ('PRIVATE_HIDDEN_REASONING','UNIT_TEST_ONLY','Authorization'):
                    self.assertNotIn(secret,attempt.path.read_text('utf-8'))

    def test_empty_legacy_and_mixed_decisions_are_not_converted_to_execution(self):
        empty=wire_value();empty['decision']['experiments']=[]
        mixed=wire_value();mixed['decision']['questions']=['假装补充']
        legacy=canonical_proposal(wire_value())
        values=[empty,mixed,legacy,dict(goal='g',reasoning='r',decision=dict(intent='needs_input',questions=[])),
                dict(goal='g',reasoning='r',decision=dict(intent='unsupported',capabilities=[]))]
        for value in values:
            with self.subTest(value=value),tempfile.TemporaryDirectory() as temp:
                opener=Mock();opener.open.return_value=response(value)
                attempt=PlanningAttempt(ChatCompletionsClient(DEFAULTS,'UNIT_TEST_ONLY',opener),Path(temp),'规划')
                with self.assertRaises(ModelError) as error:compose('停车治理，最多3次仿真，不复跑',Path('source'),attempt,CATALOG)
                attempt.finish('validation_failed',error.exception)
                self.assertEqual(attempt.record['status'],'validation_failed')
                self.assertEqual(attempt.record['usage']['finish_reason'],'tool_calls')
                self.assertFalse(attempt.record['native_started'])
                opener.open.assert_called_once()

    def test_blockers_remain_explicit_without_creating_governance_tasks(self):
        cases=[('needs_input',dict(questions=[dict(need='unresolved_location',source_quote='请研究学校门口停车干扰',question='请明确学校门口对应哪条道路。')]),'needs_input'),
               ('unsupported',dict(capabilities=['signal']),'needs_validation')]
        for intent,fields,status in cases:
            with self.subTest(intent=intent),tempfile.TemporaryDirectory() as temp:
                value=dict(goal='核对执行条件',reasoning='当前条件阻止所述研究',decision=dict(intent=intent,**fields))
                opener=Mock();opener.open.return_value=response(value)
                attempt=PlanningAttempt(ChatCompletionsClient(DEFAULTS,'UNIT_TEST_ONLY',opener),Path(temp),'规划')
                request='请优化长江村路信号配时' if intent=='unsupported' else '请研究学校门口停车干扰'
                plan=compose(request,Path('source'),attempt,CATALOG);attempt.finish(plan['status'])
                self.assertEqual(plan['status'],status)
                self.assertEqual(plan['experiments'],[])
                self.assertNotIn('task',plan)
                self.assertTrue(plan['missing'])
                self.assertEqual(listing(Path(temp))[0]['proposal']['intent'],intent)
                opener.open.assert_called_once()

    def test_response_diagnostics_do_not_treat_partial_usage_as_zero_or_keep_content(self):
        opener=Mock();opener.open.return_value=response(wire_value(),usage=dict(prompt_tokens=500))
        result=ChatCompletionsClient(DEFAULTS,'UNIT_TEST_ONLY',opener).request('',[],[TOOL])
        self.assertIsNone(result['metadata']['tokens'])
        self.assertIsNone(result['metadata']['output_tokens'])
        self.assertEqual(result['metadata']['tokens_source'],'unavailable')
        unsafe=dict(tokens=True,output_tokens=-1,reasoning_tokens='PRIVATE_REASONING',latency_s=float('inf'),
                    request_schema_sha256='x'*10000,authorization='PRIVATE_HEADER',reasoning_content='PRIVATE_REASONING')
        safe=safe_usage(unsafe)
        self.assertNotIn('tokens',safe);self.assertNotIn('reasoning_tokens',safe);self.assertNotIn('latency_s',safe)
        self.assertEqual(len(safe['request_schema_sha256']),200)
        self.assertNotIn('PRIVATE',json.dumps(safe))

    def test_request_fingerprint_is_schema_only(self):
        opener=Mock();opener.open.side_effect=[response(wire_value()),response(wire_value()),response(wire_value())]
        client=ChatCompletionsClient(DEFAULTS,'UNIT_TEST_ONLY',opener)
        first=client.request('instructions',[dict(role='user',content='first')],[TOOL])['metadata']
        second=client.request('other instructions',[dict(role='user',content='another request')],[TOOL])['metadata']
        other=copy.deepcopy(TOOL);other['description']='different tool definition'
        third=client.request('instructions',[],[other])['metadata']
        self.assertEqual(first['request_schema_sha256'],second['request_schema_sha256'])
        self.assertNotEqual(first['request_schema_sha256'],third['request_schema_sha256'])
        self.assertNotIn('instructions',json.dumps(first));self.assertNotIn('UNIT_TEST_ONLY',json.dumps(first))

    def test_prepaid_validator_rejects_stale_protocol_or_schema(self):
        from scripts.validate_observed_planning import check_capabilities
        expected=capabilities()
        api=Mock(return_value=expected)
        self.assertEqual(check_capabilities(api),expected)
        for key in ('planning_version','composition_protocol','scope_protocol','prerequisite_protocol','dynamic_initial_tools','transport_version','initial_review_protocol','max_initial_corrections','feedback_review_protocol','parking_completion_protocol','instructions_sha256','initial_schema_sha256','engine','max_model_requests'):
            stale=dict(expected,**{key:'wrong'})
            api=Mock(return_value=stale)
            with self.assertRaisesRegex(ValueError,'no paid request'):check_capabilities(api)
            api.assert_called_once_with('/api/planning-capabilities')

    def test_capabilities_endpoint_does_not_read_credentials_or_create_jobs(self):
        from traffic_agent.dispatch_web import with_dispatch
        class Base:
            def respond(self,status,body,*args):self.response=(status,body)
        with tempfile.TemporaryDirectory() as temp,patch('traffic_agent.dispatch_web.Settings') as settings:
            handler=with_dispatch(Base,Path(temp)/'source',Path(temp))()
            handler.path='/api/planning-capabilities';handler.server=Mock(server_address=('127.0.0.1',8795))
            handler.headers={'Host':'127.0.0.1:8795'};handler.do_GET()
            self.assertEqual(handler.response,(200,capabilities()))
            settings.return_value.public.assert_not_called();settings.return_value.credentials.assert_not_called()
            self.assertEqual(list(Path(temp).iterdir()),[])
            handler.headers['Origin']='https://remote.example';handler.do_GET()
            self.assertEqual(handler.response[0],403)

    def test_live_driver_stops_before_any_post_when_service_is_stale(self):
        from scripts.validate_observed_planning import main
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'new_evidence'
            def get(request,**kwargs):
                self.assertEqual(request.get_method(),'GET')
                self.assertTrue(request.full_url.endswith('/api/planning-capabilities'))
                return io.BytesIO(json.dumps(dict(planning_version='old')).encode())
            with patch('scripts.validate_observed_planning.urlopen',side_effect=get) as http, \
                 patch('traffic_agent.planning_attempt.listing',return_value=[]),patch('builtins.print'), \
                 patch('sys.argv',['validator','--live-api','--native','--output',str(output)]):
                self.assertEqual(main(),1)
            http.assert_called_once()
            record=json.loads((output/'acceptance.json').read_text('utf-8'))
            self.assertEqual(record['new_model_calls'],0)
            self.assertFalse(record['initial_preview_submitted'])
            self.assertFalse(record['native_launched'])


if __name__=='__main__':unittest.main()
