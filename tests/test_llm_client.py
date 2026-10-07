import io
import json
import os
import tempfile
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from traffic_agent.llm_client import ResponsesClient, ChatCompletionsClient, ModelError, NoRedirect
from traffic_agent.llm_settings import Settings, DEFAULTS as NEW_DEFAULTS, validate, redact
DEFAULTS = dict(NEW_DEFAULTS, base_url="https://agentrouter.org/v1", wire_api="responses")

class ClientTests(unittest.TestCase):
    def test_chat_tools_and_budget_no_retry(self):
        opener = Mock()
        data = dict(choices=[dict(finish_reason="tool_calls", message=dict(role="assistant", tool_calls=[
            dict(id="c", type="function", function=dict(name="connection_probe", arguments='{"nonce":"traffic-probe"}'))]))],
            usage={"total_tokens": 24})
        opener.open.return_value = self.response(data)
        result = ChatCompletionsClient(NEW_DEFAULTS, "test-key", opener).test()
        self.assertTrue(result["ok"])
        opener.open.assert_called_once()
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, "https://tokenrhythm.studio/v1/chat/completions")
        body = json.loads(req.data)
        self.assertFalse(body["stream"])
        self.assertIn("messages", body)
        self.assertEqual(body["tool_choice"], "required")
        self.assertEqual([t['function']['name'] for t in body['tools']], ['connection_probe'])
        self.assertEqual(body["max_tokens"], NEW_DEFAULTS["max_output_tokens"])

    def test_chat_truncated_malformed_or_duplicate_rejected(self):
        call = dict(id="dup", type="function", function=dict(name="x", arguments="{}"))
        for data in ({"output": []}, {"choices": []},
                     {"choices": [{"finish_reason": "length", "message": {"role": "assistant"}}]},
                     {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [call, call]}}]}):
            opener = Mock(); opener.open.return_value = self.response(data)
            with self.assertRaises(ModelError):
                ChatCompletionsClient(NEW_DEFAULTS, "test-key", opener).request("", [], [])
            opener.open.assert_called_once()

    def test_new_key_formats_redacted(self):
        for key in ("sk-private-example", "sk_tr_-example-123", "Bearer private-token"):
            self.assertEqual(redact(key), "[REDACTED]")

    def test_failed_tool_response_keeps_safe_usage_and_specific_truncation_reason(self):
        opener=Mock()
        opener.open.return_value=self.response(dict(choices=[dict(finish_reason='length',message=dict(role='assistant',content='PRIVATE_PARTIAL_OUTPUT'))],usage=dict(total_tokens=321)))
        with self.assertRaisesRegex(ModelError,'达到本次上限') as error:
            ChatCompletionsClient(NEW_DEFAULTS,'test-key',opener).request('',[],[])
        self.assertEqual(error.exception.usage['tokens'],321)
        self.assertNotIn('PRIVATE_PARTIAL_OUTPUT',str(error.exception))
        opener.open.assert_called_once()
        opener.open.return_value=self.response(dict(output=[],status='incomplete',usage=dict(total_tokens=123)))
        with self.assertRaises(ModelError) as error:ResponsesClient(DEFAULTS,'test-key',opener).request('',[],[])
        self.assertEqual(error.exception.usage['tokens'],123)

    def response(self, data):
        response = io.BytesIO(json.dumps(data).encode())
        return response

    def test_responses_wire_and_continuation(self):
        call = dict(type="function_call", call_id="c1", name="connection_probe", arguments='{"nonce":"traffic-probe"}')
        opener = Mock()
        opener.open.side_effect = [self.response(dict(output=[call], model="returned", usage={"total_tokens": 12})),
                                   self.response(dict(output=[dict(type="message", content=[dict(type="output_text", text="probe-ok")])]))]
        client = ResponsesClient(DEFAULTS, "test-key", opener)
        self.assertTrue(client.test()["ok"])
        req = opener.open.call_args_list[1].args[0]
        self.assertEqual(req.full_url, "https://agentrouter.org/v1/responses")
        body = json.loads(req.data)
        self.assertNotIn("messages", body)
        self.assertEqual(body["input"][-1]["call_id"], "c1")
        self.assertEqual(body["input"][-1]["type"], "function_call_output")
        self.assertFalse(body["store"])

    def test_error_body_and_redirect_never_expose_key(self):
        opener = Mock()
        opener.open.side_effect = HTTPError("https://agentrouter.org/v1/responses", 401, "secret", {}, io.BytesIO(b"sk-private-do-not-log"))
        with self.assertRaises(ModelError) as error:
            ResponsesClient(DEFAULTS, "test-key", opener).request("", [], [])
        self.assertNotIn("private", str(error.exception))
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test"))

    def test_401_explains_service_authorization_without_retry_or_body_leak(self):
        opener = Mock()
        opener.open.side_effect = HTTPError(
            NEW_DEFAULTS['base_url'] + '/chat/completions', 401, 'PRIVATE_REASON', {},
            io.BytesIO(b'{"error":{"message":"sk-private-service-body"}}'))
        with self.assertRaises(ModelError) as caught:
            ChatCompletionsClient(NEW_DEFAULTS, 'test-key', opener).test()
        message = str(caught.exception)
        self.assertIn('当前服务签发', message)
        self.assertIn('模型权限', message)
        self.assertNotIn('PRIVATE_REASON', message)
        self.assertNotIn('sk-private-service-body', message)
        self.assertEqual(caught.exception.status, 401)
        self.assertIs(caught.exception.request_sent, True)
        self.assertEqual(opener.open.call_count, 1)

    def test_403_client_restriction_is_not_reported_as_invalid_key(self):
        opener = Mock()
        opener.open.side_effect = HTTPError(
            DEFAULTS['base_url'] + '/responses', 403, 'PRIVATE_REASON', {},
            io.BytesIO(b'{"type":"unauthorized_client_error","message":"PRIVATE_BODY"}'))
        with self.assertRaises(ModelError) as caught:
            ResponsesClient(DEFAULTS, 'test-key', opener).test()
        self.assertIn('服务方开放接入', str(caught.exception))
        self.assertNotIn('PRIVATE_BODY', str(caught.exception))
        self.assertIs(caught.exception.request_sent, True)
        self.assertEqual(opener.open.call_count, 1)

    def test_protocol_mismatch_and_duplicate_calls_rejected(self):
        for data in ({"choices": []}, {"output": [], "status": "incomplete"},
                     {"output": [dict(type="function_call", name="x", arguments="{}", call_id="c")] * 2}):
            opener = Mock(); opener.open.return_value = self.response(data)
            with self.assertRaises(ModelError):
                ResponsesClient(DEFAULTS, "test-key", opener).request("", [], [])

    def test_settings_validation(self):
        for v in ({"base_url": "http://example.com"}, {"base_url": "https://secret@example.com"},
                  {"wire_api": "chat"}, {"base_url": "https://example.com/v1/responses"}, {"timeout_s": True}):
            with self.assertRaises(ValueError): validate(v)

    def test_chat_probe_invalid_arguments_preserves_paid_usage_without_raw_reply(self):
        for arguments in ('PRIVATE_INVALID_JSON', 'null', '{"nonce":"wrong"}'):
            with self.subTest(arguments=arguments):
                opener = Mock()
                opener.open.return_value = self.response(dict(choices=[dict(finish_reason='tool_calls',
                    message=dict(role='assistant', tool_calls=[dict(id='c', type='function',
                        function=dict(name='connection_probe', arguments=arguments))]))], usage=dict(total_tokens=17)))
                with self.assertRaises(ModelError) as caught:
                    ChatCompletionsClient(NEW_DEFAULTS, 'test-key', opener).test()
                self.assertIn('探针', str(caught.exception))
                self.assertNotIn(arguments, str(caught.exception))
                self.assertIs(caught.exception.request_sent, True)
                self.assertEqual(caught.exception.requests[0]['tokens'], 17)
                opener.open.assert_called_once()

    def test_responses_probe_second_failure_keeps_first_usage_and_unknown_second(self):
        call = dict(type='function_call', call_id='c', name='connection_probe', arguments='{"nonce":"traffic-probe"}')
        opener = Mock()
        opener.open.side_effect = [self.response(dict(output=[call], usage=dict(total_tokens=12))),
            HTTPError(DEFAULTS['base_url'] + '/responses', 429, 'PRIVATE_REASON', {}, io.BytesIO(b'PRIVATE_BODY'))]
        with self.assertRaises(ModelError) as caught:
            ResponsesClient(DEFAULTS, 'test-key', opener).test()
        self.assertEqual([r['tokens'] for r in caught.exception.requests], [12, None])
        self.assertEqual(caught.exception.status, 429)
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertEqual(opener.open.call_count, 2)

    def test_responses_probe_rejects_malformed_acknowledgement_without_losing_usage(self):
        call = dict(type='function_call', call_id='c', name='connection_probe', arguments='{"nonce":"traffic-probe"}')
        for content in (None, 'PRIVATE_CONTENT', [None], [{'text': {'secret': 'PRIVATE_CONTENT'}}]):
            with self.subTest(content=content):
                opener = Mock()
                opener.open.side_effect = [self.response(dict(output=[call], usage=dict(total_tokens=12))),
                    self.response(dict(output=[dict(type='message', content=content)], usage=dict(total_tokens=8)))]
                with self.assertRaises(ModelError) as caught:
                    ResponsesClient(DEFAULTS, 'test-key', opener).test()
                self.assertEqual([r['tokens'] for r in caught.exception.requests], [12, 8])
                self.assertNotIn('PRIVATE_CONTENT', str(caught.exception))
                self.assertEqual(opener.open.call_count, 2)

    def test_received_non_json_and_oversize_failures_are_marked_as_sent(self):
        for raw in (b'PRIVATE_NOT_JSON', b' ' * 2_000_001):
            opener = Mock(); opener.open.return_value = io.BytesIO(raw)
            with self.assertRaises(ModelError) as caught:
                ChatCompletionsClient(NEW_DEFAULTS, 'test-key', opener).test()
            self.assertIs(caught.exception.request_sent, True)
            self.assertEqual(len(caught.exception.requests), 1)
            self.assertIsNone(caught.exception.requests[0]['tokens'])
            self.assertNotIn('PRIVATE_NOT_JSON', str(caught.exception))
            opener.open.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "DPAPI current-user check")
    def test_dpapi_replace_keep_clear_no_echo(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(tmp)
            key = "test-credential-only-12345"
            result = settings.update(dict(config=DEFAULTS, key_action="replace", api_key=key))
            self.assertTrue(result["key_configured"])
            self.assertNotIn(key, settings.path.read_text())
            self.assertEqual(settings.credentials()[1], key)
            self.assertNotIn("secret", result)
            with self.assertRaises(ValueError):
                settings.update(dict(config=dict(DEFAULTS, base_url="https://different.example/v1")))
            settings.update(dict(key_action="clear"))
            self.assertFalse(settings.public()["key_configured"])
