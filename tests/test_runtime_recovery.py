"""CPU-only durable-execution evidence. Mock outcomes are engineering fixtures."""
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from deepresearch.agent.research import ResearchEvidenceAgent
from deepresearch.agent.budget import ResearchLimits
from deepresearch.llm.provider import MockProvider
from deepresearch.models import UserQuery, RetrievedDocument
from deepresearch.retrieval.corpus import CorpusReader
from deepresearch.retrieval.bm25 import BM25SearchProvider
from deepresearch.runtime.resumable_agent import StatefulResearchAgent
from deepresearch.runtime.state import (AgentState, CheckpointStore,
    CheckpointIntegrityError, RecoveryBlocked)


class Interrupt(BaseException):
    pass


class Clock:
    def __init__(self):
        self.now = 1000.0
    def __call__(self):
        return self.now


class SpyProvider(MockProvider):
    def __init__(self, log=None, scenarios=None):
        super().__init__(scenarios)
        self.calls = []
        self.log = Path(log) if log else None
    def complete(self, operation, payload):
        self.calls.append((operation, json.loads(json.dumps(payload))))
        if self.log:
            with self.log.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'kind': 'model', 'operation': operation}) + '\n')
                stream.flush(); os.fsync(stream.fileno())
        return super().complete(operation, payload)


class SpyRetriever(BM25SearchProvider):
    def __init__(self, reader, log=None):
        super().__init__(reader, top_k=1)
        self.log = Path(log) if log else None
        self.calls = 0
    def search(self, query):
        self.calls += 1
        if self.log:
            with self.log.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'kind': 'search'}) + '\n')
                stream.flush(); os.fsync(stream.fileno())
        return super().search(query)


def fixtures(cls=StatefulResearchAgent, provider=None, log=None, **kwargs):
    reader = CorpusReader(ROOT / 'examples/fixtures/synthetic_corpus.json')
    retriever = SpyRetriever(reader, log)
    provider = provider or SpyProvider(log)
    return cls(provider, retriever, reader, **kwargs), provider, retriever


def normalized(value):
    # UUIDs/time are not algorithm or prompt differences. Stable evidence IDs,
    # prompts, statuses, order, reservations and costs remain in the comparison.
    excluded = {'query_id', 'trace_id', 'plan_id', 'request_id', 'feedback_id',
        'citation_id', 'answer_id', 'logical_call_id', 'retrieved_at', 'elapsed_ms',
        'latency_ms', 'runtime_state', 'events_complete', 'event_count', 'event_log_path',
        'event_log_sha256'}
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in value.items() if k not in excluded}
    if isinstance(value, list):
        return [normalized(v) for v in value]
    return value


class RuntimeRecoveryTests(unittest.TestCase):
    def test_default_engine_exact_algorithm_and_payload_parity(self):
        configurations = [
            {'planner': False, 'memory': False, 'critic': False, 'replan': False},
            {'planner': True, 'memory': False, 'critic': False, 'replan': False},
            {}, {'deduplicate': True}, {'cache': True}, {'retrieval': False},
            {'reading': False}, {'critic': False},
            {'limits': ResearchLimits(max_total_tokens=10)},
            {'limits': ResearchLimits(max_tool_calls=1)},
            {'limits': ResearchLimits(max_model_calls=1)},
            {'limits': ResearchLimits(max_input_tokens=10)},
        ]
        for query in ('NF4', 'NF4;NF4', 'unmatchedxyz'):
            for flags in configurations:
                with self.subTest(query=query, flags=flags):
                    old, p0, _ = fixtures(ResearchEvidenceAgent, **flags)
                    new, p1, _ = fixtures(trace_max_events=256, **flags)
                    r0 = old.run(UserQuery(query, 'same-fixture'))
                    r1 = new.run(UserQuery(query, 'same-fixture'))
                    self.assertEqual(normalized(r0), normalized(r1))
                    self.assertEqual(p0.calls, p1.calls)

    def test_invalid_model_output_terminal_is_never_retried(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            provider = SpyProvider(scenarios={'answer': ['invalid output']})
            first, _, _ = fixtures(provider=provider)
            result = first.run(UserQuery('NF4', 'invalid'), directory)
            self.assertEqual(result['answer']['status'], 'failed')
            second, p2, search = fixtures()
            repeated = second.run(UserQuery('NF4', 'invalid'), directory)
            self.assertEqual(result, repeated)
            self.assertEqual(p2.calls, [])
            self.assertEqual(search.calls, 0)

    def test_failure_and_replan_paths_keep_legacy_parity(self):
        cases = [
            {'plan': [json.dumps({'queries': []})]},
            {'plan': [json.dumps({'queries': ['NF4'] * 5})]},
            {'answer': ['not-json']},
            {'answer': [json.dumps({'text': 'bad', 'citations': [{'evidence_id': 'unknown', 'claim': 'bad'}]})]},
            {'critic': [json.dumps({'sufficient': False, 'reason': 'fixture', 'missing_queries': ['NF4'], 'conflicts': []})]},
        ]
        for scenario in cases:
            with self.subTest(scenario=scenario):
                old, p0, _ = fixtures(ResearchEvidenceAgent, provider=SpyProvider(scenarios=scenario))
                new, p1, _ = fixtures(provider=SpyProvider(scenarios=scenario), trace_max_events=256)
                r0 = old.run(UserQuery('NF4', 'failure-parity'))
                r1 = new.run(UserQuery('NF4', 'failure-parity'))
                self.assertEqual(normalized(r0), normalized(r1))
                self.assertEqual(p0.calls, p1.calls)

    def test_active_owned_worker_lease_blocks_second_executor(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            first = CheckpointStore(directory); second = CheckpointStore(directory)
            with first.lease():
                with self.assertRaisesRegex(RecoveryBlocked, 'active_worker'):
                    with second.lease():
                        self.fail('second worker acquired active lease')

    def test_multiple_evidence_order_and_payload_preserved_on_restore(self):
        def make():
            reader = CorpusReader(ROOT / 'examples/fixtures/synthetic_corpus.json')
            provider = SpyProvider()
            agent = StatefulResearchAgent(provider, BM25SearchProvider(reader, top_k=5), reader, trace_max_events=256)
            return agent, provider
        query = UserQuery('Qwen license', 'multi-evidence')
        baseline, baseline_provider = make()
        baseline_result = baseline.run(query)
        self.assertGreater(len(baseline_result['evidence']), 1)
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            first, first_provider = make()
            def fault(boundary, state):
                if boundary == 'model_receipt:r0/answer':
                    raise Interrupt()
            with self.assertRaises(Interrupt):
                first.run(query, directory, fault)
            second, second_provider = make()
            result = second.run(query, directory)
            self.assertEqual(first_provider.calls + second_provider.calls, baseline_provider.calls)
            self.assertEqual([e['evidence_id'] for e in result['evidence']],
                             [e['evidence_id'] for e in baseline_result['evidence']])
            self.assertEqual(normalized(result), normalized(baseline_result))

    def test_receipt_gap_reuses_model_without_reserving_twice(self):
        uninterrupted, _, _ = fixtures()
        baseline = uninterrupted.run(UserQuery('NF4', 'receipt-gap'))
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            first, p1, _ = fixtures()
            def fault(boundary, state):
                if boundary == 'model_receipt:r0/plan':
                    raise Interrupt()
            with self.assertRaises(Interrupt):
                first.run(UserQuery('NF4', 'receipt-gap'), directory, fault)
            second, p2, _ = fixtures()
            result = second.run(UserQuery('NF4', 'receipt-gap'), directory)
            self.assertEqual([op for op, _ in p1.calls], ['plan'])
            self.assertEqual([op for op, _ in p2.calls], ['answer', 'critic'])
            self.assertEqual(result['trace']['model_calls'], 3)
            self.assertEqual(result['trace']['tool_calls'], 2)
            self.assertEqual(result['trace']['budget']['model_calls'], 3)
            self.assertEqual(result['trace']['budget']['reserved_tokens'],
                             baseline['trace']['budget']['reserved_tokens'])
            self.assertEqual(result['runtime_state']['receipt_reuse_count'], 1)
            self.assertEqual(result['runtime_state']['lost_steps'], 0)

    def test_inflight_model_without_receipt_is_blocked(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            first, p1, _ = fixtures()
            def fault(boundary, state):
                if boundary.startswith('model_intent:'):
                    raise Interrupt()
            with self.assertRaises(Interrupt):
                first.run(UserQuery('NF4', 'uncertain'), directory, fault)
            second, p2, _ = fixtures()
            with self.assertRaisesRegex(RecoveryBlocked, 'ambiguous_inflight_call:model'):
                second.run(UserQuery('NF4', 'uncertain'), directory)
            self.assertEqual(p1.calls, [])
            self.assertEqual(p2.calls, [])

    def test_actual_owned_child_restart_at_committed_receipt(self):
        for boundary in ('model_receipt:r0/plan', 'tool_receipt:r0/t0/search'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory(dir=ROOT) as directory:
                log = Path(directory) / 'physical_calls.jsonl'
                command = [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--child', directory, str(log)]
                first = subprocess.run(command + [boundary], capture_output=True, text=True)
                self.assertEqual(first.returncode, 37, first.stderr)
                second = subprocess.run(command + ['none'], capture_output=True, text=True)
                self.assertEqual(second.returncode, 0, second.stderr)
                result = json.loads((Path(directory) / 'child_result.json').read_text(encoding='utf-8'))
                calls = [json.loads(x) for x in log.read_text(encoding='utf-8').splitlines()]
                self.assertEqual([c['operation'] for c in calls if c['kind'] == 'model'], ['plan', 'answer', 'critic'])
                self.assertEqual(sum(c['kind'] == 'search' for c in calls), 1)
                self.assertEqual(result['answer']['status'], 'completed')
                self.assertEqual(result['runtime_state']['physical_replay_model_calls'], 0)
                self.assertEqual(result['runtime_state']['physical_replay_tool_calls'], 0)
                self.assertEqual(result['runtime_state']['receipt_reuse_count'], 1)

    def test_tool_timeout_result_preserved_without_resampling(self):
        class TimeoutReader:
            def read(self, key):
                raise TimeoutError('synthetic_tool_timeout')
        class Search:
            backend_name = 'synthetic-timeout-fixture'
            def search(self, query):
                return [RetrievedDocument('doc', 'fixture', 'fixture', 'evidence', {})]
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            p = SpyProvider()
            agent = StatefulResearchAgent(p, Search(), TimeoutReader(), planner=False, critic=False, replan=False)
            result = agent.run(UserQuery('fixture', 'tool-timeout'), directory)
            self.assertEqual(result['answer']['status'], 'insufficient_evidence')
            self.assertEqual(result['trace']['tool_calls'], 2)
            self.assertTrue(any(e['kind'] == 'tool' and 'synthetic_tool_timeout' in e['error'] for e in result['trace']['events']))
            self.assertEqual(agent.run(UserQuery('fixture', 'tool-timeout'), directory), result)

    def test_snapshot_and_event_SHA_tampering_block(self):
        for location in ('snapshot', 'journal'):
            with self.subTest(location=location), tempfile.TemporaryDirectory(dir=ROOT) as directory:
                first, _, _ = fixtures()
                def fault(boundary, state):
                    if boundary == 'model_receipt:r0/plan':
                        raise Interrupt()
                with self.assertRaises(Interrupt):
                    first.run(UserQuery('NF4', 'tamper'), directory, fault)
                head = json.loads((Path(directory) / 'HEAD.json').read_bytes())
                if location == 'snapshot':
                    target = Path(directory) / 'snapshots' / (head['snapshot_sha256'] + '.json')
                else:
                    target = Path(directory) / 'events-0.jsonl'
                target.write_bytes(b'tampered')
                second, p2, _ = fixtures()
                with self.assertRaises(CheckpointIntegrityError):
                    second.run(UserQuery('NF4', 'tamper'), directory)
                self.assertEqual(p2.calls, [])

    def test_task_identity_or_limits_change_block_even_terminal(self):
        for change in ('task', 'goal', 'limits'):
            with self.subTest(change=change), tempfile.TemporaryDirectory(dir=ROOT) as directory:
                first, _, _ = fixtures()
                first.run(UserQuery('NF4', 'identity'), directory)
                second, p2, _ = fixtures(limits=ResearchLimits(max_total_tokens=25000) if change == 'limits' else ResearchLimits())
                query = UserQuery('changed' if change == 'goal' else 'NF4', 'changed' if change == 'task' else 'identity')
                with self.assertRaises(CheckpointIntegrityError):
                    second.run(query, directory)
                self.assertEqual(p2.calls, [])

    def test_terminal_artifact_tampering_blocks_without_resampling(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            first, _, _ = fixtures()
            result = first.run(UserQuery('NF4', 'terminal-tamper'), directory)
            Path(result['trace']['event_log_path']).write_bytes(b'changed')
            second, provider, _ = fixtures()
            with self.assertRaisesRegex(CheckpointIntegrityError, 'terminal_event_journal_SHA'):
                second.run(UserQuery('NF4', 'terminal-tamper'), directory)
            self.assertEqual(provider.calls, [])

    def test_budget_and_deadline_survive_restart(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            clock = Clock()
            first, _, _ = fixtures(clock=clock, wall_clock=clock)
            def fault(boundary, state):
                if boundary == 'model_receipt:r0/plan':
                    raise Interrupt()
            with self.assertRaises(Interrupt):
                first.run(UserQuery('NF4', 'deadline'), directory, fault)
            clock.now += 121
            second, p2, _ = fixtures(clock=clock, wall_clock=clock)
            result = second.run(UserQuery('NF4', 'deadline'), directory)
            self.assertEqual(result['answer']['status'], 'failed')
            self.assertIn('run_deadline_exceeded', result['answer']['error'])
            self.assertEqual(result['trace']['budget']['model_calls'], 1)
            self.assertGreater(result['trace']['budget']['reserved_tokens'], 0)
            self.assertEqual(p2.calls, [])

    def test_clock_rollback_blocks(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            clock = Clock()
            first, _, _ = fixtures(clock=clock, wall_clock=clock)
            def fault(boundary, state):
                if boundary == 'initialized':
                    raise Interrupt()
            with self.assertRaises(Interrupt):
                first.run(UserQuery('NF4', 'clock'), directory, fault)
            clock.now -= 1
            with self.assertRaisesRegex(RecoveryBlocked, 'wall_clock_moved_backwards'):
                first.run(UserQuery('NF4', 'clock'), directory)

    def test_saved_monotonic_elapsed_is_not_replenished(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            monotonic = Clock(); wall = Clock()
            class Slow(SpyProvider):
                def complete(self, operation, payload):
                    monotonic.now += 2
                    return super().complete(operation, payload)
            first, _, _ = fixtures(provider=Slow(), clock=monotonic, wall_clock=wall)
            def fault(boundary, state):
                if boundary == 'model_receipt:r0/plan':
                    raise Interrupt()
            with self.assertRaises(Interrupt):
                first.run(UserQuery('NF4', 'elapsed'), directory, fault)
            wall.now += 119  # +2 prior work => expiry, despite differing clocks.
            second, p2, _ = fixtures(provider=Slow(), clock=monotonic, wall_clock=wall)
            result = second.run(UserQuery('NF4', 'elapsed'), directory)
            self.assertIn('run_deadline_exceeded', result['answer']['error'])
            self.assertEqual(p2.calls, [])

    def test_decided_answer_survives_expired_finish_only_restart(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            clock = Clock()
            first, p1, _ = fixtures(clock=clock, wall_clock=clock)
            def fault(boundary, state):
                if state['stage'] == 'finish' and boundary.startswith('r0/decide/'):
                    raise Interrupt()
            with self.assertRaises(Interrupt):
                first.run(UserQuery('NF4', 'decided-answer'), directory, fault)
            clock.now += 121
            second, p2, retriever = fixtures(clock=clock, wall_clock=clock)
            result = second.run(UserQuery('NF4', 'decided-answer'), directory)
            self.assertEqual(result['answer']['status'], 'completed')
            self.assertEqual(result['answer']['error'], '')
            self.assertEqual(len(p1.calls), 3)
            self.assertEqual(p2.calls, [])
            self.assertEqual(retriever.calls, 0)
            self.assertGreater(result['trace']['latency_ms'], 120000)

    def test_context_and_tool_policy_integration_and_restore(self):
        from deepresearch.runtime.context import ContextManager
        from deepresearch.runtime.reliable_tools import ToolPolicy
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            first, p1, _ = fixtures(context_manager=ContextManager(), tool_policy=ToolPolicy())
            def fault(boundary, state):
                if boundary == 'tool_receipt:r0/t0/search':
                    raise Interrupt()
            with self.assertRaises(Interrupt):
                first.run(UserQuery('NF4', 'integrated'), directory, fault)
            second, p2, retriever = fixtures(context_manager=ContextManager(), tool_policy=ToolPolicy())
            result = second.run(UserQuery('NF4', 'integrated'), directory)
            self.assertEqual(result['answer']['status'], 'completed')
            self.assertEqual(retriever.calls, 0)
            self.assertEqual(result['tool_reliability_metrics']['physical_attempts'], 2)
            self.assertEqual(result['trace']['tool_calls'], 2)
            self.assertEqual([op for op, _ in p1.calls], ['plan'])
            self.assertEqual([op for op, _ in p2.calls], ['answer', 'critic'])

    def test_bounded_trace_full_journal_and_committed_prefix(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            first, _, _ = fixtures(trace_max_events=3)
            def fault(boundary, state):
                if boundary == 'model_receipt:r0/plan':
                    raise Interrupt()
            with self.assertRaises(Interrupt):
                first.run(UserQuery('NF4', 'journal'), directory, fault)
            old = Path(directory) / 'events-0.jsonl'
            with old.open('a', encoding='utf-8') as stream:
                stream.write('{"uncommitted_fixture":true}\n')
            second, _, _ = fixtures(trace_max_events=3)
            result = second.run(UserQuery('NF4', 'journal'), directory)
            self.assertLessEqual(len(result['trace']['events']), 3)
            self.assertFalse(result['trace']['events_complete'])
            path = Path(result['trace']['event_log_path'])
            lines = path.read_text(encoding='utf-8').splitlines()
            self.assertEqual(len(lines), result['trace']['event_count'])
            self.assertNotIn('uncommitted_fixture', path.read_text(encoding='utf-8'))
            self.assertIn('uncommitted_fixture', old.read_text(encoding='utf-8'))
            self.assertEqual(sha256(path.read_bytes()).hexdigest(), result['trace']['event_log_sha256'])

    def test_verified_journal_short_read_during_restore_blocks(self):
        from unittest.mock import patch
        import deepresearch.runtime.resumable_agent as module
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            first, _, _ = fixtures()
            def fault(boundary, state):
                if boundary == 'model_receipt:r0/plan':
                    raise Interrupt()
            with self.assertRaises(Interrupt):
                first.run(UserQuery('NF4', 'short-copy'), directory, fault)
            real_hash = module._file_prefix_hash
            old = Path(directory) / 'events-0.jsonl'
            def shorten_after_verification(path, length):
                result = real_hash(path, length)
                if Path(path) == old:
                    old.write_bytes(b'')
                return result
            second, p2, _ = fixtures()
            with patch.object(module, '_file_prefix_hash', side_effect=shorten_after_verification):
                with self.assertRaisesRegex(CheckpointIntegrityError, 'shortened_during_restore_copy'):
                    second.run(UserQuery('NF4', 'short-copy'), directory)
            self.assertEqual(p2.calls, [])

    def test_checkpoint_and_journal_sensitive_fields_redacted(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=ROOT) as directory, patch.dict(os.environ, {'TEST_API_KEY': 'fixture-key-never-persist'}):
            agent, _, _ = fixtures(planner=False, critic=False, replan=False)
            agent.run(UserQuery('fixture-key-never-persist', 'redaction'), directory)
            for path in Path(directory).rglob('*'):
                if path.is_file():
                    self.assertNotIn(b'fixture-key-never-persist', path.read_bytes())


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        directory, log, boundary = sys.argv[2:5]
        agent, _, _ = fixtures(log=log)
        def fault(name, state):
            if name == boundary:
                os._exit(37)  # Only this owned synthetic child exits.
        result = agent.run(UserQuery('NF4', 'owned-child-restart'), directory, fault)
        (Path(directory) / 'child_result.json').write_text(json.dumps(result), encoding='utf-8')
    else:
        unittest.main()
