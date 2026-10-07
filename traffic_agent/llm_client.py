"""Stateless Responses transport; continuation items remain memory-only."""
import json
import hashlib
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from .llm_settings import redact

TRANSPORT_VERSION='llm-transport-v2'


def chat_tool_choice(config, tools, choice):
    """Respect the selected provider's documented wire format, before I/O.

    Token Rhythm documents string-only tool_choice for DeepSeek. A named
    selection becomes required with exactly that tool exposed, preserving the
    caller's restriction. No retry, model change or protocol fallback occurs.
    https://tokenrhythm.studio/docs/api-integration
    """
    url=urlsplit(config['base_url'])
    profile=('tokenrhythm-deepseek-chat-v1' if url.scheme=='https' and url.hostname=='tokenrhythm.studio'
             and url.port in (None,443) and url.path.rstrip('/')=='/v1'
             and not url.username and not url.password and not url.query and not url.fragment
             and config['model'].lower().startswith('deepseek-') else 'standard-chat')
    if (url.scheme=='https' and url.hostname=='api.deepseek.com' and url.port in (None,443)
            and url.path.rstrip('/') in ('','/v1') and not url.username and not url.password
            and not url.query and not url.fragment and config['model'].lower().startswith('deepseek-')):
        profile='deepseek-official-chat-v1'
    if not isinstance(tools,list) or any(not isinstance(tool,dict) for tool in tools):
        raise ModelError('当前允许工具的本地配置无效；未请求模型',request_sent=False)
    if isinstance(choice,dict):
        if set(choice)!={'type','name'} or choice['type']!='function' or not isinstance(choice['name'],str):
            raise ModelError('指定工具的本地配置无效；未请求模型',request_sent=False)
        selected=[tool for tool in tools if tool.get('type')=='function' and tool.get('name')==choice['name']]
        if len(selected)!=1:
            raise ModelError('指定工具必须唯一存在于当前允许工具中；未请求模型',request_sent=False)
        if profile=='tokenrhythm-deepseek-chat-v1':return selected,'required',profile
        return tools,dict(type='function',function=dict(name=choice['name'])),profile
    if choice not in ('auto','none','required'):
        raise ModelError('不支持的工具选择方式；未请求模型',request_sent=False)
    if choice=='required' and not tools:
        raise ModelError('要求调用工具时必须提供允许工具；未请求模型',request_sent=False)
    return tools,choice,profile


class ModelError(ValueError):
    def __init__(self, message, status=None, usage=None, request_sent=None, requests=None):
        super().__init__(message)
        self.status = status
        self.usage = usage
        self.request_sent = request_sent
        self.requests = requests


class PlanShapeError(ModelError):
    """A complete compose call whose public argument shape failed validation."""


def substantive_text(value):
    """Reject exact placeholders, without rejecting sentences about testing."""
    return isinstance(value,str) and value.strip().casefold() not in {
        '', 'test', 'testing', 'todo', 'placeholder', 'n/a', '测试', '待填', '待填写'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def metadata(config, data, started, payload=None):
    """Counted/enum diagnostics only. Never retain content or hidden reasoning."""
    def count(value):return value if type(value) is int and value >= 0 else None
    def mapping(value):return value if isinstance(value,dict) else {}
    usage = mapping(data.get('usage'))
    input_tokens=count(usage.get('input_tokens',usage.get('prompt_tokens')))
    output_tokens=count(usage.get('output_tokens',usage.get('completion_tokens')))
    tokens=count(usage.get('total_tokens'))
    tokens_source='reported_total' if tokens is not None else 'unavailable'
    if tokens is None and input_tokens is not None and output_tokens is not None:
        tokens=input_tokens+output_tokens;tokens_source='sum_reported_parts'
    def label(value):
        return redact(value)[:200] if isinstance(value, str) else None
    result=dict(request_model=label(config['model']), response_model=label(data.get('model')),
                response_id=label(data.get('id')), tokens=tokens, tokens_source=tokens_source,
                input_tokens=input_tokens,output_tokens=output_tokens,
                reasoning_tokens=count(mapping(usage.get('output_tokens_details',usage.get('completion_tokens_details'))).get('reasoning_tokens')),
                cached_input_tokens=count(mapping(usage.get('input_tokens_details',usage.get('prompt_tokens_details'))).get('cached_tokens')),
                latency_s=round(time.monotonic()-started,3))
    choices=data.get('choices')
    if isinstance(choices,list) and len(choices)==1 and isinstance(choices[0],dict):
        finish=choices[0].get('finish_reason')
        result['finish_reason']=finish if finish in ('stop','tool_calls','length','content_filter','function_call') else None
        calls=mapping(choices[0].get('message')).get('tool_calls',[])
        result['tool_call_count']=len(calls) if isinstance(calls,list) else None
    elif isinstance(data.get('output'),list):
        status=data.get('status')
        result['response_status']=status if status in ('completed','incomplete','failed','cancelled','queued','in_progress') else None
        result['tool_call_count']=sum(isinstance(x,dict) and x.get('type')=='function_call' for x in data['output'])
    if payload is not None:
        choice=payload.get('tool_choice')
        if isinstance(choice,dict):
            name=choice.get('name',mapping(choice.get('function')).get('name'))
            choice='function:'+name if isinstance(name,str) else None
        tools=payload.get('tools',[])
        result.update(wire_api=label(config.get('wire_api')),request_tool_choice=label(choice),
                      request_tool_count=len(tools),request_max_output_tokens=config['max_output_tokens'],
                      request_size_bytes=len(json.dumps(payload,ensure_ascii=False).encode('utf-8')),
                      request_schema_sha256=hashlib.sha256(json.dumps(tools,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest())
    return result


def function(name, description, properties):
    return dict(type="function", name=name, description=description, strict=True,
                parameters=dict(type="object", properties=properties,
                                required=list(properties), additionalProperties=False))


class ResponsesClient:
    def __init__(self, config, key, opener=None):
        self.config, self._key = config.copy(), key
        self.opener = opener or build_opener(ProxyHandler(), NoRedirect())

    def request(self, instructions, inputs, tools, choice="auto"):
        payload = dict(model=self.config["model"], instructions=instructions, input=inputs,
                       tools=tools, tool_choice=choice, store=False,
                       parallel_tool_calls=False,
                       max_output_tokens=self.config["max_output_tokens"])
        data, started = self._post("/responses", payload)
        try:
            return self._responses_result(data, started, payload)
        except ModelError as exc:
            if isinstance(data,dict):exc.usage=metadata(self.config,data,started,payload)
            exc.request_sent=True
            raise

    def _post(self, endpoint, payload):
        req = Request(self.config["base_url"] + endpoint,
                      json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      {"Content-Type": "application/json", "Authorization": "Bearer " + self._key})
        started = time.monotonic()
        try:
            with self.opener.open(req, timeout=self.config["timeout_s"]) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ModelError("模型响应过大", request_sent=True)
                data = json.loads(raw)
        except HTTPError as exc:
            # Never retain server bodies, headers, request objects or credentials.
            hint = {
                401: "：授权校验未通过，请确认密钥由当前服务签发、API基址及模型权限；不自动重试或切换服务",
                403: "：服务拒绝访问，请检查账号、模型权限或客户端接入限制；不自动重试或切换服务",
            }.get(exc.code, "")
            try:
                error = json.loads(exc.read(10000))
                if isinstance(error, dict) and error.get("type") == "unauthorized_client_error":
                    hint = "：服务拒绝当前应用客户端，需服务方开放接入；不能据此判断密钥无效"
            except (ValueError, OSError):
                pass
            raise ModelError(f"模型服务 HTTP {exc.code}{hint}（未记录响应体）", exc.code, request_sent=True) from None
        except (URLError, TimeoutError, OSError):
            raise ModelError("模型网络连接失败或超时") from None
        except (UnicodeError, json.JSONDecodeError):
            raise ModelError("模型返回非 JSON 数据", request_sent=True) from None
        return data, started

    def _responses_result(self, data, started, payload=None):
        if not isinstance(data, dict) or not isinstance(data.get("output"), list):
            raise ModelError("缺少 Responses output 数组；未切换协议")
        if data.get("status") not in (None, "completed"):
            raise ModelError("模型响应未完成或被截断")
        calls = []
        for item in data["output"]:
            if not isinstance(item, dict):
                raise ModelError("响应项格式无效")
            if item.get("type") == "function_call":
                if not all(isinstance(item.get(k), str) and item[k] for k in ("call_id", "name", "arguments")):
                    raise ModelError("工具调用缺少 call_id、name 或 arguments")
                calls.append(item)
        if len({c["call_id"] for c in calls}) != len(calls):
            raise ModelError("响应包含重复 call_id")
        return dict(output=data["output"], calls=calls, metadata=metadata(self.config, data, started,payload))

    def _probe_request(self, requests, *args):
        """Keep only counted transport diagnostics for every explicit probe step."""
        try:
            reply = self.request(*args)
        except ModelError as exc:
            if exc.request_sent is not False:
                requests.append(exc.usage or dict(tokens=None, tokens_source='unavailable'))
            exc.requests = list(requests)
            raise
        requests.append(reply['metadata'])
        return reply

    @staticmethod
    def _probe_error(message, requests):
        return ModelError(message, usage=requests[-1] if requests else None,
                          request_sent=True, requests=list(requests))

    def test(self):
        requests = []
        tool = function("connection_probe", "Return the probe result", {"nonce": {"type": "string", "enum": ["traffic-probe"]}})
        history = [{"role": "user", "content": "Call connection_probe with nonce traffic-probe."}]
        first = self._probe_request(requests, "Test tool compatibility only.", history, [tool], {"type": "function", "name": "connection_probe"})
        if len(first["calls"]) != 1 or first["calls"][0]["name"] != "connection_probe":
            raise self._probe_error("已连通但没有返回预期工具调用", requests)
        call = first["calls"][0]
        try:
            if json.loads(call["arguments"]) != {"nonce": "traffic-probe"}:
                raise ValueError()
        except ValueError:
            raise self._probe_error("探针工具参数无效", requests) from None
        history.extend(first["output"])
        history.append(dict(type="function_call_output", call_id=call["call_id"], output='{"result":"probe-ok"}'))
        second = self._probe_request(requests, "Acknowledge the tool result with probe-ok.", history, [tool], "none")
        texts = []
        for item in second['output']:
            if item.get('type') != 'message':continue
            content = item.get('content')
            if not isinstance(content, list) or any(not isinstance(c, dict) for c in content):
                raise self._probe_error("工具结果回传格式无效；未自动重试", requests)
            for part in content:
                text = part.get('text', '')
                if not isinstance(text, str):
                    raise self._probe_error("工具结果回传格式无效；未自动重试", requests)
                texts.append(text)
        if "probe-ok" not in " ".join(texts):
            raise self._probe_error("工具结果回传未得到确认", requests)
        return dict(ok=True, stages=["connected", "authenticated", "function_call", "function_call_output"],
                    requests=requests)


class ChatCompletionsClient(ResponsesClient):
    """Single tool decisions. No retries, polling or hidden fallback."""
    def request(self, instructions, inputs, tools, choice="auto"):
        requested_choice=choice
        tools,choice,profile=chat_tool_choice(self.config,tools,choice)
        wrapped = [dict(type="function", function={k: v for k, v in t.items() if k != "type"}) for t in tools]
        payload = dict(model=self.config["model"], messages=[dict(role="system", content=instructions), *inputs],
                       tools=wrapped, tool_choice=choice, stream=False, parallel_tool_calls=False,
                       max_tokens=self.config["max_output_tokens"])
        if profile=='deepseek-official-chat-v1':
            # Official thinking mode rejects required/named tool choices (HTTP 400).
            # Keep one mode throughout this stateless tool workflow, including auto.
            # https://api-docs.deepseek.com/api/create-chat-completion/
            payload['thinking']=dict(type='disabled')
        data, started = self._post("/chat/completions", payload)
        usage=metadata(self.config,data,started,payload) if isinstance(data,dict) else None
        if usage is not None:
            usage.update(transport_version=TRANSPORT_VERSION,tool_choice_profile=profile,
                         requested_tool_choice=('function:'+requested_choice['name']) if isinstance(requested_choice,dict) else requested_choice)
            if profile=='deepseek-official-chat-v1':usage['request_thinking_mode']='disabled'
        try:
            if not isinstance(data, dict) or len(data["choices"]) != 1:
                raise ValueError()
            item = data["choices"][0]
            if item.get('finish_reason')=='length':
                raise ModelError('Chat Completions 输出达到本次上限而被截断；未执行工具、未自动重试',usage=usage,request_sent=True)
            if item["finish_reason"] not in ("stop", "tool_calls"):
                raise ValueError()
            message = item["message"]
            if message["role"] != "assistant":
                raise ValueError()
            calls = []
            for call in message.get("tool_calls", []):
                f = call["function"]
                if call["type"] != "function" or not all(isinstance(v, str) and v for v in (call["id"], f["name"], f["arguments"])):
                    raise ValueError()
                calls.append(dict(type="function_call", call_id=call["id"], name=f["name"], arguments=f["arguments"]))
            if len({c["call_id"] for c in calls}) != len(calls):
                raise ValueError()
        except ModelError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ModelError("Chat Completions 工具响应结构无效；未自动重试",usage=usage,request_sent=True) from None
        return dict(output=[message], calls=calls, metadata=usage)

    def test(self):
        requests = []
        tool = function("connection_probe", "Check function support", {"nonce": {"type": "string", "enum": ["traffic-probe"]}})
        result = self._probe_request(requests, "Call connection_probe only.", [{"role": "user", "content": "nonce: traffic-probe"}],
                              [tool], {"type": "function", "name": "connection_probe"})
        calls = result["calls"]
        try:
            valid = len(calls) == 1 and calls[0]['name'] == 'connection_probe' and json.loads(calls[0]['arguments']) == {'nonce': 'traffic-probe'}
        except ValueError:
            valid = False
        if not valid:
            raise self._probe_error("已连通，但工具参数探针未通过；未自动重试", requests) from None
        return dict(ok=True, stages=["connected", "authenticated", "function_call"], requests=requests)


def client_for(config, key, **kwargs):
    cls = {"responses": ResponsesClient, "chat_completions": ChatCompletionsClient}.get(config["wire_api"])
    if cls is None:
        raise ModelError("不支持的接口类型")
    return cls(config, key, **kwargs)
