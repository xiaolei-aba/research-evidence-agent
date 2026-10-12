"""V1 algorithm as explicit durable stages; completed observations never resample.

The source Agent, budget, provider, prompt and scorer remain untouched. This
engine changes the execution harness. Context/tool policy changes are optional
and independently fingerprinted. Uncertain dispatched calls fail closed.
"""
from contextlib import nullcontext
from dataclasses import asdict, fields, is_dataclass
from hashlib import sha256
import json
from pathlib import Path
import time

from deepresearch.agent.research import ResearchEvidenceAgent
from deepresearch.agent.budget import RunBudget, ResearchBudgetExceeded
from deepresearch.agent.planner import parse_plan
from deepresearch.critic.feedback import parse_feedback
from deepresearch.memory.store import EvidenceStore
from deepresearch.models import (AgentTrace, TraceEvent, SubTask, ResearchPlan,
    ToolRequest, ToolResult, RetrievedDocument, EvidenceItem, Citation,
    FinalAnswer, CriticFeedback, uid)
from deepresearch.tools.executor import ToolExecutor
from deepresearch.utils.redaction import redact, environment_secrets
from deepresearch.runtime.state import (AgentState, CheckpointStore,
    CheckpointIntegrityError, RecoveryBlocked, identity_hash)


class RecordedOperationError(RuntimeError):
    pass


class TraceJournal(list):
    """Bounded tail; durable complete JSONL is separate from the result tail."""
    def __init__(self, values=(), *, path=None, max_events=64, count=0):
        super().__init__(values)
        self.path = Path(path) if path is not None else None
        self.max_events = max_events
        self.total_count = count

    def append(self, event):
        if self.path is not None:
            with self.path.open('ab') as stream:
                stream.write((json.dumps(asdict(event), ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8'))
                stream.flush(); __import__('os').fsync(stream.fileno())
        self.total_count += 1
        super().append(event)
        if len(self) > self.max_events:
            del self[:len(self) - self.max_events]


def _file_prefix_hash(path, length):
    h = sha256()
    with Path(path).open('rb') as stream:
        remaining = length
        while remaining:
            block = stream.read(min(1048576, remaining))
            if not block:
                raise CheckpointIntegrityError('checkpoint_event_journal_shortened')
            h.update(block); remaining -= len(block)
    return h.hexdigest()


def _configuration(instance):
    if instance is None:
        return None
    if is_dataclass(instance):
        config = asdict(instance)
    else:
        config = getattr(instance, 'configuration', None)
        if callable(config):
            config = config()
        if config is None:
            config = {k: v for k, v in vars(instance).items()
                      if isinstance(v, (str, int, float, bool, type(None), list, dict))
                      and not any(word in k.lower() for word in ('secret', 'password', 'api_key'))}
    # Validation catches non-JSON configuration instead of guessing identity.
    identity_hash(config)
    return {'class': type(instance).__module__ + '.' + type(instance).__qualname__, 'configuration': config}


class StatefulResearchAgent(ResearchEvidenceAgent):
    def __init__(self, *args, context_manager=None, tool_policy=None,
                 trace_max_events=64, wall_clock=time.time, **kwargs):
        super().__init__(*args, **kwargs)
        if type(trace_max_events) is not int or trace_max_events < 1:
            raise ValueError('invalid_trace_tail_bound')
        self.context_manager = context_manager
        self.tool_policy = tool_policy
        self.trace_max_events = trace_max_events
        self.wall_clock = wall_clock

    def _identity(self, query):
        source_hashes = {}
        for name in ('state.py', 'resumable_agent.py'):
            path = Path(__file__).with_name(name)
            source_hashes[name] = sha256(path.read_bytes()).hexdigest()
        for instance in (self.context_manager, self.tool_policy):
            if instance is not None:
                module = __import__('sys').modules[type(instance).__module__]
                if getattr(module, '__file__', None):
                    source_hashes[type(instance).__module__] = sha256(Path(module.__file__).read_bytes()).hexdigest()
        return identity_hash({'query_id': query.query_id, 'goal': query.text,
            'versions': self.versions, 'limits': asdict(self.limits), 'flags': self.flags,
            'selector': _configuration(self.selector), 'context_manager': _configuration(self.context_manager),
            'tool_policy': _configuration(self.tool_policy), 'engine_sources': source_hashes,
            'provider_configuration': _configuration(getattr(self.provider, 'config', None)),
            'output_validator': self.output_validator.__module__ + '.' + self.output_validator.__qualname__,
            'trace_max_events': self.trace_max_events})

    def run(self, query, checkpoint_dir=None, fault_hook=None):
        checkpoint = CheckpointStore(checkpoint_dir) if checkpoint_dir is not None else None
        with checkpoint.lease() if checkpoint else nullcontext():
            return self._run(query, checkpoint, fault_hook)

    def _run(self, query, checkpoint, fault_hook):
        fingerprint = self._identity(query)
        safe = getattr(self.provider, 'redact', lambda value: redact(value, environment_secrets()))
        state = checkpoint.load(fingerprint, query.query_id, safe(query.text)) if checkpoint else None
        if state is not None and state.terminal_result is not None:
            # All failed outcomes are completed observations too.
            journal = state.execution.get('event_journal')
            if journal:
                path = Path(journal['path']).resolve()
                if not path.is_relative_to(checkpoint.directory):
                    raise CheckpointIntegrityError('event_journal_outside_checkpoint')
                if path.stat().st_size != journal['bytes'] or _file_prefix_hash(path, journal['bytes']) != journal['sha256']:
                    raise CheckpointIntegrityError('terminal_event_journal_SHA_mismatch')
            return state.terminal_result
        if state is not None and state.pending_intent is not None:
            raise RecoveryBlocked('ambiguous_inflight_call:' + state.pending_intent['kind'])
        now = self.wall_clock()
        restoring = state is not None
        if restoring and now < state.deadline['last_wall_time'] - .001:
            raise RecoveryBlocked('wall_clock_moved_backwards_cannot_restore_deadline')
        if not restoring:
            state = AgentState(query.query_id, safe(query.text), fingerprint)
            state.deadline = {'start_wall_time': now, 'absolute_wall_time': now + self.limits.max_seconds,
                              'last_wall_time': now, 'elapsed_seconds': 0,
                              'scope': 'same original task lifetime including restart downtime'}
            state.execution = {'plans': [], 'feedbacks': [], 'retrieval_ids': [], 'selected_ids': [],
                'missing': [], 'report': {}, 'candidate': None, 'answer': None, 'scratch': {},
                'trace_tail': [], 'recovery_count': 0, 'receipt_reuse_count': 0, 'tool_cache': {},
                'cache_hits': 0, 'cache_lookups': 0, 'event_journal': None, 'tool_metrics': None}
        else:
            state.execution['recovery_count'] += 1
            state.execution['completed_steps_before_failure'] = list(state.completed_steps)
            state.execution['retained_steps_after_restore'] = list(state.completed_steps)
            if state.current_plan is not None:
                # JSON has no shared-object aliases. Restore the original
                # current-plan/plans[-1] alias so task status updates persist.
                state.current_plan = state.execution['plans'][-1]

        budget = RunBudget(self.limits, self.clock)
        if restoring:
            elapsed = max(state.deadline['elapsed_seconds'] + max(0, now - state.deadline['last_wall_time']),
                          now - state.deadline['start_wall_time'])
            budget.start = self.clock() - elapsed
            budget.reserved_tokens = state.budget['reserved_tokens']
            budget.model_calls = state.budget['model_calls']
        trace = self.trace_factory(query.query_id, versions=self.versions.copy())
        for name, value in state.cost.items():
            if name in {f.name for f in fields(AgentTrace)} and name not in {'events', 'versions'}:
                setattr(trace, name, value)
        journal_path = None
        if checkpoint:
            previous = state.execution.get('event_journal')
            journal_path = checkpoint.directory / ('events-' + str(state.execution['recovery_count']) + '.jsonl')
            if previous:
                old = Path(previous['path']).resolve()
                if not old.is_relative_to(checkpoint.directory):
                    raise CheckpointIntegrityError('event_journal_outside_checkpoint')
                if _file_prefix_hash(old, previous['bytes']) != previous['sha256']:
                    raise CheckpointIntegrityError('checkpoint_event_journal_SHA_mismatch')
                # Preserve any uncommitted suffix in the historical file; only
                # the verified committed prefix participates in restored trace.
                if journal_path == old:
                    raise CheckpointIntegrityError('resume_event_segment_collision')
                with old.open('rb') as source, journal_path.open('xb') as target:
                    remaining = previous['bytes']
                    while remaining:
                        block = source.read(min(1048576, remaining))
                        if not block:
                            raise CheckpointIntegrityError('event_journal_shortened_during_restore_copy')
                        target.write(block); remaining -= len(block)
                    target.flush(); __import__('os').fsync(target.fileno())
                if _file_prefix_hash(journal_path, previous['bytes']) != previous['sha256']:
                    raise CheckpointIntegrityError('event_journal_changed_during_restore_copy')
            else:
                journal_path.touch(exist_ok=False)
        trace.events = TraceJournal([TraceEvent(**e) for e in state.execution['trace_tail']],
            path=journal_path, max_events=self.trace_max_events,
            count=state.execution.get('event_count', 0))
        store = EvidenceStore(self.flags['deduplicate'])
        stored_items = state.memory_state.get('items', {})
        item_order = state.memory_state.get('item_order', [])
        if len(item_order) != len(set(item_order)) or set(item_order) != set(stored_items):
            raise CheckpointIntegrityError('checkpoint_evidence_order_identity_mismatch')
        store.items = {k: EvidenceItem(**stored_items[k]) for k in item_order}
        store.keys = dict(state.memory_state.get('keys', {}))
        if self.tool_policy is None:
            tools = ToolExecutor(self.retriever, self.reader, trace, self.limits.max_tool_calls,
                                 self.flags['cache'], redactor=safe)
        else:
            tools = self.tool_policy.create_executor(self.retriever, self.reader, trace,
                self.limits.max_tool_calls, self.flags['cache'], redactor=safe)
        tools.deadline_remaining = lambda: max(0, self.limits.max_seconds - (self.clock() - budget.start))
        tools.entries = {tuple(json.loads(k)): [RetrievedDocument(**d) for d in docs]
                         for k, docs in state.execution['tool_cache'].items()}
        tools.hits = state.execution['cache_hits']; tools.lookups = state.execution['cache_lookups']
        if state.execution.get('tool_metrics') is not None and hasattr(tools, 'metrics'):
            tools.metrics = dict(state.execution['tool_metrics'])

        def event(kind, status, payload, error='', elapsed=0):
            trace.events.append(TraceEvent(kind, status, elapsed, safe(payload), safe(error)))

        def commit(boundary, completed=False):
            state.memory_state = {'items': {k: asdict(v) for k, v in store.items.items()},
                                  'item_order': list(store.items), 'keys': dict(store.keys)}
            state.budget = budget.snapshot()
            state.deadline['last_wall_time'] = self.wall_clock()
            state.deadline['elapsed_seconds'] = max(state.deadline['elapsed_seconds'], self.clock() - budget.start)
            state.cost = {f.name: getattr(trace, f.name) for f in fields(AgentTrace)
                          if f.name not in {'events', 'versions', 'budget'}}
            state.execution['trace_tail'] = [asdict(e) for e in trace.events]
            state.execution['event_count'] = trace.events.total_count
            state.execution['tool_cache'] = {json.dumps(k): [asdict(d) for d in docs] for k, docs in tools.entries.items()}
            state.execution['cache_hits'] = tools.hits; state.execution['cache_lookups'] = tools.lookups
            if hasattr(tools, 'metrics'):
                state.execution['tool_metrics'] = dict(tools.metrics)
            if journal_path:
                length = journal_path.stat().st_size
                state.execution['event_journal'] = {'path': str(journal_path), 'bytes': length,
                                                     'sha256': _file_prefix_hash(journal_path, length)}
            if completed:
                state.completed_steps.append(boundary)
            if checkpoint:
                checkpoint.save(state, safe)
            if fault_hook:
                fault_hook(boundary, asdict(state))

        def reuse(key, kind, request_hash):
            receipt = state.receipts.get(key)
            if receipt is None:
                return None
            if receipt['kind'] != kind or receipt['request_hash'] != request_hash:
                raise CheckpointIntegrityError('receipt_request_identity_mismatch')
            state.execution['receipt_reuse_count'] += 1
            if receipt.get('error'):
                raise RecordedOperationError(receipt['error'])
            return receipt

        def call(key, operation, payload):
            request_hash = identity_hash({'operation': operation, 'input': safe(payload)})
            receipt = reuse(key, 'model', request_hash)
            if receipt is not None:
                return receipt['output']
            actual_payload = payload
            if self.context_manager is not None:
                actual_payload, decision = self.context_manager.prepare(operation, payload, self.counter, self.limits.max_input_tokens)
                event('context_decision', decision.get('status', 'selected'), decision)
                if decision.get('missing_pin_ids'):
                    raise ValueError('context_missing_pinned_evidence')
                if decision.get('status') in {'empty_after_selection', 'invalid_pin_reference'}:
                    raise ValueError('context_' + decision['status'])
            attempts = getattr(self.provider, 'attempts', None)
            cursor = len(attempts) if attempts is not None else 0
            retries = getattr(getattr(self.provider, 'config', None), 'max_retries', 0) + 1
            counted = self.counter.count_request(operation, actual_payload)
            reservation = {'operation': operation, 'native_input_tokens': counted,
                'counter_is_exact': self.counter.is_exact, 'attempts_reserved': retries,
                'output_cap_per_attempt': self.limits.max_output_tokens,
                'requested_reservation_tokens': (counted + self.limits.max_output_tokens) * retries,
                'prior_reserved_tokens': budget.reserved_tokens, 'limits': asdict(self.limits)}
            event('token_budget', 'requested', reservation)
            try:
                budget.reserve(counted, retries)
            except Exception as exc:
                event('token_budget', 'rejected', dict(reservation, error_stage=operation), str(exc)); raise
            event('token_budget', 'accepted', dict(reservation, reserved_tokens=budget.reserved_tokens))
            logical = uid(); start = self.clock(); trace.model_calls += 1
            state.pending_intent = {'key': key, 'kind': 'model', 'operation': operation, 'request_hash': request_hash,
                                    'assembled_payload_sha256': identity_hash(safe(actual_payload)), 'logical_call_id': logical}
            commit('model_intent:' + key)
            output = None; error = None
            try:
                raw = self.provider.complete(operation, safe(actual_payload))
                output = self.output_validator(operation, raw)
                if self.context_manager is not None and operation == 'answer':
                    allowed = {e['evidence_id'] for e in actual_payload.get('evidence', [])}
                    if any(c['evidence_id'] not in allowed for c in output['citations']):
                        raise ValueError('citation_not_in_assembled_input')
                event('llm:' + operation, 'ok', {'logical_call_id': logical, 'input': actual_payload, 'output': output},
                      elapsed=(self.clock() - start) * 1000)
            except Exception as exc:
                error = exc
                event('llm:' + operation, 'error', {'logical_call_id': logical, 'input': actual_payload}, str(exc), (self.clock() - start) * 1000)
            finally:
                if attempts is not None:
                    for attempt in attempts[cursor:]:
                        event('llm_attempt', attempt['status'], dict(attempt, logical_call_id=logical), attempt.get('error', ''), attempt['elapsed_ms'])
                        if attempt.get('sent'):
                            trace.http_attempts += 1
                            usage = attempt.get('usage')
                            if usage is None:
                                trace.usage_unreported_attempts += 1
                            else:
                                trace.prompt_tokens_reported += usage['prompt_tokens']
                                trace.completion_tokens_reported += usage['completion_tokens']
                                trace.token_usage_reported += usage['total_tokens']
                try:
                    budget.check()
                    if trace.token_usage_reported > self.limits.max_total_tokens:
                        raise ResearchBudgetExceeded('reported_token_budget_exceeded')
                except Exception as exc:
                    error = exc
            state.receipts[key] = {'kind': 'model', 'operation': operation, 'request_hash': request_hash,
                                  'output': output, 'error': safe(str(error)) if error else None,
                                  'assembled_payload_sha256': identity_hash(safe(actual_payload))}
            state.pending_intent = None
            commit('model_receipt:' + key)
            if error:
                raise error
            return output

        def tool(key, request):
            request_hash = identity_hash({'task_id': request.task_id, 'tool': request.tool, 'argument': request.argument})
            receipt = reuse(key, 'tool', request_hash)
            if receipt is not None:
                value = receipt['output']
                return ToolResult(value['request_id'], value['status'], [RetrievedDocument(**d) for d in value['documents']], value['error'])
            budget.check()
            state.pending_intent = {'key': key, 'kind': 'tool', 'request_hash': request_hash, 'request': safe(asdict(request))}
            commit('tool_intent:' + key)
            result = None; error = None
            try:
                result = tools.call(request)
                budget.check()
            except Exception as exc:
                error = exc
            state.receipts[key] = {'kind': 'tool', 'request_hash': request_hash, 'output': asdict(result) if result else None,
                                  'error': safe(str(error)) if error else None}
            state.tool_history.append({'key': key, 'request': safe(asdict(request)), 'status': result.status if result else 'error',
                                       'error': safe(str(error)) if error else result.error})
            state.pending_intent = None
            commit('tool_receipt:' + key)
            if error:
                raise error
            return result

        def public_evidence():
            return [{'evidence_id': e.evidence_id, 'document_id': e.document_id, 'source': e.source,
                     'source_version': e.source_version, 'section_id': e.section_id, 'chunk_id': e.chunk_id,
                     'locator': e.locator, 'content': e.content} for e in store.items.values()]

        X = state.execution
        if not restoring:
            commit('initialized')
        try:
            if restoring and state.stage != 'finish':
                budget.check()  # Expiry becomes terminal; no physical call occurs.
            while state.stage != 'finish':
                r = state.cursor['round']; i = state.cursor['subtask']; stage = state.stage
                prefix = 'r' + str(r)
                if stage == 'start':
                    event('agent', 'started', {'configuration': self.flags, 'versions': self.versions, 'limits': asdict(self.limits)})
                    if not isinstance(query.text, str) or not query.text.strip() or len(query.text) > 2048:
                        raise ValueError('invalid_query')
                    state.stage = 'plan'
                elif stage == 'plan':
                    budget.check()
                    if self.flags['planner']:
                        plan = parse_plan(call(prefix + '/plan', 'plan' if r == 0 else 'replan',
                            {'query': query.text, 'missing_queries': X['missing'], 'evidence': public_evidence()}), query.query_id, r)
                    else:
                        plan = ResearchPlan(query.query_id, [SubTask(f'{r}:0', query.text)])
                    if len(plan.tasks) > self.limits.max_subtasks:
                        raise ResearchBudgetExceeded('subtask_budget_exceeded')
                    state.current_plan = asdict(plan); X['plans'].append(state.current_plan); X['missing'] = []
                    if not self.flags['memory']:
                        store = EvidenceStore(self.flags['deduplicate'])
                    state.cursor['subtask'] = 0; state.stage = 'task'
                elif stage == 'task':
                    if i >= len(state.current_plan['tasks']):
                        X['candidate'] = None; state.stage = 'answer'
                    else:
                        task = state.current_plan['tasks'][i]; task['status'] = 'running'
                        event('task', 'running', task)
                        X['scratch'] = {'docs': [], 'hits': []}; state.cursor['hit'] = 0
                        state.stage = 'search' if self.flags['retrieval'] else 'select'
                elif stage == 'search':
                    task = state.current_plan['tasks'][i]
                    result = tool(prefix + '/t' + str(i) + '/search', ToolRequest(task['task_id'], 'search', task['query']))
                    X['retrieval_ids'].extend(d.chunk_id or d.document_id for d in result.documents)
                    if result.status == 'error':
                        task['error'] = result.error
                    X['scratch']['hits'] = [asdict(d) for d in result.documents]
                    state.stage = 'read' if self.flags['reading'] else 'select'
                elif stage == 'read':
                    h = state.cursor['hit']; task = state.current_plan['tasks'][i]
                    if h >= len(X['scratch']['hits']):
                        state.stage = 'select'
                    else:
                        hit = X['scratch']['hits'][h]
                        result = tool(prefix + '/t' + str(i) + '/read' + str(h),
                                      ToolRequest(task['task_id'], 'read', hit['chunk_id'] or hit['document_id']))
                        if result.status == 'ok':
                            for document in result.documents:
                                document.retrieval_score = hit['retrieval_score']; X['scratch']['docs'].append(asdict(document))
                        else:
                            task['error'] = result.error
                        state.cursor['hit'] += 1
                elif stage == 'select':
                    task = state.current_plan['tasks'][i]
                    chosen, selection = self.selector.select([RetrievedDocument(**d) for d in X['scratch']['docs']])
                    event('selection', 'ok', dict(selection, task_id=task['task_id']))
                    for doc in chosen:
                        item = store.add(doc, task['task_id']); X['selected_ids'].append(doc.chunk_id or doc.document_id)
                        event('evidence', 'stored', asdict(item))
                    task['status'] = 'completed' if chosen else 'failed'
                    if not chosen:
                        X['missing'].append(task['query'])
                    event('task', task['status'], task, task['error'])
                    state.cursor['subtask'] += 1; state.stage = 'task'
                elif stage == 'answer':
                    if store.items:
                        parsed = call(prefix + '/answer', 'answer', {'query': query.text, 'evidence': public_evidence()})
                        citations = [Citation(**c) for c in parsed['citations']]
                        X['candidate'] = asdict(FinalAnswer(query.query_id, parsed['text'], citations, 'completed'))
                        X['report'] = self.verifier.verify(citations, store)
                        event('citation_verifier', 'checked', X['report'])
                    else:
                        X['report'] = {'structural_valid': False, 'findings': [], 'semantic_support_complete': False}
                    state.stage = 'critic'
                elif stage == 'critic':
                    candidate = X['candidate']; report = X['report']
                    critic_input = {'query': query.text, 'evidence': public_evidence(), 'missing_queries': X['missing'],
                        'conflicts': store.conflicts(), 'answer': {'text': candidate['text'],
                          'citations': [{'evidence_id': c['evidence_id'], 'claim': c['claim']} for c in candidate['citations']]} if candidate else None,
                        'citation_report': {'structural_valid': report['structural_valid'],
                          'findings': [{k: f[k] for k in ('evidence_id', 'structural_valid', 'support', 'error')} for f in report['findings']]}}
                    if self.flags['critic']:
                        feedback = parse_feedback(call(prefix + '/critic', 'critic', critic_input))
                    else:
                        feedback = CriticFeedback(candidate is not None and not X['missing'], 'Model citation critic disabled', X['missing'], store.conflicts())
                        event('critic', 'disabled', asdict(feedback))
                    X['feedbacks'].append(asdict(feedback)); state.stage = 'decide'
                elif stage == 'decide':
                    candidate = X['candidate']; feedback = X['feedbacks'][-1]
                    rejected = any(f.get('support') is False for f in X['report']['findings'])
                    if candidate and feedback['sufficient'] and not X['missing'] and not store.conflicts() and X['report']['structural_valid'] and not rejected:
                        X['answer'] = candidate; state.stage = 'finish'
                    else:
                        X['missing'] = feedback['missing_queries'] or X['missing'] or [query.text]
                        rounds = (self.limits.max_replans if self.flags['replan'] else 0) + 1
                        event('replan', 'requested' if r + 1 < rounds else 'limit_reached', {'missing_queries': X['missing'], 'round': r})
                        state.cursor['round'] += 1
                        if state.cursor['round'] < rounds:
                            state.stage = 'plan'
                        else:
                            X['answer'] = asdict(FinalAnswer(query.query_id, 'Insufficient verified evidence within the research budget.', [],
                                              'insufficient_evidence', 'Unresolved evidence or citation issues'))
                            state.stage = 'finish'
                else:
                    raise CheckpointIntegrityError('unknown_agent_state_stage')
                commit(prefix + '/' + stage + '/t' + str(i) + '/h' + str(state.cursor['hit']), completed=True)
        except (CheckpointIntegrityError, RecoveryBlocked):
            raise
        except Exception as exc:
            for plan in X['plans']:
                for task in plan['tasks']:
                    if task['status'] == 'running':
                        task['status'] = 'failed'; task['error'] = safe(str(exc)); event('task', 'failed', task, str(exc))
            X['answer'] = asdict(FinalAnswer(query.query_id, '', [], 'failed', safe(str(exc))))
            state.stage = 'finish'; event('agent', 'error', {}, str(exc))
        trace.token_usage_complete = bool(trace.http_attempts) and trace.usage_unreported_attempts == 0
        trace.token_usage = trace.token_usage_reported if trace.token_usage_complete else None
        trace.latency_ms = (self.clock() - budget.start) * 1000
        trace.budget = budget.snapshot()
        event('agent', X['answer']['status'], {'answer': X['answer'], 'versions': self.versions, 'budget': trace.budget}, elapsed=trace.latency_ms)
        result = safe({'answer': X['answer'], 'trace': asdict(trace), 'plans': X['plans'],
            'feedback': X['feedbacks'], 'evidence': [asdict(e) for e in store.items.values()],
            'citation_report': X['report'], 'retrieved_chunk_ids': X['retrieval_ids'], 'selected_chunk_ids': X['selected_ids'],
            'cache': {'hits': tools.hits, 'lookups': tools.lookups}, 'quality_status': 'not_human_evaluated'})
        result['trace'].update(events_complete=trace.events.total_count <= self.trace_max_events,
                               event_count=trace.events.total_count, event_log_path=str(journal_path) if journal_path else None)
        if journal_path:
            result['trace']['event_log_sha256'] = _file_prefix_hash(journal_path, journal_path.stat().st_size)
        result['runtime_state'] = {'fingerprint': fingerprint, 'checkpoint_hash': state.last_checkpoint,
            'checkpoint_hash_kind': 'last_step_before_terminal_commit; HEAD.json pins terminal snapshot',
            'completed_steps': list(state.completed_steps), 'recovery_count': X['recovery_count'],
            'receipt_reuse_count': X['receipt_reuse_count'], 'physical_replay_model_calls': 0, 'physical_replay_tool_calls': 0,
            'completed_steps_before_failure': X.get('completed_steps_before_failure'),
            'retained_steps_after_restore': X.get('retained_steps_after_restore'),
            'lost_steps': 0 if restoring else None, 'recovery_eligible': restoring,
            'restored': restoring, 'engineering_only': bool(getattr(self.provider, 'is_mock', False))}
        if hasattr(tools, 'metrics'):
            result['tool_reliability_metrics'] = dict(tools.metrics)
        state.terminal_result = result
        commit('terminal')
        return result
