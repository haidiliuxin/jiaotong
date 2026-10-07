import copy
import io
import json
import unittest
from unittest.mock import Mock

from traffic_agent.llm_client import ChatCompletionsClient, function
from traffic_agent.llm_settings import DEFAULTS
from traffic_agent.planning_attempt import safe_usage


class DeepSeekOfficialTests(unittest.TestCase):
    def test_safe_evidence_retains_only_known_thinking_mode(self):
        self.assertEqual(safe_usage({'request_thinking_mode': 'disabled', 'reasoning_content': 'PRIVATE'}),
                         {'request_thinking_mode': 'disabled'})
        self.assertEqual(safe_usage({'request_thinking_mode': 'PRIVATE'}), {})

    def request_body(self, base, choice, model='deepseek-v4-flash'):
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps({
            'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'ok'}}]
        }).encode())
        tools = [function('decision', 'Choose a decision', {'value': {'type': 'integer'}})]
        original = copy.deepcopy(tools)
        result = ChatCompletionsClient(dict(DEFAULTS, base_url=base, model=model), 'test-key', opener).request('', [], tools, choice)
        opener.open.assert_called_once()
        self.assertEqual(tools, original)
        return json.loads(opener.open.call_args.args[0].data), result['metadata']

    def test_official_forced_and_auto_tools_use_consistent_non_thinking_mode(self):
        for base in ('https://api.deepseek.com', 'https://api.deepseek.com/v1', 'https://api.deepseek.com:443/v1/'):
            for choice in ({'type': 'function', 'name': 'decision'}, 'required', 'auto', 'none'):
                with self.subTest(base=base, choice=choice):
                    body, usage = self.request_body(base, choice)
                    self.assertEqual(body['thinking'], {'type': 'disabled'})
                    expected = {'type': 'function', 'function': {'name': 'decision'}} if isinstance(choice, dict) else choice
                    self.assertEqual(body['tool_choice'], expected)
                    self.assertTrue(body['tools'][0]['function']['strict'])
                    self.assertEqual(usage['tool_choice_profile'], 'deepseek-official-chat-v1')
                    self.assertEqual(usage['request_thinking_mode'], 'disabled')

    def test_official_override_is_scoped_to_exact_endpoint_and_model(self):
        for base, model in (
            ('https://tokenrhythm.studio/v1', 'deepseek-v4-flash'),
            ('https://api.deepseek.com.evil.example/v1', 'deepseek-v4-flash'),
            ('https://api.deepseek.com/proxy/v1', 'deepseek-v4-flash'),
            ('https://api.deepseek.com/beta', 'deepseek-v4-flash'),
            ('https://api.deepseek.com:444/v1', 'deepseek-v4-flash'),
            ('https://api.deepseek.com/v1?proxy=1', 'deepseek-v4-flash'),
            ('https://api.deepseek.com/v1', 'another-model'),
        ):
            with self.subTest(base=base, model=model):
                body, usage = self.request_body(base, 'required', model)
                self.assertNotIn('thinking', body)
                self.assertNotIn('request_thinking_mode', usage)


if __name__ == '__main__':
    unittest.main()
