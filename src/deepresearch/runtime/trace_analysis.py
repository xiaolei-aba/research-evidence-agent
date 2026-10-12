"""Streaming diagnostic metrics, with explicit evidence and denominator boundaries.

This module never calls a model, repairs predictions, or grades semantic support.
It accepts legacy results and the stateful engine's persisted result shape.
"""
from collections import Counter, defaultdict
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re

from deepresearch.utils.redaction import redact, environment_secrets

SCHEMA = 'runtime-trace-analysis-v1'
SENSITIVE = re.compile(r'authorization|credential|password|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|cookie', re.I)


def safe_value(value, secrets=None):
    known = tuple(environment_secrets()) + tuple(secrets or ())
    known += tuple(v for k, v in os.environ.items() if SENSITIVE.search(k) and v)
    def walk(obj):
        if isinstance(obj, dict):
            return {redact(str(k), known): '[REDACTED]' if SENSITIVE.search(str(k)) else walk(v)
                    for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [walk(v) for v in obj]
        cleaned = redact(obj, known)
        if isinstance(cleaned, str):
            cleaned = re.sub(r'(?i)(authorization|credential|cookie|private[_-]?key)\s*[=:]\s*[^\n,;]+',
                             r'\1=[REDACTED]', cleaned)
        return cleaned
    return walk(value)


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _symptom(error, finish_reason=None):
    value = str(error or '').lower()
    if not value:
        return 'NoTerminalError'
    if 'ambiguous_inflight' in value:
        return 'AmbiguousInflightRecovery'
    if 'checkpoint' in value or 'receipt_sha' in value or 'integrity' in value:
        return 'CheckpointIntegrityFailure'
    if any(term in value for term in ('hardware_guard', 'resource_guard', 'out_of_memory', 'ram_below', 'foreign_gpu')):
        return 'ResourceFailure'
    if 'deadline' in value or 'timeout' in value:
        return 'Timeout'
    if 'token_budget' in value or 'token_reservation' in value or 'context_overflow' in value:
        return 'ContextBudgetFailure'
    if 'incomplete_or_refused_completion' in value:
        return 'GenerationLengthFailure' if finish_reason == 'length' else 'GenerationFailureReasonUnknown'
    if any(term in value for term in ('empty_list', 'invalid_json', 'invalid_output', 'schema', 'unknown_evidence', 'unresolved evidence', 'citation')):
        return 'ValidationOrAcceptanceFailure'
    if 'tool' in value:
        return 'ToolFailure'
    if 'transport' in value or 'connection' in value:
        return 'UnknownRuntimeTransportFailure'
    return 'UnknownFailure'


def analyze_trace(record, source_ref=None, *, event_iterable=None, secrets=None, run_kind=None):
    """Summarize one result, without retaining full payloads or inferring correctness.

    event_iterable supplies the complete disk log for a bounded-tail Trace. Without
    it, omitted events make tool/retry coverage partial. Raw call evidence must
    come from SHA-checked source receipts and contain only diagnostic metadata.
    """
    run = record.get('run', record)
    trace = run.get('trace') or {}
    answer = run.get('answer') or {}
    state = record.get('runtime_state', run.get('runtime_state')) or {}
    declared_complete = trace.get('events_complete', True) is True
    complete_events = event_iterable is not None or declared_complete
    events = event_iterable if event_iterable is not None else trace.get('events', [])
    task_id = record.get('task_id', answer.get('query_id', trace.get('query_id', 'unknown')))
    status = answer.get('status', record.get('status', 'unknown'))
    error = answer.get('error', record.get('error', trace.get('error', '')))
    raw_calls = record.get('raw_call_evidence', [])
    failing_answer = [c for c in raw_calls if c.get('operation') == 'answer']
    final_finish = failing_answer[-1].get('finish_reason') if failing_answer else None
    stages = []
    rejections = []
    context_decisions = []
    tool_statuses = Counter()
    tools = defaultdict(list)
    tool_summaries = []
    attempt_started = set()
    attempt_outcomes = {}
    tool_logical_statuses = Counter()
    cache_request_ids = set()
    model_attempts = defaultdict(list)
    citation_states = Counter()
    checkpoint_states = Counter()
    tool_termination = []
    event_count = 0
    for index, event in enumerate(events):
        event_count += 1
        kind = str(event.get('kind', event.get('type', 'unknown')))
        event_status = event.get('status', 'unknown')
        payload = event.get('payload') or {}
        event_error = event.get('error', '')
        operation = payload.get('operation', event.get('stage'))
        if not operation and kind.startswith('llm:'):
            operation = kind.partition(':')[2]
        if not operation and kind.startswith('tool'):
            operation = (payload.get('request') or {}).get('tool', payload.get('tool'))
        if event_error:
            stages.append({'event_index': index, 'kind': kind, 'status': event_status,
                           'stage': operation or 'unknown', 'error': event_error,
                           'symptom': _symptom(event_error, final_finish if operation == 'answer' else None),
                           'evidence_level': 'direct_observed_error'})
        if kind == 'token_budget' and event_status == 'rejected':
            rejections.append({'event_index': index, 'operation': operation or 'unknown',
                               'native_input_tokens': _count(payload.get('native_input_tokens')),
                               'requested_reservation_tokens': _count(payload.get('requested_reservation_tokens')),
                               'prior_reserved_tokens': _count(payload.get('prior_reserved_tokens')),
                               'attempts_reserved': _count(payload.get('attempts_reserved')),
                               'output_cap_per_attempt': _count(payload.get('output_cap_per_attempt')),
                               'counter_is_exact': payload.get('counter_is_exact') is True,
                               'limits': payload.get('limits', {}), 'error': event_error})
        if kind in {'context_decision', 'context_manager', 'context_selection'}:
            # Reasons/IDs and token receipts only; never echo evidence/Gold payloads.
            context_decisions.append({'event_index': index, 'status': event_status,
                                      **{k: payload[k] for k in ('operation', 'kept_ids', 'dropped_ids', 'kept', 'dropped',
                                                               'input_tokens', 'native_input_tokens', 'reasons', 'budget', 'selected_ids',
                                                               'before_tokens', 'after_tokens', 'max_input_tokens', 'count_calls', 'reason',
                                                               'pinned_ids', 'input_payload_sha256', 'output_payload_sha256') if k in payload}})
        if kind in {'tool', 'tool_attempt', 'tool_cache'}:
            request = payload.get('request') or {}
            request_id = request.get('request_id', payload.get('request_id'))
            attempt_no = payload.get('physical_attempt', payload.get('attempt'))
            if event_status == 'timeout' or payload.get('in_flight'):
                tool_termination.append({'event_index': index, 'request_id': request_id, 'attempt': attempt_no,
                                         'status': event_status, 'in_flight': payload.get('in_flight'),
                                         'underlying_execution_stopped': payload.get('underlying_execution_stopped'),
                                         'timeout_is_preemptive': payload.get('timeout_is_preemptive', False),
                                         'will_retry': payload.get('will_retry')})
            if kind == 'tool_attempt' and request_id is not None and _count(attempt_no):
                key = (request_id, attempt_no)
                if event_status == 'started':
                    attempt_started.add(key)
                elif event_status not in {'receipt_reuse', 'cache_hit', 'reused'}:
                    attempt_outcomes[key] = event_status
            elif kind == 'tool':
                tool_summaries.append({'request_id': request_id, 'status': event_status,
                                       'attempt': attempt_no})
                tool_logical_statuses[event_status] += 1
            elif kind == 'tool_cache':
                tool_statuses['cache_hit'] += 1
                cache_request_ids.add(request_id)
        if kind == 'llm_attempt':
            logical = payload.get('logical_call_id')
            if logical:
                model_attempts[logical].append(payload.get('status', event_status))
        if kind == 'citation_verifier':
            citation_states[(payload.get('structural_valid'), payload.get('semantic_support_complete'))] += 1
        if 'checkpoint' in kind or kind in {'recovery', 'receipt_reuse', 'inflight'}:
            checkpoint_states[(kind, event_status)] += 1
    usage_complete = trace.get('token_usage_complete') is True
    input_reported = _count(trace.get('prompt_tokens_reported', record.get('reported_input_tokens')))
    output_reported = _count(trace.get('completion_tokens_reported', record.get('reported_output_tokens')))
    if input_reported is None or output_reported is None:
        usage_complete = False
    recovery = record.get('recovery_metrics') or state.get('recovery_metrics') or {
        'episode_id': state.get('recovery_episode_id'), 'eligible': state.get('recovery_eligible'),
        'restored': state.get('restored'), 'completed_steps_before_failure': state.get('completed_steps_before_failure'),
        'retained_steps_after_restore': state.get('retained_steps_after_restore'),
        'replay_reported_tokens': state.get('replay_reported_tokens'),
        'replay_token_usage_complete': state.get('replay_token_usage_complete')}
    before = recovery.get('completed_steps_before_failure')
    retained = recovery.get('retained_steps_after_restore')
    lost_steps = len(set(before) - set(retained)) if (isinstance(before, list) and isinstance(retained, list)
                  and all(isinstance(item, str) for item in before + retained)) else None
    attempt_request_ids = {key[0] for key in attempt_started | set(attempt_outcomes)}
    new_tool_mode = 'tool_reliability_metrics' in run or bool(attempt_request_ids) or bool(cache_request_ids)
    for request_id, attempt_no in sorted(attempt_started, key=lambda key: (str(key[0]), key[1])):
        outcome = attempt_outcomes.get((request_id, attempt_no), 'unknown_inflight')
        tool_statuses[outcome] += 1
        tools[request_id].append({'status': outcome, 'attempt': attempt_no, 'physical': True})
    for entry in tool_summaries:
        if new_tool_mode or entry['request_id'] in cache_request_ids:
            continue  # New wrapper's logical summary is not an extra physical call.
        tool_statuses[entry['status']] += 1
        if entry['request_id']:
            tools[entry['request_id']].append({**entry, 'physical': entry['status'] not in {'cache_hit', 'receipt_reuse', 'reused'}})
    physical_tools = [[a for a in attempts if a['physical']] for attempts in tools.values()]
    tool_retries = [attempts for attempts in physical_tools if any((_count(a['attempt']) or 0) > 1 for a in attempts)]
    model_retries = [attempts for attempts in model_attempts.values() if len(attempts) > 1]
    role = record.get('role', record.get('split', 'unknown'))
    kind = run_kind or record.get('run_kind', 'unknown')
    row = {
        'schema': SCHEMA, 'task_id': task_id, 'task_hash': record.get('task_hash', record.get('sample_identity_hash')),
        'arm': record.get('group', record.get('arm', 'unknown')),
        'benchmark': record.get('benchmark', record.get('benchmark_id', 'unknown')),
        'role': role, 'run_kind': kind, 'run_id': record.get('run_id'),
        'configuration_hash': record.get('configuration_hash', record.get('configuration_fingerprint')),
        'runtime_state_fingerprint': state.get('fingerprint'),
        'backend_fingerprint': record.get('backend_execution_fingerprint', record.get('execution_fingerprint')),
        'model_sha256': (trace.get('versions') or {}).get('weight_sha256', record.get('model_sha256')),
        'source_ref': source_ref or {}, 'status': status, 'error': error,
        'terminal_symptom': _symptom(error, final_finish),
        'failure_rate_eligible': status != 'unknown', 'operational_completed': status == 'completed',
        'status_failed': status == 'failed', 'human_task_success': None,
        'semantic_error_root_cause': 'unknown',
        'quality_reported_only': record.get('quality', {}),
        'trace_id': trace.get('trace_id'), 'full_trace_complete': complete_events,
        'events_analyzed': event_count, 'declared_event_count': trace.get('event_count'),
        'stage_errors': stages, 'native_rejected_requests': rejections,
        'context_decisions': context_decisions,
        'budget_failure_stage': rejections[-1]['operation'] if rejections else 'unknown',
        'input_tokens': input_reported if usage_complete else None,
        'output_tokens': output_reported if usage_complete else None,
        'reported_input_tokens_lower_bound': input_reported,
        'reported_output_tokens_lower_bound': output_reported,
        'token_usage_complete': usage_complete,
        'model_calls': _count(trace.get('model_calls')), 'tool_calls': _count(trace.get('tool_calls')),
        'http_attempts': _count(trace.get('http_attempts')), 'latency_ms': _number(trace.get('latency_ms')),
        'observed_tool_status_counts': dict(tool_statuses),
        'observed_logical_tool_status_counts': dict(tool_logical_statuses),
        'physical_tool_attempt_starts': len(attempt_started),
        'physical_tool_outcomes_without_started': len(set(attempt_outcomes) - attempt_started),
        'tool_termination_evidence': tool_termination,
        'observed_tool_retry_episodes': len(tool_retries),
        'observed_tool_retry_successes': sum(a[-1]['status'] == 'ok' for a in tool_retries),
        'observed_model_retry_episodes': len(model_retries),
        'observed_model_retry_successes': sum(a[-1] == 'ok' for a in model_retries),
        'citation_structural_states': [{'structural_valid': k[0], 'semantic_support_complete': k[1], 'n': n}
                                       for k, n in citation_states.items()],
        'human_citation_support_rate': None,
        'checkpoint_event_counts': [{'kind': k[0], 'status': k[1], 'n': n} for k, n in checkpoint_states.items()],
        'checkpoint_hash': state.get('checkpoint_hash'),
        'completed_steps': state.get('completed_steps'),
        'recovery_count': _count(state.get('recovery_count')),
        'receipt_reuse_count': _count(state.get('receipt_reuse_count')),
        'physical_replay_model_calls': _count(state.get('physical_replay_model_calls')),
        'physical_replay_tool_calls': _count(state.get('physical_replay_tool_calls')),
        'recovery_episode_id': recovery.get('episode_id'),
        'recovery_eligible': recovery.get('eligible') if type(recovery.get('eligible')) is bool else None,
        'recovery_restored': recovery.get('restored') if type(recovery.get('restored')) is bool else None,
        'lost_completed_steps': lost_steps,
        'replay_reported_tokens': _count(recovery.get('replay_reported_tokens')),
        'replay_token_usage_complete': recovery.get('replay_token_usage_complete') is True,
        'raw_call_evidence': raw_calls,
    }
    return safe_value(row, secrets)


class TraceSummary:
    """All-observation denominator; compact numeric state, never full traces.

    Exact P95 stores up to percentile_limit latency scalars. Beyond it P95 is
    explicitly unavailable; sums/means/counts continue for the full population.
    """
    def __init__(self, percentile_limit=100000, expected_recovery_episode_ids=None):
        self.n = 0
        self.statuses = Counter()
        self.symptoms = Counter()
        self.stage_errors = Counter()
        self.run_kinds = Counter()
        self.unknown_statuses = 0
        self.incomplete_events = 0
        self.known_usage = 0
        self.tokens = Counter()
        self.measures = defaultdict(lambda: {'sum': 0, 'known_n': 0})
        self.tools = Counter()
        self.logical_tools = Counter()
        self.orphan_tool_outcomes = 0
        self.retry_n = 0
        self.retry_ok = 0
        self.recovery_n = 0
        self.recovered = 0
        self.recovery_unknown = 0
        self.lost_n = 0
        self.lost_sum = 0
        self.replay_cost = defaultdict(lambda: {'sum': 0, 'known_n': 0})
        self.recovery_episode_ids = set()
        self.expected_recovery_episode_ids = (set(expected_recovery_episode_ids)
                                               if expected_recovery_episode_ids is not None else None)
        self.latencies = []
        self.percentile_limit = percentile_limit
        self.p95_available = True

    def add(self, row):
        self.n += 1
        self.statuses[row['status']] += 1
        self.symptoms[row['terminal_symptom']] += 1
        self.run_kinds[row['run_kind']] += 1
        self.unknown_statuses += row['status'] == 'unknown'
        self.incomplete_events += not row['full_trace_complete']
        self.known_usage += row['token_usage_complete']
        for key in ('reported_input_tokens_lower_bound', 'reported_output_tokens_lower_bound'):
            if row[key] is not None:
                self.tokens[key] += row[key]
        for key in ('input_tokens', 'output_tokens', 'model_calls', 'tool_calls', 'http_attempts', 'latency_ms'):
            value = row[key]
            if value is not None:
                self.measures[key]['sum'] += value
                self.measures[key]['known_n'] += 1
        for event in row['stage_errors']:
            self.stage_errors[(event['stage'], event['symptom'])] += 1
        self.tools.update(row['observed_tool_status_counts'])
        self.logical_tools.update(row['observed_logical_tool_status_counts'])
        self.orphan_tool_outcomes += row['physical_tool_outcomes_without_started']
        self.retry_n += row['observed_tool_retry_episodes']
        self.retry_ok += row['observed_tool_retry_successes']
        episode_id = row.get('recovery_episode_id')
        if row.get('recovery_eligible') is True and episode_id is not None:
            if self.expected_recovery_episode_ids is not None and episode_id not in self.expected_recovery_episode_ids:
                raise ValueError('unregistered_recovery_episode')
            if episode_id in self.recovery_episode_ids:
                raise ValueError('duplicate_recovery_episode; do not inflate recovery denominator')
            self.recovery_episode_ids.add(episode_id)
            self.recovery_n += 1
            if row['recovery_restored'] is None:
                self.recovery_unknown += 1
            else:
                self.recovered += row['recovery_restored'] is True
            if row['lost_completed_steps'] is not None:
                self.lost_n += 1
                self.lost_sum += row['lost_completed_steps']
            for key in ('physical_replay_model_calls', 'physical_replay_tool_calls', 'replay_reported_tokens'):
                value = row[key]
                if value is not None and (key != 'replay_reported_tokens' or row['replay_token_usage_complete']):
                    self.replay_cost[key]['sum'] += value
                    self.replay_cost[key]['known_n'] += 1
        if row['latency_ms'] is not None and self.p95_available:
            if len(self.latencies) == self.percentile_limit:
                self.p95_available = False
                self.latencies.clear()
            else:
                self.latencies.append(row['latency_ms'])

    def result(self):
        physical = sum(n for status, n in self.tools.items() if status not in {'cache_hit', 'receipt_reuse', 'reused', 'rejected', 'budget_exceeded'})
        recovery_denominator = len(self.expected_recovery_episode_ids) if self.expected_recovery_episode_ids is not None else self.recovery_n
        missing_recovery = len(self.expected_recovery_episode_ids - self.recovery_episode_ids) if self.expected_recovery_episode_ids is not None else 0
        recovery_unknown = self.recovery_unknown + missing_recovery
        out = {
            'schema': SCHEMA, 'n_observations': self.n, 'statuses': dict(self.statuses),
            'terminal_symptoms': dict(self.symptoms), 'run_kind_counts': dict(self.run_kinds),
            'operational_completed_rate': self.statuses['completed'] / self.n if self.n else None,
            'failure_rate_status_failed': self.statuses['failed'] / self.n if self.n else None,
            'unknown_status_observations': self.unknown_statuses,
            'noncompleted_rate': (self.n - self.statuses['completed']) / self.n if self.n else None,
            'human_task_success_rate': None,
            'known_usage_observations': self.known_usage, 'missing_usage_observations': self.n - self.known_usage,
            'reported_token_sums_lower_bound': dict(self.tokens),
            'observed_tool_statuses': dict(self.tools), 'full_trace_missing_observations': self.incomplete_events,
            'observed_logical_tool_statuses': dict(self.logical_tools),
            'tool_attempt_outcomes_missing_started_event': self.orphan_tool_outcomes,
            'logical_tool_returned_ok_rate_full_event_coverage': self.logical_tools['ok'] / sum(self.logical_tools.values())
                if self.logical_tools and not self.incomplete_events else None,
            'observed_physical_tool_event_denominator': physical,
            'tool_returned_ok_rate_full_event_coverage': self.tools['ok'] / physical
                if physical and not self.incomplete_events and not self.orphan_tool_outcomes else None,
            'tool_retry_episode_denominator': self.retry_n,
            'tool_retry_success_rate': self.retry_ok / self.retry_n if self.retry_n and not self.incomplete_events else None,
            'human_citation_support_rate': None,
            'recovery_eligible_episode_denominator': recovery_denominator,
            'recovery_denominator_source': 'registered_fault_manifest' if self.expected_recovery_episode_ids is not None else 'observed_eligible_receipts_only',
            'recovery_observed_eligible_episodes': self.recovery_n,
            'recovery_missing_episode_results': missing_recovery,
            'recovery_unknown_outcome_episodes': recovery_unknown,
            'recovery_success_rate': self.recovered / recovery_denominator if recovery_denominator and not recovery_unknown else None,
            'recovery_success_rate_lower_bound': self.recovered / recovery_denominator if recovery_denominator else None,
            'lost_steps_known_episode_denominator': self.lost_n,
            'lost_completed_steps_mean_known_only': self.lost_sum / self.lost_n if self.lost_n else None,
            'replay_cost_eligible_episode_denominator': recovery_denominator,
            'replay_cost': {key: {'known_episode_n': value['known_n'], 'sum_known': value['sum'],
                                 'mean_all_complete': value['sum'] / recovery_denominator
                                     if recovery_denominator and value['known_n'] == recovery_denominator else None}
                            for key, value in self.replay_cost.items()},
            'stage_error_event_counts': [{'stage': k[0], 'symptom': k[1], 'n': n} for k, n in self.stage_errors.items()],
            'latency_p95_ms_known_only': sorted(self.latencies)[math.ceil(.95 * len(self.latencies)) - 1]
                if self.latencies and self.p95_available else None,
            'latency_p95_limit_exceeded': not self.p95_available,
        }
        for key, value in self.measures.items():
            out[key + '_known_n'] = value['known_n']
            out[key + '_mean_all_complete'] = value['sum'] / self.n if self.n and value['known_n'] == self.n else None
            out[key + '_mean_known_only_diagnostic'] = value['sum'] / value['known_n'] if value['known_n'] else None
        return out


def summarize_rows(rows):
    summary = TraceSummary()
    for row in rows:
        summary.add(row)
    return summary.result()


def file_sha256(path):
    h = sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def write_dashboard(rows_path, output_path, summary, *, limit=5000, secrets=None):
    """Single offline HTML, textContent rendering, compact rows and local refs.

    The row display cap never changes the full summary's denominator. Only local
    file sources are linked, so an untrusted source URL cannot execute JS.
    """
    rows = []
    shown = 0
    with Path(rows_path).open(encoding='utf-8') as handle:
        for line in handle:
            if line.strip():
                if len(rows) < limit:
                    rows.append(safe_value(json.loads(line), secrets))
                shown += 1
    payload = json.dumps({'rows': rows, 'summary': safe_value(summary, secrets), 'display_limit': limit,
                          'total_rows': shown}, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ResearchEvidence Runtime Failure Dashboard</title>
<style>body{font:15px system-ui;margin:24px;color:#203044;background:#f6f8fb}h1{font-size:24px}section{background:white;padding:18px;border-radius:8px;margin:12px 0}select,input{padding:7px;margin:5px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:8px;text-align:left;border-bottom:1px solid #dce2ea}pre{white-space:pre-wrap;overflow-wrap:anywhere}button{cursor:pointer}#detail{max-height:480px;overflow:auto}.failed{color:#9c2525}a{color:#1764b0}</style>
<h1>Agent Runtime · Failure Dashboard</h1><p>本地只读；工具返回状态≠正确率，operational completed≠人工任务正确率。CPU故障注入与真实Dev按run_kind分别统计；过滤不会替换全体分母。</p>
<section><div id="summary"></div><details><summary>完整汇总与分母</summary><pre id="summaryjson"></pre></details></section>
<section><label>Arm<select id="arm"></select></label><label>Benchmark<select id="benchmark"></select></label><label>Run kind<select id="run_kind"></select></label><label>错误阶段<select id="stage"></select></label><label>Task ID<input id="task" placeholder="包含文本"></label><label>症状<select id="symptom"></select></label><p id="visible"></p><table><thead><tr><th>Task</th><th>Arm / Benchmark</th><th>Status / Symptom</th><th>Input / Output</th><th>Latency ms</th><th>Evidence</th></tr></thead><tbody id="rows"></tbody></table></section>
<section><h2>逐题证据</h2><pre id="detail">选择一条观察查看配置、SHA、拒绝原生计数、错误阶段和恢复证据。</pre></section>
<script id="data" type="application/json">__PAYLOAD__</script><script>
'use strict';const data=JSON.parse(document.getElementById('data').textContent),rows=data.rows;
const byId=id=>document.getElementById(id);const text=(v)=>v===null||v===undefined?'unknown':String(v);
byId('summary').textContent=`全体观察 ${data.summary.n_observations}; completed ${data.summary.statuses.completed||0}; failed ${data.summary.statuses.failed||0}; 缺失usage ${data.summary.missing_usage_observations}; 显示上限 ${data.display_limit}`;
byId('summaryjson').textContent=JSON.stringify(data.summary,null,2);
for(const key of ['arm','benchmark','run_kind','symptom','stage']){let vals=key==='stage'?rows.flatMap(r=>r.stage_errors.map(e=>e.stage)):rows.map(r=>r[key==='symptom'?'terminal_symptom':key]);const sel=byId(key);for(const value of ['',...new Set(vals.filter(Boolean).sort())]){const o=document.createElement('option');o.value=value;o.textContent=value||'全部';sel.appendChild(o)}sel.addEventListener('change',render)}
byId('task').addEventListener('input',render);
function render(){let selected=rows.filter(r=>['arm','benchmark','run_kind'].every(k=>!byId(k).value||r[k]===byId(k).value)&&(!byId('symptom').value||r.terminal_symptom===byId('symptom').value)&&(!byId('stage').value||r.stage_errors.some(e=>e.stage===byId('stage').value))&&String(r.task_id).includes(byId('task').value));byId('visible').textContent=`过滤结果 ${selected.length} / 可显示 ${rows.length} / 全体 ${data.total_rows}。主汇总保持全体；条件子集不是新的评测成绩。`;const body=byId('rows');body.replaceChildren();for(const r of selected){const tr=document.createElement('tr');for(const value of [r.task_id,`${r.arm} / ${r.benchmark}`,`${r.status} / ${r.terminal_symptom}`,`${text(r.input_tokens)} / ${text(r.output_tokens)}`,text(r.latency_ms)]){const td=document.createElement('td');td.textContent=value;tr.appendChild(td)}const td=document.createElement('td'),btn=document.createElement('button');btn.textContent='证据 / SHA';btn.addEventListener('click',()=>byId('detail').textContent=JSON.stringify(r,null,2));td.appendChild(btn);const ref=r.source_ref||{};if(typeof ref.local_uri==='string'&&ref.local_uri.startsWith('file:///')){const a=document.createElement('a');a.href=ref.local_uri;a.textContent=' 本地来源';td.appendChild(a)}tr.appendChild(td);body.appendChild(tr)}}render();
</script></html>'''
    Path(output_path).write_text(page.replace('__PAYLOAD__', payload), encoding='utf-8', newline='\n')
