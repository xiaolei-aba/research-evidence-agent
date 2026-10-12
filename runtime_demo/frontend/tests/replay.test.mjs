import test from 'node:test';
import assert from 'node:assert/strict';
import { SOURCE_KINDS, assertReplayManifest, assertReplayRecord, formatReplayTime, historicalSummary, replayDelay, replayEvents } from '../src/replay.js';

const manifest = { public_id: 'dev-failure-example', source_kind: 'real_model_dev_history', event_count: 2, sha256: 'a'.repeat(64), recovery_observed: false };
const events = [{ timestamp: null, component: 'Planner', event: 'request_failed', status: 'failed', metadata: { stage: 'planner', error_category: 'input_budget_exceeded', duration_ms: 12 } }, { timestamp: null, component: 'Runtime', event: 'terminal_state', status: 'failed', metadata: { terminal_status: 'failed' } }];
const record = { manifest, events, readonly: true, model_calls: false };

test('read-only source must match the selected identity and event count', () => {
  assert.equal(assertReplayRecord(record, manifest.public_id), record);
  assert.throws(() => assertReplayRecord(record, 'another-source'));
  assert.throws(() => assertReplayRecord({ ...record, readonly: false }, manifest.public_id));
  assert.throws(() => assertReplayRecord({ ...record, model_calls: true }, manifest.public_id));
  assert.throws(() => assertReplayRecord({ ...record, events: events.slice(0, 1) }, manifest.public_id));
});

test('source kinds and SHA must be audited rather than silently relabeled as Mock', () => {
  assert.equal(Object.keys(SOURCE_KINDS).length, 2);
  assert.throws(() => assertReplayManifest({ ...manifest, source_kind: 'unverified_history' }));
  assert.throws(() => assertReplayManifest({ ...manifest, sha256: 'not-a-sha' }));
  assert.throws(() => assertReplayManifest({ ...manifest, public_id: '../private-trace' }));
  assert.throws(() => assertReplayManifest({ ...manifest, event_count: 257 }));
});

test('unknown historical timing is accepted without fabricating a wall clock', () => {
  assert.doesNotThrow(() => assertReplayRecord(record, manifest.public_id));
  assert.equal(formatReplayTime(null), 't = ?');
  assert.equal(formatReplayTime(undefined), 't = ?');
  assert.equal(formatReplayTime(0), '+0.00s');
  assert.equal(formatReplayTime(1.234), '+1.23s');
  assert.throws(() => assertReplayRecord({ ...record, events: [{ ...events[0], timestamp: -2 }, events[1]] }, manifest.public_id));
});

test('playback preserves event order and missing stages without manufacturing state', () => {
  const displayed = replayEvents(events, 1);
  assert.equal(displayed.length, 1);
  assert.equal(displayed[0].component, 'Planner');
  assert.equal(displayed[0].timestamp, null);
  assert.deepEqual(displayed[0].metadata, events[0].metadata);
  assert.equal(displayed[0].historical, true);
  assert.equal(replayEvents(events, 300).length, 2);
  assert.equal(replayEvents(events, -2).length, 0);
  const summary = historicalSummary(events);
  assert.equal(summary.terminal_status, 'failed');
  assert.equal(summary.memory_size, null);
  assert.equal(summary.token_budget, null);
  assert.equal(summary.completed_steps, null);
  assert.deepEqual(summary.recovery_counters, { replayed_steps: null, lost_steps: null, duplicate_tool_calls: null });
});

test('engineering counters appear only after their recorded recovery event is played', () => {
  const recovery = { timestamp: null, component: 'Checkpoint', event: 'recovery_summary', status: 'recovered', metadata: { replayed_steps: 0, lost_steps: 0, duplicate_tool_calls: 0 } };
  assert.equal(historicalSummary(events).recovery_counters.lost_steps, null);
  assert.deepEqual(historicalSummary([...events, recovery]).recovery_counters, { replayed_steps: 0, lost_steps: 0, duplicate_tool_calls: 0 });
  assert.equal(historicalSummary([...events, recovery]).checkpoint_events_observed, 1);
});

test('source snapshot counts remain factual and checkpoint success never masks a failed task', () => {
  const checkpoint = { timestamp: null, component: 'Checkpoint', event: 'checkpoint_saved', status: 'success', metadata: { stage: 'finish', completed_steps_count: 4, memory_size: 3 } };
  const summary = historicalSummary([...events, checkpoint]);
  assert.equal(summary.last_status, 'success');
  assert.equal(summary.terminal_status, 'failed');
  assert.equal(summary.memory_size, 3);
  assert.equal(summary.completed_steps_count, 4);
  assert.equal(summary.last_native_input_tokens, null);
});

test('playback speed controls bounded presentation delay only', () => {
  assert.equal(replayDelay(1), 850);
  assert.equal(replayDelay(4), 212.5);
  assert.throws(() => replayDelay(0));
  assert.throws(() => replayDelay(100));
});
