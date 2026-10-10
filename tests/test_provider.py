import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, Mock
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from deepresearch.llm.openai_compatible import (
    OpenAICompatibleProvider, ProviderConfig, ProviderError, HTTPResponse, UrllibHTTPClient, NoRedirect)
from deepresearch.llm.validation import validate_output, OutputValidationError
from deepresearch.utils.redaction import redact
from deepresearch.agent.workflow import Agent
from deepresearch.llm.provider import MockProvider
from deepresearch.retrieval.offline import OfflineSearchProvider
from deepresearch.models import UserQuery, AgentTrace, ToolRequest
from deepresearch.tools.executor import ToolExecutor


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.delays = []

    def time(self):
        return self.now

    def sleep(self, delay):
        self.delays.append(delay)
        self.now += delay


def response(output=None, status=200, usage=True, headers=None):
    envelope = {"id": "mock-response", "choices": [{"finish_reason": "stop",
                "message": {"content": json.dumps(output or {"queries": ["Atlas"]})}}]}
    if usage:
        envelope["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    return HTTPResponse(status, json.dumps(envelope).encode(), headers or {})


class MockHTTPClient:
    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.requests = []

    def post(self, endpoint, headers, body, timeout):
        # Keep only a test-local request, never write credential-bearing headers to artifacts.
        self.requests.append((endpoint, headers, body, timeout))
        item = self.sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.secret = uuid4().hex
        self.env_patch = patch.dict(os.environ, {"DEEPRESEARCH_API_KEY": self.secret})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        # Any accidental real socket use fails the entire test, independently of allow_network.
        self.socket_patch = patch("socket.socket", side_effect=AssertionError("network forbidden in tests"))
        self.socket_patch.start()
        self.addCleanup(self.socket_patch.stop)

    def provider(self, sequence, **kwargs):
        clock = FakeClock()
        client = MockHTTPClient(sequence)
        config = ProviderConfig("mock-model", "https://mock.invalid/v1/chat/completions", **kwargs)
        return OpenAICompatibleProvider(config, client, clock.time, clock.sleep, wall_clock=lambda:0), client, clock

    def test_success_and_configuration(self):
        p, c, _ = self.provider([response()], timeout_seconds=7, max_output_tokens=19)
        self.assertEqual(json.loads(p.complete("plan", {"query": "Atlas"})), {"queries": ["Atlas"]})
        endpoint, headers, body, timeout = c.requests[0]
        self.assertEqual(timeout, 7)
        self.assertEqual(body["model"], "mock-model")
        self.assertEqual(body["max_tokens"], 19)
        self.assertEqual(headers["Authorization"], "Bearer " + self.secret)
        self.assertEqual(p.attempts[0]["usage"]["total_tokens"], 15)
        self.assertNotIn(self.secret, json.dumps(p.attempts))

    def test_timeout_retry(self):
        p, c, _ = self.provider([TimeoutError(self.secret), response()])
        p.complete("plan", {})
        self.assertEqual(len(c.requests), 2)
        self.assertEqual(p.attempts[0]["error"], "timeout")
        self.assertIsNone(p.attempts[0]["usage"])

    def test_429_and_retry_after(self):
        p, c, clock = self.provider([response(status=429, headers={"Retry-After": "4"}), response()])
        p.complete("plan", {})
        self.assertEqual(len(c.requests), 2)
        self.assertEqual(p.attempts[0]["retry_delay_seconds"], 4)
        self.assertIn(4, clock.delays)

    def test_retry_after_http_date(self):
        p, _, clock = self.provider([response(status=429,headers={'Retry-After':'Thu, 01 Jan 1970 00:00:05 GMT'}),response()])
        p.complete('plan',{})
        self.assertEqual(p.attempts[0]['retry_delay_seconds'],5)
        self.assertIn(5,clock.delays)

    def test_retry_after_exceeds_cap_stops_instead_of_retrying_early(self):
        p, c, clock = self.provider([response(status=429,headers={'Retry-After':'40'})],max_retry_delay_seconds=30)
        with self.assertRaisesRegex(ProviderError,'retry_after_exceeds_wait_limit'): p.complete('plan',{})
        self.assertEqual(len(c.requests),1)
        self.assertEqual(clock.delays,[])

    def test_5xx_retry(self):
        for code in (500, 502, 503, 504):
            with self.subTest(code=code):
                p, c, _ = self.provider([response(status=code), response()])
                p.complete("plan", {})
                self.assertEqual(len(c.requests), 2)

    def test_retry_limit(self):
        p, c, _ = self.provider([TimeoutError()] * 4, max_retries=2)
        with self.assertRaisesRegex(ProviderError, "timeout"):
            p.complete("plan", {})
        self.assertEqual(len(c.requests), 3)
        self.assertEqual(len(p.attempts), 3)

    def test_zero_retries(self):
        p, c, _ = self.provider([response(status=503)], max_retries=0)
        with self.assertRaisesRegex(ProviderError, "http_503"):
            p.complete("plan", {})
        self.assertEqual(len(c.requests), 1)

    def test_nonretryable_http(self):
        for code in (400, 401, 403, 404, 302):
            p, c, _ = self.provider([response(status=code)])
            with self.assertRaises(ProviderError):
                p.complete("plan", {})
            self.assertEqual(len(c.requests), 1)

    def test_invalid_response_json(self):
        p, c, _ = self.provider([HTTPResponse(200, b"{", {})])
        with self.assertRaisesRegex(ProviderError, "invalid_response_json"):
            p.complete("plan", {})
        self.assertEqual(len(c.requests), 1)

    def test_invalid_model_json_and_usage_preserved(self):
        r = response()
        envelope = json.loads(r.body)
        envelope["choices"][0]["message"]["content"] = "{"
        r.body = json.dumps(envelope).encode()
        p, c, _ = self.provider([r])
        with self.assertRaisesRegex(ProviderError, "invalid_json"):
            p.complete("plan", {})
        self.assertEqual(len(c.requests), 1)
        self.assertEqual(p.attempts[0]["usage"]["total_tokens"], 15)

    def test_missing_fields(self):
        for content in ({"choices": []}, {"usage": {}}, []):
            p, c, _ = self.provider([HTTPResponse(200, json.dumps(content).encode(), {})])
            with self.assertRaises(ProviderError):
                p.complete("plan", {})
            self.assertEqual(len(c.requests), 1)
        p, _, _ = self.provider([response({"not_queries": []})])
        with self.assertRaisesRegex(ProviderError, "missing_or_extra_field"):
            p.complete("plan", {})

    def test_missing_or_bad_usage(self):
        for usage in (None, {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3},
                      {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 8}):
            r = response()
            envelope = json.loads(r.body)
            envelope["usage"] = usage
            r.body = json.dumps(envelope).encode()
            p, _, _ = self.provider([r])
            with self.assertRaisesRegex(ProviderError, "missing_or_invalid_usage"):
                p.complete("plan", {})

    def test_finish_reason(self):
        r = response()
        envelope = json.loads(r.body)
        envelope["choices"][0]["finish_reason"] = "length"
        r.body = json.dumps(envelope).encode()
        p, _, _ = self.provider([r])
        with self.assertRaisesRegex(ProviderError, "incomplete_or_refused"):
            p.complete("plan", {})

    def test_operation_contracts(self):
        for op, value in (("plan", {"queries": [""]}), ("plan", {"queries": [1]}),
                          ("critic", {"sufficient": "true", "reason": "", "missing_queries": [], "conflicts": []}),
                          ("answer", {"text": "", "citations": []})):
            with self.assertRaises(OutputValidationError):
                validate_output(op, json.dumps(value))
        for text in ('{"queries":[],"queries":["x"]}', '{"queries":[NaN]}'):
            with self.assertRaises(OutputValidationError):
                validate_output("plan", text)

    def test_schema_and_token_parameter_modes(self):
        for mode in ("json_schema", "json_object", "none"):
            p, c, _ = self.provider([response()], response_format=mode, output_token_parameter="max_completion_tokens")
            p.complete("plan", {})
            self.assertIn("max_completion_tokens", c.requests[0][2])
            self.assertEqual("response_format" in c.requests[0][2], mode != "none")

    def test_rate_limit_applies_across_calls(self):
        p, c, clock = self.provider([response(), response()], requests_per_minute=10)
        p.complete("plan", {})
        p.complete("plan", {})
        self.assertEqual(clock.delays, [6.0])
        self.assertEqual(p.attempts[1]["rate_wait_seconds"], 6)

    def test_missing_key(self):
        with patch.dict(os.environ, {}, clear=True):
            p, c, _ = self.provider([response()])
            with self.assertRaisesRegex(ProviderError, "missing_or_invalid_api_key"):
                p.complete("plan", {})
            self.assertEqual(c.requests, [])

    def test_network_default_disabled(self):
        p = OpenAICompatibleProvider(ProviderConfig("mock", "https://mock.invalid/chat/completions"))
        with self.assertRaisesRegex(ProviderError, "network_disabled"):
            p.complete("plan", {})
        self.assertFalse(p.attempts[0]["sent"])

    def test_redaction_in_payload_output_and_error(self):
        p, c, _ = self.provider([response({"queries": [self.secret]})])
        text = p.complete("plan", {"query": self.secret, "nested": {"api_key": self.secret}})
        self.assertNotIn(self.secret, text)
        self.assertNotIn(self.secret, json.dumps(c.requests[0][2]))
        p, _, _ = self.provider([RuntimeError("Authorization: Bearer " + self.secret)])
        with self.assertRaises(ProviderError) as caught:
            p.complete("plan", {})
        self.assertNotIn(self.secret, str(caught.exception) + json.dumps(p.attempts))

    def test_custom_environment_name_redacted(self):
        secret = uuid4().hex
        with patch.dict(os.environ, {'CUSTOM_CREDENTIAL':secret}):
            p, _, _ = self.provider([response({'queries':[secret]})], api_key_env='CUSTOM_CREDENTIAL')
            self.assertNotIn(secret,p.complete('plan',{'query':secret}))
            self.assertNotIn(secret,json.dumps(p.attempts))

    def test_response_size_limit(self):
        p, _, _ = self.provider([HTTPResponse(200, b" " * 21, {})], max_response_bytes=20)
        with self.assertRaisesRegex(ProviderError, "response_too_large"):
            p.complete("plan", {})

    def test_invalid_config(self):
        for args in ({"timeout_seconds": 0}, {"max_retries": -1}, {"max_retries": 11},
                     {"requests_per_minute": float("nan")}, {"allow_network": "false"}):
            with self.assertRaises(ValueError):
                ProviderConfig("x", "https://mock.invalid/chat/completions", **args)
        for endpoint in ("http://mock.invalid/chat/completions", "https://user:pass@mock.invalid/chat/completions",
                         "https://mock.invalid/chat/completions?api_key=x", "https://mock.invalid/v1"):
            with self.assertRaises(ValueError):
                ProviderConfig("x", endpoint)

    def test_redirects_rejected(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://else.invalid"))

    def test_standard_http_adapter_uses_mock_opener(self):
        client = UrllibHTTPClient(1000)
        handle = Mock()
        handle.code = 200
        handle.headers = {"Content-Type": "application/json"}
        handle.read.return_value = b'{}'
        context = Mock()
        context.__enter__ = Mock(return_value=handle)
        context.__exit__ = Mock(return_value=False)
        client.opener = Mock()
        client.opener.open.return_value = context
        result = client.post("https://mock.invalid/chat/completions", {}, {"model": "x"}, 3)
        self.assertEqual(result.body, b'{}')
        self.assertEqual(client.opener.open.call_args.kwargs["timeout"], 3)


class TraceAndAblationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.search = OfflineSearchProvider(self.root / "data/fixtures/documents.json")

    def test_independent_critic_replan_switches(self):
        for critic in (False, True):
            for replan in (False, True):
                run = Agent(MockProvider(), self.search, self.search, critic=critic, replan=replan).run(UserQuery("UnknownXYZ"))
                self.assertEqual(len(run["plans"]), 2 if replan else 1)
                self.assertEqual(any(e["kind"] == "llm:critic" for e in run["trace"]["events"]), critic)

    def test_trace_inputs_evidence_citations(self):
        run = Agent(MockProvider(), self.search, self.search).run(UserQuery("Atlas"))
        events = run["trace"]["events"]
        self.assertIn("input", next(e for e in events if e["kind"] == "llm:plan")["payload"])
        self.assertEqual(len([e for e in events if e["kind"] == "evidence"]), 1)
        self.assertEqual(events[-1]["payload"]["citations"], run["answer"]["citations"])
        self.assertEqual(run["trace"]["model_calls"], 3)

    def test_remote_usage_failure_is_not_zero(self):
        secret = uuid4().hex
        with patch.dict(os.environ, {"DEEPRESEARCH_API_KEY": secret}):
            clock = FakeClock()
            client = MockHTTPClient([TimeoutError(secret), response(), response(
                {"sufficient": True, "reason": "ok", "missing_queries": [], "conflicts": []}),
                response({"text": "unsupported", "citations": [{"evidence_id": "fake", "claim": "bad"}]})])
            provider = OpenAICompatibleProvider(ProviderConfig("mock", "https://mock.invalid/chat/completions"),
                                                client, clock.time, clock.sleep)
            run = Agent(provider, self.search, self.search).run(UserQuery("Atlas"))
            trace = run["trace"]
            self.assertEqual(trace["http_attempts"], 4)
            self.assertEqual(trace["token_usage_reported"], 45)
            self.assertEqual(trace["usage_unreported_attempts"], 1)
            self.assertIsNone(trace["token_usage"])
            self.assertFalse(trace["token_usage_complete"])
            self.assertEqual(len([e for e in trace["events"] if e["kind"] == "llm_attempt"]), 4)
            self.assertEqual(run["answer"]["status"], "failed")
            self.assertNotIn(secret, json.dumps(run))

    def test_complete_remote_usage(self):
        with patch.dict(os.environ, {"DEEPRESEARCH_API_KEY": uuid4().hex}):
            client = MockHTTPClient([response({"wrong": "field"})])
            clock = FakeClock()
            provider = OpenAICompatibleProvider(ProviderConfig("mock", "https://mock.invalid/chat/completions"),
                                                client, clock.time, clock.sleep)
            run = Agent(provider, self.search, self.search).run(UserQuery("Atlas"))
            self.assertEqual(run["trace"]["token_usage"], 15)
            self.assertTrue(run["trace"]["token_usage_complete"])
            self.assertEqual(run["answer"]["status"], "failed")

    def test_tool_and_model_exception_secrets_redacted(self):
        secret = uuid4().hex
        with patch.dict(os.environ, {"DEEPRESEARCH_API_KEY": secret}):
            run = Agent(MockProvider({"plan": [RuntimeError(secret)]}), self.search, self.search).run(UserQuery(secret))
            self.assertNotIn(secret, json.dumps(run))
            trace = AgentTrace("q")
            executor = ToolExecutor(self.search, self.search, trace)
            executor.call(ToolRequest("t", "read", secret))
            self.assertNotIn(secret, trace.to_json())
            self.assertEqual(redact({"Authorization": secret}), {"Authorization": "[REDACTED]"})


if __name__ == "__main__":
    unittest.main()
