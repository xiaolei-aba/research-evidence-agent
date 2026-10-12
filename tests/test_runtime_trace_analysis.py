"""CPU diagnostics tests: evidence provenance, denominator, redaction and recovery."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deepresearch.runtime.trace_analysis import analyze_trace, summarize_rows, write_dashboard, file_sha256, TraceSummary


def record(status='completed', error='', complete=True, events=None):
    return {'task_id': 'task-1', 'task_hash': 'task-sha', 'group': 'B3', 'benchmark': 'qasper',
            'role': 'development', 'mock_used': True, 'configuration_hash': 'cfg-sha',
            'run': {'answer': {'status': status, 'error': error},
                    'trace': {'events': events or [], 'token_usage_complete': complete,
                              'prompt_tokens_reported': 120, 'completion_tokens_reported': 20,
                              'model_calls': 1, 'tool_calls': 1, 'http_attempts': 1, 'latency_ms': 50}}}


class TraceAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.temp_root = (root / '.local' / 'trace_analysis_test_temp').resolve()
        if not cls.temp_root.is_relative_to(root):
            raise ValueError('test_temp_outside_workspace')
        cls.temp_root.mkdir(parents=True, exist_ok=True)

    def test_failure_full_denominator_and_partial_usage(self):
        good = analyze_trace(record(), run_kind='real_dev')
        bad = analyze_trace(record('failed', 'transport_error', complete=False), run_kind='real_dev')
        insufficient = analyze_trace(record('insufficient_evidence', 'Unresolved evidence or citation issues'), run_kind='real_dev')
        summary = summarize_rows(iter((good, bad, insufficient)))
        self.assertEqual(summary['n_observations'], 3)
        self.assertEqual(summary['failure_rate_status_failed'], 1 / 3)
        self.assertEqual(summary['operational_completed_rate'], 1 / 3)
        self.assertEqual(summary['noncompleted_rate'], 2 / 3)
        self.assertIsNone(summary['input_tokens_mean_all_complete'])
        self.assertEqual(summary['reported_token_sums_lower_bound']['reported_input_tokens_lower_bound'], 360)
        self.assertIsNone(summary['human_task_success_rate'])

    def test_length_is_not_refusal_and_no_receipt_remains_unknown(self):
        value = record('failed', 'incomplete_or_refused_completion')
        self.assertEqual(analyze_trace(value)['terminal_symptom'], 'GenerationFailureReasonUnknown')
        value['raw_call_evidence'] = [{'operation': 'answer', 'finish_reason': 'length', 'sha256': 'checked-sha'}]
        self.assertEqual(analyze_trace(value)['terminal_symptom'], 'GenerationLengthFailure')

    def test_native_rejection_is_not_cumulative_consumption(self):
        event = {'kind': 'token_budget', 'status': 'rejected', 'error': 'input_token_budget_exceeded',
                 'payload': {'operation': 'critic', 'native_input_tokens': 3300, 'counter_is_exact': True,
                             'requested_reservation_tokens': 7624, 'prior_reserved_tokens': 21000,
                             'attempts_reserved': 2, 'output_cap_per_attempt': 512}}
        row = analyze_trace(record('failed', 'input_token_budget_exceeded', events=[event]))
        self.assertEqual(row['input_tokens'], 120)
        self.assertEqual(row['native_rejected_requests'][0]['native_input_tokens'], 3300)
        self.assertEqual(row['budget_failure_stage'], 'critic')
        self.assertEqual(row['stage_errors'][0]['stage'], 'critic')

    def test_bounded_tail_never_proves_full_tool_success(self):
        event = {'kind': 'tool', 'status': 'ok', 'payload': {'request': {'request_id': 'r1', 'tool': 'search'}}, 'error': ''}
        value = record(events=[event])
        value['run']['trace'].update(events_complete=False, event_count=200)
        partial = summarize_rows([analyze_trace(value)])
        self.assertEqual(partial['full_trace_missing_observations'], 1)
        self.assertIsNone(partial['tool_returned_ok_rate_full_event_coverage'])
        row = analyze_trace(value, event_iterable=iter([event]))
        self.assertEqual(summarize_rows([row])['tool_returned_ok_rate_full_event_coverage'], 1)
        self.assertIsNone(row['human_citation_support_rate'])

    def test_tool_retries_differ_from_receipt_reuse(self):
        events = [{'kind': 'tool_attempt', 'status': s, 'payload': {'request_id': 'r1', 'physical_attempt': a}, 'error': ''}
                  for a, s in ((1, 'started'), (1, 'error'), (2, 'started'), (2, 'ok'), (2, 'receipt_reuse'))]
        events.append({'kind': 'tool', 'status': 'ok', 'payload': {'request_id': 'r1'}, 'error': ''})
        # Last physical outcome, not a later reuse marker, decides retry success.
        row = analyze_trace(record(events=events))
        self.assertEqual(row['observed_tool_retry_episodes'], 1)
        self.assertEqual(row['observed_tool_retry_successes'], 1)
        self.assertEqual(summarize_rows([row])['observed_physical_tool_event_denominator'], 2)

    def test_tool_inflight_timeout_is_not_preemptive_shutdown(self):
        events = [{'kind': 'tool_attempt', 'status': s, 'payload': {'request_id': 'r1', 'physical_attempt': 1,
                                                                   'underlying_execution_stopped': None}, 'error': ''}
                  for s in ('started', 'timeout')]
        events.append({'kind': 'tool', 'status': 'error', 'payload': {'request_id': 'r1'}, 'error': 'tool_timeout'})
        row = analyze_trace(record('failed', 'tool_timeout', events=events))
        self.assertEqual(row['physical_tool_attempt_starts'], 1)
        self.assertEqual(summarize_rows([row])['observed_physical_tool_event_denominator'], 1)
        self.assertEqual(row['observed_tool_status_counts'], {'timeout': 1})

    def test_new_cache_and_validation_summary_are_not_physical_calls(self):
        value = record(events=[{'kind': 'tool_cache', 'status': 'hit', 'payload': {'request_id': 'r1'}, 'error': ''},
                               {'kind': 'tool', 'status': 'ok', 'payload': {'request_id': 'r1'}, 'error': ''},
                               {'kind': 'tool', 'status': 'error', 'payload': {'request_id': 'r2'}, 'error': 'invalid_request'}])
        value['run']['tool_reliability_metrics'] = {'physical_attempts': 0}
        result = summarize_rows([analyze_trace(value)])
        self.assertEqual(result['observed_physical_tool_event_denominator'], 0)
        self.assertEqual(result['observed_logical_tool_statuses'], {'ok': 1, 'error': 1})

    def test_recovery_denominator_and_lost_steps_not_task_resume(self):
        old = analyze_trace(record())
        self.assertIsNone(summarize_rows([old])['recovery_success_rate'])
        value = record()
        value['runtime_state'] = {'physical_replay_model_calls': 0, 'physical_replay_tool_calls': 1,
                                  'checkpoint_hash': 'cp', 'completed_steps': ['s1']}
        value['recovery_metrics'] = {'eligible': True, 'restored': True, 'episode_id': 'fault-1',
                                     'completed_steps_before_failure': ['s1', 's2'], 'retained_steps_after_restore': ['s1'],
                                     'replay_reported_tokens': 0, 'replay_token_usage_complete': True}
        row = analyze_trace(value, run_kind='engineering')
        summary = summarize_rows([row])
        self.assertEqual(summary['recovery_eligible_episode_denominator'], 1)
        self.assertEqual(summary['recovery_success_rate'], 1)
        self.assertEqual(summary['lost_completed_steps_mean_known_only'], 1)
        self.assertEqual(summary['replay_cost']['physical_replay_model_calls']['mean_all_complete'], 0)
        with self.assertRaisesRegex(ValueError, 'duplicate_recovery_episode'):
            summarize_rows([row, row])

    def test_ambiguous_inflight_and_integrity_stage_separated(self):
        self.assertEqual(analyze_trace(record('failed', 'ambiguous_inflight_call'))['terminal_symptom'], 'AmbiguousInflightRecovery')
        self.assertEqual(analyze_trace(record('failed', 'checkpoint_integrity_mismatch'))['terminal_symptom'], 'CheckpointIntegrityFailure')
        self.assertEqual(analyze_trace(record('failed', 'tool_timeout'))['terminal_symptom'], 'Timeout')

    def test_fault_manifest_missing_case_stays_in_denominator(self):
        value = record()
        value['runtime_state'] = {'recovery_episode_id': 'fault-1', 'recovery_eligible': True, 'restored': True,
                                  'completed_steps_before_failure': ['s1'], 'retained_steps_after_restore': ['s1']}
        summary = TraceSummary(expected_recovery_episode_ids=['fault-1', 'fault-2'])
        summary.add(analyze_trace(value, run_kind='engineering'))
        result = summary.result()
        self.assertEqual(result['recovery_eligible_episode_denominator'], 2)
        self.assertEqual(result['recovery_missing_episode_results'], 1)
        self.assertIsNone(result['recovery_success_rate'])
        self.assertEqual(result['recovery_success_rate_lower_bound'], .5)

    def test_env_keys_fields_authorization_and_html_xss_redacted(self):
        value = record('failed', 'credential=private-value; TESTCRED Bearer abc-secret')
        value['quality'] = {'Authorization': 'Basic private-value', 'credential': 'private-value'}
        value['task_id'] = '</script><script>alert(1)</script>TESTCRED'
        with patch.dict(os.environ, {'CUSTOM_CREDENTIAL': 'TESTCRED'}):
            row = analyze_trace(value)
            serialized = json.dumps(row)
            for secret in ('TESTCRED', 'private-value', 'abc-secret'):
                self.assertNotIn(secret, serialized)
            with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
                rows = Path(directory) / 'rows.jsonl'
                rows.write_text(json.dumps(row) + '\n', encoding='utf-8')
                html_path = Path(directory) / 'dashboard.html'
                write_dashboard(rows, html_path, summarize_rows([row]))
                text = html_path.read_text(encoding='utf-8')
                self.assertNotIn('</script><script>alert(1)</script>', text)
                self.assertNotIn('https://', text)
                self.assertIn('textContent', text)
                self.assertIn('\\u003c', text)

    def test_cli_sha_mismatch_blocks_before_summary(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/analyze_runtime_traces.py'
        spec = importlib.util.spec_from_file_location('trace_cli_test', path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
            source = Path(directory) / 'result.json'
            source.write_text(json.dumps(record()), encoding='utf-8')
            source.with_suffix('.sha256').write_text('0' * 64, encoding='ascii')
            with self.assertRaisesRegex(ValueError, 'source_sha_sidecar_mismatch'):
                cli.verified_json(source)

    def test_cli_final_path_and_escaping_root_forbidden(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/analyze_runtime_traces.py'
        spec = importlib.util.spec_from_file_location('trace_cli_paths_test', path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
            with self.assertRaisesRegex(ValueError, 'Final_Test'):
                cli.checked_path(Path(directory), 'final/result.json')
            with self.assertRaisesRegex(ValueError, 'source_outside'):
                cli.checked_path(Path(directory), '../result.json')


if __name__ == '__main__':
    unittest.main()
