"""Synthetic CPU reliability tests; no model/tokenizer/backend is loaded."""
from copy import deepcopy
import errno
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from deepresearch.agent.budget import ResearchLimits, ResearchBudgetExceeded, RunBudget
from deepresearch.models import AgentTrace, RetrievedDocument, ToolRequest
from deepresearch.runtime.context import ContextManager, validate_selected_citations
from deepresearch.runtime.reliable_tools import ToolPolicy, ToolReliabilityLayer
from deepresearch.tools.executor import BudgetExceeded
from deepresearch.utils.redaction import redact


class FixtureCounter:
    is_exact = False
    name = 'synthetic_json_character_units_not_native_tokens'

    def __init__(self):
        self.calls = []

    def count_request(self, operation, payload):
        self.calls.append((operation, deepcopy(payload)))
        return 37 + len(json.dumps({'operation': operation, 'payload': payload},
                                   ensure_ascii=False, sort_keys=True))


def evidence(eid, text, source='https://source.test/paper', version='v1', chunk='p0'):
    return {'evidence_id': eid, 'document_id': 'paper1', 'source': source,
            'source_version': version, 'section_id': 's0', 'chunk_id': chunk,
            'locator': 's0:0:20', 'content': text}


def document(chunk='p0', content='verified original evidence'):
    digest = sha256(content.encode()).hexdigest()
    return RetrievedDocument('paper1', 'https://source.test/paper', 'paper', content, {},
                             source_id='paper1', source_version='v1', chunk_id=chunk,
                             document_sha256=digest, chunk_sha256=digest,
                             span_start=0, span_end=len(content), source_text=content)


class Search:
    def __init__(self, values=None):
        self.values = list(values or [[document()]])
        self.calls = []

    def search(self, query):
        self.calls.append(query)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class Reader:
    def __init__(self, value=None):
        self.value = document() if value is None else value
        self.calls = []

    def read(self, key):
        self.calls.append(key)
        return self.value


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.counter = FixtureCounter()
        self.manager = ContextManager()

    def size(self, operation, payload):
        return FixtureCounter().count_request(operation, payload)

    def test_all_fit_preserves_identity_order_and_duplicates(self):
        payload = {'query': 'evidence', 'evidence': [evidence('e1', 'same'), evidence('e2', 'same')]}
        prepared, decision = self.manager.prepare('answer', payload, self.counter, 10000)
        self.assertIs(prepared, payload)
        self.assertEqual(decision['selected_ids'], ['e1', 'e2'])
        self.assertEqual(decision['count_calls'], 1)
        self.assertFalse(decision['counter_is_exact'])
        self.assertEqual(decision['before_tokens'], decision['after_tokens'])

    def test_disabled_or_no_evidence_does_not_modify_overflow(self):
        payload = {'query': 'long' * 100, 'evidence': [evidence('e1', 'text')]}
        prepared, decision = ContextManager(enabled=False).prepare('answer', payload, self.counter, 1)
        self.assertIs(prepared, payload)
        self.assertEqual(decision['reason'], 'disabled')
        empty = {'query': 'long' * 100}
        prepared, decision = self.manager.prepare('plan', empty, self.counter, 1)
        self.assertIs(prepared, empty)
        self.assertEqual(decision['reason'], 'no_evidence_units')

    def test_whole_unit_eviction_uses_query_and_preserves_source_memory(self):
        payload = {'query': 'important evidence', 'evidence': [
            evidence('e0', 'irrelevant ' * 60, chunk='p0'),
            evidence('e1', 'important evidence retained', chunk='p1'),
            evidence('e2', 'other material', chunk='p2')]}
        original = deepcopy(payload)
        budget = self.size('answer', {'query': payload['query'], 'evidence': [payload['evidence'][1]]})
        prepared, decision = self.manager.prepare('answer', payload, self.counter, budget)
        self.assertEqual(payload, original)
        self.assertEqual(prepared['evidence'], [original['evidence'][1]])
        self.assertEqual(decision['selected_ids'], ['e1'])
        self.assertLessEqual(decision['after_tokens'], budget)
        self.assertFalse(decision['text_truncated'])
        self.assertEqual(decision['count_calls'], len(self.counter.calls))

    def test_remaining_units_preserve_original_order(self):
        units = [evidence('a', 'z' * 300, chunk='a'), evidence('b', 'query', chunk='b'),
                 evidence('c', 'query high', chunk='c')]
        payload = {'query': 'query high', 'evidence': units}
        budget = self.size('answer', {'query': payload['query'], 'evidence': units[1:]})
        prepared, _ = self.manager.prepare('answer', payload, self.counter, budget)
        self.assertEqual([e['evidence_id'] for e in prepared['evidence']], ['b', 'c'])

    def test_duplicate_source_prefers_pinned_citation_without_remapping(self):
        units = [evidence('unpinned', 'exact same source text'), evidence('pinned', 'exact same source text')]
        answer = {'text': 'candidate', 'citations': [{'evidence_id': 'pinned', 'claim': 'exact'}]}
        payload = {'query': 'text', 'evidence': units, 'answer': answer}
        budget = self.size('critic', {**payload, 'evidence': [units[1]]})
        prepared, decision = self.manager.prepare('critic', payload, self.counter, budget)
        self.assertEqual(decision['selected_ids'], ['pinned'])
        self.assertEqual(prepared['answer'], answer)
        self.assertEqual(decision['dropped'][0]['reason'], 'duplicate_same_source_unit')
        self.assertEqual(decision['dropped'][0]['representative_ids'], ['pinned'])

    def test_two_pinned_duplicates_remain_and_overflow_fails_closed(self):
        units = [evidence('a', 'same'), evidence('b', 'same')]
        payload = {'evidence': units, 'answer': {'text': 'same', 'citations': [
            {'evidence_id': 'a', 'claim': 'same'}, {'evidence_id': 'b', 'claim': 'same'}]}}
        prepared, decision = self.manager.prepare('critic', payload, self.counter, 50)
        self.assertEqual(decision['selected_ids'], ['a', 'b'])
        self.assertEqual(decision['status'], 'budget_unresolved')
        self.assertEqual(prepared['answer'], payload['answer'])
        budget = RunBudget(ResearchLimits(max_input_tokens=50))
        with self.assertRaisesRegex(ResearchBudgetExceeded, 'input_token_budget_exceeded'):
            budget.reserve(decision['after_tokens'])

    def test_source_and_version_differences_never_text_dedup(self):
        for second in (evidence('b', 'same', source='https://other.test'),
                       evidence('b', 'same', version='v2')):
            payload = {'query': 'same', 'evidence': [evidence('a', 'same'), second]}
            _, decision = self.manager.prepare('answer', payload, self.counter, 1)
            self.assertFalse(any(d['reason'] == 'duplicate_same_source_unit' for d in decision['dropped']))

    def test_missing_provenance_never_text_dedup(self):
        payload = {'evidence': [{'evidence_id': 'a', 'content': 'same'},
                                {'evidence_id': 'b', 'content': 'same'}]}
        _, decision = self.manager.prepare('answer', payload, self.counter, 1)
        self.assertFalse(any(d['reason'] == 'duplicate_same_source_unit' for d in decision['dropped']))

    def test_full_candidate_and_metadata_counted(self):
        payload = {'query': 'x', 'evidence': [evidence('e', 'a')],
                   'answer': {'text': 'candidate ' * 100, 'citations': [{'evidence_id': 'e', 'claim': 'a'}]},
                   'citation_report': {'findings': [{'structural_valid': True}]}}
        _, decision = self.manager.prepare('critic', payload, self.counter, 300)
        self.assertEqual(decision['status'], 'budget_unresolved')
        self.assertGreater(decision['after_tokens'], 300)
        self.assertEqual(decision['pinned_ids'], ['e'])

    def test_empty_selection_explicit_not_claimed_as_success(self):
        payload = {'query': 'x', 'evidence': [evidence('e', 'large ' * 100)]}
        limit = self.size('answer', {**payload, 'evidence': []})
        prepared, decision = self.manager.prepare('answer', payload, self.counter, limit)
        self.assertEqual(prepared['evidence'], [])
        self.assertEqual(decision['status'], 'empty_after_selection')
        self.assertIn('unknown', decision['semantic_support'])

    def test_unknown_candidate_pin_fails_closed_even_if_fit(self):
        payload = {'evidence': [evidence('a', 'text')], 'answer': {
            'text': 'candidate', 'citations': [{'evidence_id': 'missing', 'claim': 'text'}]}}
        prepared, decision = self.manager.prepare('critic', payload, self.counter, 10000)
        self.assertIs(prepared, payload)
        self.assertEqual(decision['status'], 'invalid_pin_reference')
        self.assertEqual(decision['missing_pin_ids'], ['missing'])

    def test_selected_scope_rejects_unseen_store_citation(self):
        prepared = {'evidence': [evidence('visible', 'text')]}
        valid = {'citations': [{'evidence_id': 'visible', 'claim': 'text'}]}
        self.assertIs(validate_selected_citations(valid, prepared), valid)
        with self.assertRaisesRegex(ValueError, 'outside_supplied'):
            validate_selected_citations({'citations': [{'evidence_id': 'in-store-unseen'}]}, prepared)

    def test_duplicate_id_and_bad_counter_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate_evidence_id'):
            self.manager.prepare('answer', {'evidence': [evidence('a', 'x'), evidence('a', 'y')]},
                                 self.counter, 10000)
        class BadCounter:
            def count_request(self, *args): return 1.5
        with self.assertRaisesRegex(ValueError, 'invalid_native_input'):
            self.manager.prepare('answer', {}, BadCounter(), 100)


class ToolTests(unittest.TestCase):
    def executor(self, search=None, reader=None, **kwargs):
        trace = AgentTrace('cpu-fixture')
        return ToolReliabilityLayer(search or Search(), reader or Reader(), trace, cache=False, **kwargs), trace

    def test_valid_result_has_provenance_and_explicit_counts(self):
        executor, trace = self.executor()
        result = executor.call(ToolRequest('step', 'search', 'evidence'))
        self.assertEqual(result.status, 'ok')
        self.assertEqual(trace.tool_calls, 1)
        self.assertEqual(executor.metrics['physical_attempts'], 1)
        self.assertEqual(executor.metrics['logical_success'], 1)

    def test_rejected_request_consumes_no_physical_budget(self):
        executor, trace = self.executor()
        for tool, argument in [('shell', 'anything'), ('read', '../outside'), ('search', '')]:
            result = executor.call(ToolRequest('step', tool, argument))
            self.assertEqual(result.status, 'error')
        self.assertEqual(trace.tool_calls, 0)
        self.assertEqual(executor.metrics['logical_failure'], 3)

    def test_transient_retry_once_and_recovered_metric(self):
        search = Search([TimeoutError('finished timeout'), [document()]])
        executor, trace = self.executor(search=search, policy=ToolPolicy(retry_enabled=True))
        result = executor.call(ToolRequest('step', 'search', 'evidence'))
        self.assertEqual(result.status, 'ok')
        self.assertEqual(trace.tool_calls, 2)
        self.assertEqual(executor.metrics['retried_requests'], 1)
        self.assertEqual(executor.metrics['retry_recovered'], 1)
        self.assertEqual(len(search.calls), 2)

    def test_retry_disabled_default_preserves_failure(self):
        search = Search([TimeoutError('timeout'), [document()]])
        executor, trace = self.executor(search=search)
        result = executor.call(ToolRequest('step', 'search', 'q'))
        self.assertEqual(result.status, 'error')
        self.assertEqual(trace.tool_calls, 1)
        self.assertEqual(len(search.calls), 1)

    def test_retry_limit_and_physical_budget_preserve_attempt(self):
        search = Search([OSError(errno.ECONNRESET, 'connection reset'), [document()]])
        executor, trace = self.executor(search=search, max_calls=1, policy=ToolPolicy(retry_enabled=True))
        with self.assertRaises(BudgetExceeded):
            executor.call(ToolRequest('step', 'search', 'q'))
        self.assertEqual(trace.tool_calls, 1)
        self.assertEqual(len(search.calls), 1)
        self.assertEqual(executor.metrics['logical_failure'], 1)
        self.assertTrue(any(e.status == 'budget_exceeded' for e in trace.events))
        failing, trace = self.executor(search=Search([TimeoutError('a'), TimeoutError('b'), [document()]]),
                                       policy=ToolPolicy(retry_enabled=True))
        result = failing.call(ToolRequest('step', 'search', 'q'))
        self.assertEqual(result.status, 'error')
        self.assertEqual(trace.tool_calls, 2)

    def test_validation_and_non_transient_errors_never_retry(self):
        for value in ({'documents': []}, [None], ValueError('invalid'), FileNotFoundError('missing')):
            search = Search([value, [document()]])
            executor, trace = self.executor(search=search, policy=ToolPolicy(retry_enabled=True))
            self.assertEqual(executor.call(ToolRequest('step', 'search', 'q')).status, 'error')
            self.assertEqual(trace.tool_calls, 1)
            self.assertEqual(len(search.calls), 1)

    def test_invalid_sha_span_and_read_identity_fail(self):
        bad_sha = document(); bad_sha.chunk_sha256 = 'a' * 64
        bad_span = document(); bad_span.span_end -= 1
        for bad in (bad_sha, bad_span):
            executor, trace = self.executor(reader=Reader(bad), policy=ToolPolicy(retry_enabled=True))
            self.assertEqual(executor.call(ToolRequest('step', 'read', 'p0')).status, 'error')
            self.assertEqual(trace.tool_calls, 1)
        executor, _ = self.executor(reader=Reader(document('different')))
        self.assertIn('identity_mismatch', executor.call(ToolRequest('step', 'read', 'p0')).error)

    def test_empty_search_is_successful_empty_not_fallback(self):
        executor, _ = self.executor(search=Search([[]]))
        result = executor.call(ToolRequest('step', 'search', 'no hits'))
        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.documents, [])
        self.assertEqual(executor.metrics['empty_search_results'], 1)
        self.assertTrue(executor.trace.events[-1].payload['result']['empty_result'])
        with self.assertRaisesRegex(ValueError, 'fallback_forbidden'):
            ToolPolicy(fallback='empty_success')

    def test_cache_receipt_sha_revalidation_and_mutation_isolation(self):
        trace = AgentTrace('cache')
        executor = ToolPolicy().create_executor(Search(), Reader(), trace, max_calls=18, cache=True)
        request = ToolRequest('step', 'search', 'q')
        result = executor.call(request)
        result.documents[0].retrieval_score = 999
        cached = executor.call(ToolRequest('step', 'search', 'q'))
        self.assertEqual(cached.documents[0].retrieval_score, 0)
        self.assertEqual(executor.hits, 1)
        self.assertEqual(trace.tool_calls, 1)
        executor.entries[('search', 'q')][0].content = 'tampered'
        self.assertIn('cached_receipt_invalid', executor.call(ToolRequest('step', 'search', 'q')).error)
        self.assertEqual(trace.tool_calls, 1)

    def test_cache_same_hash_but_changed_authoritative_version_is_rejected(self):
        from deepresearch.benchmarks.runtime import BenchmarkCorpusReader
        reader = BenchmarkCorpusReader({'paper1': [{'unit_id': 'p0', 'locator': 's0',
                                                     'text': 'original source text'}]},
                                       revision='v1', license='fixture', source_url='https://source.test')
        search = Search([[reader.read('p0')]])
        trace = AgentTrace('source-version')
        executor = ToolPolicy().create_executor(search, reader, trace, cache=True)
        self.assertEqual(executor.call(ToolRequest('step', 'search', 'q')).status, 'ok')
        reader.chunks['p0'].source_version = 'v2'
        result = executor.call(ToolRequest('step', 'search', 'q'))
        self.assertEqual(result.status, 'error')
        self.assertIn('current_source_mismatch:source_version', result.error)
        self.assertEqual(trace.tool_calls, 1)

    def test_default_cooperative_tools_remain_on_sqlite_owner_thread(self):
        db = sqlite3.connect(':memory:')
        thread_id = threading.get_ident()
        class SqliteReader:
            def read(self, key):
                self.observed_thread = threading.get_ident()
                db.execute('SELECT 1').fetchone()
                return document(key)
        reader = SqliteReader()
        executor, _ = self.executor(reader=reader)
        try:
            self.assertEqual(executor.call(ToolRequest('step', 'read', 'p0')).status, 'ok')
            self.assertEqual(reader.observed_thread, thread_id)
            self.assertFalse(executor.trace.events[-1].payload['timeout_is_preemptive'])
        finally:
            db.close()

    def test_cooperative_completed_late_is_not_retried(self):
        now = [0.0]
        class SlowReader:
            def read(self, key):
                now[0] = 6.0
                return document(key)
        executor, trace = self.executor(reader=SlowReader(), clock=lambda: now[0],
                                        policy=ToolPolicy(timeout_seconds=1, retry_enabled=True))
        result = executor.call(ToolRequest('step', 'read', 'p0'))
        self.assertIn('completed_late_no_retry', result.error)
        self.assertEqual(trace.tool_calls, 1)
        self.assertTrue(trace.events[-1].payload['underlying_execution_stopped'])

    def test_remaining_task_deadline_never_resets(self):
        executor, trace = self.executor()
        executor.deadline_remaining = lambda: 0.0
        result = executor.call(ToolRequest('step', 'read', 'p0'))
        self.assertIn('task_deadline_exhausted', result.error)
        self.assertEqual(trace.tool_calls, 0)

    def test_thread_mode_requires_explicit_safe_fixture(self):
        executor, trace = self.executor(policy=ToolPolicy(timeout_mode='thread'))
        result = executor.call(ToolRequest('step', 'read', 'p0'))
        self.assertIn('explicit_thread_safe', result.error)
        self.assertEqual(trace.tool_calls, 0)

    def test_thread_timeout_no_overlap_or_late_receipt_acceptance(self):
        gate = threading.Event()
        class BlockingReader:
            runtime_thread_safe = True
            def __init__(self): self.calls = 0
            def read(self, key):
                self.calls += 1
                gate.wait(1.0)
                return document(key)
        reader = BlockingReader()
        executor, trace = self.executor(reader=reader,
                                        policy=ToolPolicy(timeout_mode='thread', timeout_seconds=.02,
                                                          retry_enabled=True))
        try:
            first = executor.call(ToolRequest('step', 'read', 'p0'))
            self.assertIn('timeout_in_flight_no_retry', first.error)
            self.assertIsNone(trace.events[-1].payload['underlying_execution_stopped'])
            self.assertTrue(executor.close()['in_flight'])
            blocked = executor.call(ToolRequest('step', 'read', 'p0'))
            self.assertIn('prior_request_still_in_flight', blocked.error)
            self.assertEqual(trace.tool_calls, 1)
            self.assertEqual(reader.calls, 1)
            gate.set()
            self.assertTrue(executor._pending['done'].wait(.5))
            last = executor.call(ToolRequest('step', 'read', 'p0'))
            self.assertEqual(last.status, 'ok')
            self.assertEqual(reader.calls, 2)
            self.assertEqual(trace.tool_calls, 2)
            self.assertEqual(executor.metrics['retry_recovered'], 0)
            self.assertTrue(any(e.kind == 'tool_late_completion' and not e.payload['receipt_accepted']
                                for e in trace.events))
        finally:
            gate.set()

    def test_trace_error_and_request_secrets_are_redacted(self):
        secret = 'fixture-sensitive-credential'
        search = Search([OSError(errno.ECONNRESET, 'Bearer ' + secret)])
        executor, trace = self.executor(search=search, redactor=lambda value: redact(value, (secret,)))
        result = executor.call(ToolRequest('step', 'search', 'api_key=' + secret))
        self.assertEqual(result.status, 'error')
        self.assertNotIn(secret, result.error)
        self.assertNotIn(secret, trace.to_json())
        self.assertEqual(search.calls, ['api_key=' + secret])

    def test_total_physical_budget_includes_all_requests(self):
        search = Search([[document()] for _ in range(19)])
        executor, trace = self.executor(search=search, max_calls=18)
        for i in range(18):
            self.assertEqual(executor.call(ToolRequest('step', 'search', str(i))).status, 'ok')
        with self.assertRaises(BudgetExceeded):
            executor.call(ToolRequest('step', 'search', 'extra'))
        self.assertEqual(trace.tool_calls, 18)
        self.assertEqual(len(search.calls), 18)
        with self.assertRaises(ValueError):
            ToolPolicy(max_retries=2)


if __name__ == '__main__':
    unittest.main()
