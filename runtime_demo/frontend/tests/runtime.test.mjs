import test from 'node:test';
import assert from 'node:assert/strict';
import { assertEventIdentity, assertRunIdentity, canStart, compactState, eventForStage, eventName, isTerminal, mergeEvents, STAGES } from '../src/runtime.js';

test('a recovering actor cannot be replaced by a new run before completion', () => {
  const recovering = { status: 'RECOVERED', current_stage: 'Tool', completed_steps: ['planner'] };
  assert.equal(isTerminal(recovering), false);
  assert.equal(canStart(recovering), false);
  assert.equal(canStart({ ...recovering, current_stage: 'complete', completed_steps: STAGES.map((stage) => stage.key) }), true);
  assert.equal(canStart({ status: 'FAILED' }), true);
});

test('SSE replay deduplicates immutable event identities and preserves order', () => {
  const first = { event_id: 1, component: 'Planner' };
  const second = { event_id: 2, component: 'Tool' };
  const third = { event_id: 3, component: 'Tool' };
  assert.deepEqual(mergeEvents([second, third], [first, second]), [first, second, third]);
  assert.equal(mergeEvents([first], [first]).length, 1);
});

test('recovery updates the same tool stage and does not fabricate extra steps', () => {
  const failed = { event_id: 4, component: 'Tool', status: 'failed', payload: { event: 'tool_timeout' } };
  const success = { event_id: 8, component: 'Tool', status: 'success', payload: { event: 'tool_call' } };
  const events = [failed, success];
  assert.equal(eventForStage(events, STAGES[1]), success);
  assert.equal(eventName(failed), 'tool_timeout');
});

test('state panel displays backend budget and evidence memory separately', () => {
  const state = { task_id: 'mock-1', current_stage: 'Context', completed_steps: ['planner', 'tool', 'memory', 'context'], memory_size: 3, token_budget: { limit: 4096, used: 2800 }, checkpoint_id: 'a'.repeat(64) };
  const displayed = compactState(state, 'ignored-id');
  assert.equal(displayed.memory_size, 3);
  assert.deepEqual(displayed.token_budget, { limit: 4096, used: 2800 });
  assert.deepEqual(displayed.completed_steps, state.completed_steps);
  assert.equal(displayed.task_id, 'mock-1');
});

test('idle display has no invented task, checkpoint or token counts', () => {
  const displayed = compactState(null, null);
  assert.equal(displayed.task_id, null);
  assert.equal(displayed.checkpoint_id, null);
  assert.equal(displayed.token_budget, null);
  assert.equal(displayed.completed_steps, 0);
});

test('cross-task or malformed state cannot be accepted from API or SSE', () => {
  assert.doesNotThrow(() => assertRunIdentity({ run_id: 'mock-1', state: { task_id: 'mock-1' } }));
  assert.throws(() => assertRunIdentity({ run_id: 'mock-1', state: { task_id: 'mock-2' } }));
  assert.doesNotThrow(() => assertEventIdentity({ event_id: 1, payload: { state: { task_id: 'mock-1' } } }, 'mock-1'));
  assert.throws(() => assertEventIdentity({ event_id: 1, payload: { state: { task_id: 'mock-2' } } }, 'mock-1'));
  assert.throws(() => assertEventIdentity({ event_id: '1', payload: { state: { task_id: 'mock-1' } } }, 'mock-1'));
});
