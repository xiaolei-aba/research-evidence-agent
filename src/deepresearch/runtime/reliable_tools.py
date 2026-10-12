"""Bounded read-only tool reliability, separate from model HTTP retries.

The default cooperative mode runs SQLite tools on their original thread. Its
pre/post timeout checks cannot interrupt a blocking tool: the parent-owned
worker process and original task deadline remain the hard boundary. Optional
thread mode requires explicit thread safety and never retries in-flight work.
"""
from copy import deepcopy
from dataclasses import dataclass
import errno
from hashlib import sha256
import inspect
import math
import re
import threading
import time

from deepresearch.models import RetrievedDocument, ToolResult, TraceEvent
from deepresearch.tools.executor import BudgetExceeded
from deepresearch.utils.redaction import redact, environment_secrets


@dataclass(frozen=True)
class ToolPolicy:
    validate_request: bool = True
    validate_result: bool = True
    verify_provenance: bool = True
    timeout_enabled: bool = True
    timeout_seconds: float = 5.0
    timeout_mode: str = 'cooperative'
    retry_enabled: bool = False
    max_retries: int = 1
    fallback: str = 'error'

    def __post_init__(self):
        for name in ('validate_request', 'validate_result', 'verify_provenance',
                     'timeout_enabled', 'retry_enabled'):
            if type(getattr(self, name)) is not bool:
                raise ValueError('invalid_tool_policy_switch')
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 1:
            raise ValueError('tool_retry_limit_must_be_zero_or_one')
        if (type(self.timeout_seconds) not in (int, float)
                or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0):
            raise ValueError('invalid_tool_timeout')
        if self.timeout_mode not in {'cooperative', 'thread'}:
            raise ValueError('invalid_tool_timeout_mode')
        if self.fallback != 'error':
            raise ValueError('unverified_tool_fallback_forbidden')

    def create_executor(self, search, reader, trace, max_calls=12, cache=True, redactor=None):
        return ToolReliabilityLayer(search, reader, trace, max_calls, cache,
                                    redactor=redactor, policy=self)


class ToolReliabilityLayer:
    def __init__(self, search, reader, trace, max_calls=12, cache=True, redactor=None,
                 policy=None, clock=time.monotonic):
        if type(max_calls) is not int or max_calls < 0:
            raise ValueError('invalid_tool_call_budget')
        if type(cache) is not bool:
            raise ValueError('invalid_tool_cache_switch')
        self.search, self.reader, self.trace = search, reader, trace
        self.max_calls, self.cache = max_calls, cache
        self.policy = policy or ToolPolicy()
        self.redact = redactor or (lambda value: redact(value, environment_secrets()))
        self.clock = clock
        self.deadline_remaining = None
        self.entries = {}
        self.hits = self.lookups = 0
        self.metrics = {'logical_requests': 0, 'physical_attempts': 0, 'logical_success': 0,
                        'logical_failure': 0, 'retried_requests': 0, 'retry_recovered': 0,
                        'empty_search_results': 0, 'timeouts': 0, 'in_flight_timeouts': 0}
        self._pending = None

    def _event(self, kind, status, start, payload, error=''):
        self.trace.events.append(TraceEvent(kind, status, (self.clock() - start) * 1000,
                                             self.redact(payload), self.redact(error)))

    @staticmethod
    def _request_dict(request):
        return {name: getattr(request, name, None)
                for name in ('task_id', 'tool', 'argument', 'request_id')}

    def _request_error(self, request):
        # This allowlist is a safety boundary, independent of optional schema checks.
        if getattr(request, 'tool', None) not in {'search', 'read'}:
            return 'invalid_or_disallowed_tool_request'
        if not self.policy.validate_request:
            return None
        fields = self._request_dict(request)
        if any(not isinstance(fields[k], str) or not fields[k] for k in fields):
            return 'invalid_or_disallowed_tool_request'
        if not fields['argument'].strip() or len(fields['argument']) > 2048:
            return 'invalid_or_disallowed_tool_request'
        if fields['tool'] == 'read' and not re.fullmatch(r'[A-Za-z0-9_.:-]+', fields['argument']):
            return 'invalid_or_disallowed_tool_request'
        return None

    def _validate_documents(self, documents, request):
        if not isinstance(documents, list):
            raise ValueError('tool_result_documents_must_be_list')
        if request.tool == 'read' and len(documents) != 1:
            raise ValueError('tool_read_requires_one_source_unit')
        if not self.policy.validate_result and not self.policy.verify_provenance:
            return
        for document in documents:
            if not isinstance(document, RetrievedDocument):
                raise ValueError('tool_result_document_type_invalid')
            if self.policy.validate_result:
                for field in ('document_id', 'source', 'content', 'source_version'):
                    value = getattr(document, field)
                    if not isinstance(value, str) or not value:
                        raise ValueError('tool_result_document_field_invalid:' + field)
                if not isinstance(document.facts, dict):
                    raise ValueError('tool_result_facts_invalid')
                if (request.tool == 'read'
                        and (document.chunk_id or document.document_id) != request.argument):
                    raise ValueError('tool_read_source_identity_mismatch')
            if self.policy.verify_provenance:
                if any(not isinstance(getattr(document, name), str)
                       or not re.fullmatch(r'[a-f0-9]{64}', getattr(document, name))
                       for name in ('document_sha256', 'chunk_sha256')):
                    raise ValueError('tool_result_missing_source_sha')
                if not isinstance(document.source_text, str):
                    raise ValueError('tool_result_source_text_invalid')
                if sha256(document.content.encode()).hexdigest() != document.chunk_sha256:
                    raise ValueError('tool_result_chunk_sha_mismatch')
                if sha256(document.source_text.encode()).hexdigest() != document.document_sha256:
                    raise ValueError('tool_result_document_sha_mismatch')
                if (type(document.span_start) is not int or type(document.span_end) is not int
                        or not 0 <= document.span_start < document.span_end <= len(document.source_text)
                        or document.source_text[document.span_start:document.span_end] != document.content):
                    raise ValueError('tool_result_source_span_mismatch')
                # Formal corpus readers expose immutable source units. Self-consistent
                # hashes alone must not allow a stale receipt from another version.
                registry = getattr(self.reader, 'chunks', None)
                if registry is not None:
                    expected = registry[document.chunk_id].as_document()
                    for name in ('document_id', 'source', 'source_version', 'section_id',
                                 'chunk_id', 'document_sha256', 'chunk_sha256',
                                 'span_start', 'span_end', 'source_text', 'content'):
                        if getattr(document, name) != getattr(expected, name):
                            raise ValueError('tool_result_current_source_mismatch:' + name)

    @staticmethod
    def _transient(error):
        if isinstance(error, TimeoutError):
            return True
        return isinstance(error, OSError) and error.errno in {
            errno.ETIMEDOUT, errno.ECONNRESET, errno.ECONNABORTED, errno.ECONNREFUSED,
            errno.EINTR, errno.EAGAIN, errno.ENETDOWN, errno.ENETUNREACH,
            errno.EHOSTUNREACH, errno.EPIPE,
        }

    def _deadline(self, start):
        remaining = self.deadline_remaining() if self.deadline_remaining is not None else None
        if remaining is not None and (type(remaining) not in (int, float)
                                      or not math.isfinite(remaining)):
            raise ValueError('invalid_task_deadline_remaining')
        candidates = []
        if self.policy.timeout_enabled:
            candidates.append(start + self.policy.timeout_seconds)
        if remaining is not None:
            candidates.append(start + max(0, remaining))
        return min(candidates) if candidates else None

    @staticmethod
    def _call_kwargs(function, deadline, cancellation):
        try:
            parameters = inspect.signature(function).parameters
        except (TypeError, ValueError):
            return {}
        kwargs = {}
        # Only explicitly declared cooperative parameters are passed.
        for name, value in (('deadline', deadline), ('cancel_event', cancellation)):
            parameter = parameters.get(name)
            if parameter and parameter.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                                  inspect.Parameter.KEYWORD_ONLY}:
                kwargs[name] = value
        return kwargs

    def _invoke(self, function, argument, deadline):
        cancellation = threading.Event()
        kwargs = self._call_kwargs(function, deadline, cancellation)
        if self.policy.timeout_mode == 'cooperative':
            timer = None
            if deadline is not None and 'cancel_event' in kwargs:
                timer = threading.Timer(max(0, deadline - self.clock()), cancellation.set)
                timer.daemon = True
                timer.start()
            try:
                result = function(argument, **kwargs)
            finally:
                if timer is not None:
                    timer.cancel()
            return result, False
        owner = getattr(function, '__self__', function)
        if getattr(owner, 'runtime_thread_safe', False) is not True:
            raise ValueError('thread_timeout_requires_explicit_thread_safe_tool')
        done = threading.Event()
        box = {}

        def work():
            try:
                box['result'] = function(argument, **kwargs)
            except Exception as exc:
                box['error'] = exc
            finally:
                done.set()

        worker = threading.Thread(target=work, name='project2-readonly-tool', daemon=True)
        worker.start()
        finished = done.wait(None if deadline is None else max(0, deadline - self.clock()))
        if not finished:
            cancellation.set()
            self._pending = {'done': done, 'worker': worker, 'box': box,
                             'cancel_event': cancellation}
            return None, True
        if 'error' in box:
            raise box['error']
        if 'result' not in box:
            raise ValueError('tool_thread_completed_without_receipt')
        return box['result'], False

    def _finish(self, request, start, documents=None, error='', retried=False, state=None):
        success = not error
        self.metrics['logical_success' if success else 'logical_failure'] += 1
        if success and retried:
            self.metrics['retry_recovered'] += 1
        result = ToolResult(getattr(request, 'request_id', 'unknown'),
                            'ok' if success else 'error', documents or [], self.redact(error))
        payload = {'request': self._request_dict(request), 'result': {
            'request_id': result.request_id, 'status': result.status,
            'document_ids': [d.document_id for d in result.documents],
            'chunk_ids': [d.chunk_id for d in result.documents],
            'empty_result': success and not result.documents},
            'retried': retried, 'timeout_mode': self.policy.timeout_mode,
            'timeout_is_preemptive': False,
            'wait_can_return_before_tool_stops': self.policy.timeout_mode == 'thread',
            'underlying_execution_stopped': None if state == 'in_flight' else True,
            'in_flight': state == 'in_flight', 'fallback': 'explicit_error_only'}
        self._event('tool', result.status, start, payload, result.error)
        return result

    def call(self, request):
        start = self.clock()
        self.lookups += 1
        self.metrics['logical_requests'] += 1
        error = self._request_error(request)
        if error:
            return self._finish(request, start, error=error)
        if self._pending is not None:
            if not self._pending['done'].is_set():
                return self._finish(request, start, error='tool_prior_request_still_in_flight', state='in_flight')
            self._event('tool_late_completion', 'discarded', start, {
                'receipt_accepted': False, 'reason': 'completed_after_timeout',
                'request': self._pending.get('request'),
                'physical_attempt': self._pending.get('physical_attempt'),
                'underlying_execution_stopped': True})
            self._pending = None
        key = (request.tool, request.argument)
        if self.cache and key in self.entries:
            try:
                documents = deepcopy(self.entries[key])
                self._validate_documents(documents, request)
            except Exception as exc:
                return self._finish(request, start, error='tool_cached_receipt_invalid:' + str(exc))
            self.hits += 1
            self._event('tool_cache', 'hit', start, {'request': self._request_dict(request),
                'same_source_sha_revalidated': self.policy.verify_provenance,
                'current_source_registry_verified': self.policy.verify_provenance
                                                    and getattr(self.reader, 'chunks', None) is not None})
            return self._finish(request, start, documents)
        deadline = self._deadline(start)
        if deadline is not None and self.clock() >= deadline:
            return self._finish(request, start, error='tool_task_deadline_exhausted')
        function = self.search.search if request.tool == 'search' else self.reader.read
        owner = getattr(function, '__self__', function)
        if (self.policy.timeout_mode == 'thread'
                and getattr(owner, 'runtime_thread_safe', False) is not True):
            return self._finish(request, start, error='thread_timeout_requires_explicit_thread_safe_tool')
        retries = self.policy.max_retries if self.policy.retry_enabled else 0
        for attempt in range(1, retries + 2):
            if self.trace.tool_calls >= self.max_calls:
                self.metrics['logical_failure'] += 1
                self._event('tool', 'budget_exceeded', start, {
                    'request': self._request_dict(request), 'physical_attempt': attempt,
                    'tool_calls': self.trace.tool_calls}, 'Tool call limit reached')
                raise BudgetExceeded('Tool call limit reached')
            self.trace.tool_calls += 1
            self.metrics['physical_attempts'] += 1
            if attempt == 2:
                self.metrics['retried_requests'] += 1
            self._event('tool_attempt', 'started', start, {
                'request': self._request_dict(request), 'physical_attempt': attempt,
                'retry': attempt > 1, 'tool_calls': self.trace.tool_calls,
                'deadline_monotonic': deadline, 'timeout_mode': self.policy.timeout_mode,
                'can_preempt_blocking_call': False,
                'wait_can_return_before_tool_stops': self.policy.timeout_mode == 'thread'})
            retryable = False
            try:
                value, in_flight = self._invoke(function, request.argument, deadline)
                if in_flight:
                    self._pending.update(request=self._request_dict(request), physical_attempt=attempt)
                    self.metrics['timeouts'] += 1
                    self.metrics['in_flight_timeouts'] += 1
                    self._event('tool_attempt', 'timeout', start, {
                        'request': self._request_dict(request), 'physical_attempt': attempt,
                        'underlying_execution_stopped': None, 'in_flight': True,
                        'will_retry': False}, 'tool_timeout_in_flight_no_retry')
                    return self._finish(request, start, error='tool_timeout_in_flight_no_retry',
                                        retried=attempt > 1, state='in_flight')
                if deadline is not None and self.clock() >= deadline:
                    self.metrics['timeouts'] += 1
                    self._event('tool_attempt', 'timeout', start, {
                        'request': self._request_dict(request), 'physical_attempt': attempt,
                        'underlying_execution_stopped': True, 'in_flight': False,
                        'will_retry': False}, 'tool_timeout_completed_late_no_retry')
                    return self._finish(request, start, error='tool_timeout_completed_late_no_retry',
                                        retried=attempt > 1)
                documents = value if request.tool == 'search' else [value]
                self._validate_documents(documents, request)
                if request.tool == 'search' and not documents:
                    self.metrics['empty_search_results'] += 1
                if self.cache:
                    self.entries[key] = deepcopy(documents)
                self._event('tool_attempt', 'ok', start, {
                    'request': self._request_dict(request), 'physical_attempt': attempt,
                    'document_count': len(documents), 'underlying_execution_stopped': True})
                return self._finish(request, start, documents, retried=attempt > 1)
            except Exception as exc:
                error = 'tool_' + type(exc).__name__ + ':' + str(exc)
                retryable = self._transient(exc)
                if isinstance(exc, TimeoutError):
                    self.metrics['timeouts'] += 1
                self._event('tool_attempt', 'error', start, {
                    'request': self._request_dict(request), 'physical_attempt': attempt,
                    'error_type': type(exc).__name__, 'transient': retryable,
                    'underlying_execution_stopped': True,
                    'will_retry': retryable and attempt <= retries
                                  and (deadline is None or self.clock() < deadline)}, error)
            if not retryable or attempt > retries or (deadline is not None and self.clock() >= deadline):
                return self._finish(request, start, error=error, retried=attempt > 1)
        raise AssertionError('bounded_tool_loop_unreachable')

    def close(self):
        """Request cooperative cancellation; never claim an arbitrary thread stopped."""
        if self._pending is None:
            return {'in_flight': False, 'underlying_execution_stopped': True}
        self._pending['cancel_event'].set()
        finished = self._pending['done'].is_set()
        return {'in_flight': not finished, 'underlying_execution_stopped': True if finished else None}
