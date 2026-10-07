"""Offline provider wire checks; fake responses are not live API acceptance."""
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from traffic_agent.llm_client import ChatCompletionsClient,ResponsesClient,ModelError,function,TRANSPORT_VERSION
from traffic_agent.llm_settings import DEFAULTS
from traffic_agent.planning_attempt import PlanningAttempt,listing


def response(name='selected'):
    return io.BytesIO(json.dumps(dict(model='synthetic',usage=dict(total_tokens=20),choices=[dict(finish_reason='tool_calls',message=dict(
        role='assistant',reasoning_content='PRIVATE_REASONING',tool_calls=[dict(id='synthetic_call',type='function',
        function=dict(name=name,arguments='{}'))]))])).encode())


class ProviderProfileTests(unittest.TestCase):
    def setUp(self):
        self.tools=[function('selected','Required operation',dict(value=dict(type='string'))),function('unrelated','Other operation',{})]
        self.choice=dict(type='function',name='selected')

    def test_named_choice_exposes_only_requested_tool_and_sends_required_string(self):
        opener=Mock();opener.open.return_value=response()
        originals=copy.deepcopy(self.tools)
        result=ChatCompletionsClient(DEFAULTS,'UNIT_TEST_ONLY',opener).request('instructions',[],self.tools,self.choice)
        req=opener.open.call_args.args[0];body=json.loads(req.data)
        self.assertEqual(req.full_url,DEFAULTS['base_url']+'/chat/completions')
        self.assertEqual(body['model'],DEFAULTS['model'])
        self.assertEqual(body['tool_choice'],'required')
        self.assertEqual([t['function']['name'] for t in body['tools']],['selected'])
        self.assertTrue(body['tools'][0]['function']['strict'])
        self.assertEqual(body['tools'][0]['function']['parameters'],self.tools[0]['parameters'])
        self.assertEqual(self.tools,originals)
        usage=result['metadata']
        self.assertEqual(usage['requested_tool_choice'],'function:selected')
        self.assertEqual(usage['request_tool_choice'],'required')
        self.assertEqual(usage['request_tool_count'],1)
        self.assertEqual(usage['tool_choice_profile'],'tokenrhythm-deepseek-chat-v1')
        self.assertEqual(usage['transport_version'],TRANSPORT_VERSION)
        self.assertEqual(usage['request_schema_sha256'],hashlib.sha256(json.dumps(body['tools'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest())
        self.assertNotIn('PRIVATE',json.dumps(usage));self.assertNotIn('UNIT_TEST_ONLY',json.dumps(usage))
        opener.open.assert_called_once()

    def test_other_providers_or_models_keep_standard_named_wire_choice(self):
        for fields in [dict(base_url='https://example.test/v1'),dict(model='another-model'),
                       dict(base_url='https://tokenrhythm.studio.example.test/v1'),
                       dict(base_url='https://tokenrhythm.studio/other'),dict(base_url='https://tokenrhythm.studio:8443/v1')]:
            with self.subTest(fields=fields):
                opener=Mock();opener.open.return_value=response()
                result=ChatCompletionsClient(dict(DEFAULTS,**fields),'UNIT_TEST_ONLY',opener).request('',[],self.tools,self.choice)
                body=json.loads(opener.open.call_args.args[0].data)
                self.assertEqual(body['tool_choice'],dict(type='function',function=dict(name='selected')))
                self.assertEqual(len(body['tools']),2)
                self.assertEqual(result['metadata']['tool_choice_profile'],'standard-chat')

    def test_responses_wire_is_not_rewritten_or_switched(self):
        opener=Mock();opener.open.return_value=io.BytesIO(b'{"status":"completed","output":[]}')
        ResponsesClient(dict(DEFAULTS,wire_api='responses'),'UNIT_TEST_ONLY',opener).request('',[],self.tools,self.choice)
        req=opener.open.call_args.args[0];body=json.loads(req.data)
        self.assertEqual(req.full_url,DEFAULTS['base_url']+'/responses')
        self.assertEqual(body['tool_choice'],self.choice)
        self.assertEqual(body['tools'],self.tools)

    def test_invalid_named_tools_fail_before_network_and_cost_nothing(self):
        cases=[([],self.choice),(self.tools*2,self.choice),(self.tools,dict(type='function',name='missing')),
               (self.tools,dict(type='not-a-function',name='selected')),(self.tools,'invalid'),([], 'required')]
        for tools,choice in cases:
            with self.subTest(choice=choice),tempfile.TemporaryDirectory() as temp:
                opener=Mock();attempt=PlanningAttempt(ChatCompletionsClient(DEFAULTS,'UNIT_TEST_ONLY',opener),Path(temp),'local failure')
                with self.assertRaises(ModelError) as error:attempt.request('',[],tools,choice)
                self.assertFalse(error.exception.request_sent)
                opener.open.assert_not_called()
                record=listing(Path(temp))[0]
                self.assertFalse(record['requested']);self.assertIsNone(record['usage'])

    def test_string_modes_are_not_converted_into_forced_calls(self):
        for choice in ['auto','none','required']:
            with self.subTest(choice=choice):
                opener=Mock();opener.open.return_value=response()
                ChatCompletionsClient(DEFAULTS,'UNIT_TEST_ONLY',opener).request('',[],self.tools,choice)
                body=json.loads(opener.open.call_args.args[0].data)
                self.assertEqual(body['tool_choice'],choice)
                self.assertEqual(len(body['tools']),2)

    def test_planning_and_observation_calls_share_the_same_wire_profile(self):
        opener=Mock();names=['compose_traffic_plan','choose_next_experiment','connection_probe']
        opener.open.side_effect=[response(name) for name in names]
        with tempfile.TemporaryDirectory() as temp:
            client=ChatCompletionsClient(DEFAULTS,'UNIT_TEST_ONLY',opener)
            for name in names:
                attempt=PlanningAttempt(client,Path(temp),'synthetic '+name)
                result=attempt.request('',[],[function(name,'Single allowed decision',{})],dict(type='function',name=name))
                attempt.finish('ready')
                self.assertEqual(result['calls'][0]['name'],name)
            for call,name in zip(opener.open.call_args_list,names):
                body=json.loads(call.args[0].data)
                self.assertEqual(body['tool_choice'],'required')
                self.assertEqual([t['function']['name'] for t in body['tools']],[name])
            for saved in listing(Path(temp)):
                self.assertEqual(saved['usage']['tool_choice_profile'],'tokenrhythm-deepseek-chat-v1')
                self.assertEqual(saved['usage']['request_tool_choice'],'required')
                self.assertNotIn('PRIVATE',json.dumps(saved));self.assertNotIn('UNIT_TEST_ONLY',json.dumps(saved))
        self.assertEqual(opener.open.call_count,3)


if __name__=='__main__':unittest.main()
