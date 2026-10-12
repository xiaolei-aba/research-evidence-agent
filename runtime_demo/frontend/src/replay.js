// Replay is a read-only projection. UI ordinals identify rows, not historical tasks.
export const SOURCE_KINDS = {
  real_model_dev_history: 'Real-model Dev history · sanitized',
  engineering_mock_history: 'CPU engineering history · sanitized',
};
export const REPLAY_SPEEDS = [0.5, 1, 2, 4];

export function assertReplayManifest(manifest) {
  if (!manifest || !/^[a-z0-9][a-z0-9_-]{0,79}$/i.test(manifest.public_id || '') || !SOURCE_KINDS[manifest.source_kind] || !Number.isSafeInteger(manifest.event_count) || manifest.event_count < 1 || manifest.event_count > 256 || !/^[a-f0-9]{64}$/i.test(manifest.sha256 || '') || typeof manifest.recovery_observed !== 'boolean') {
    throw new Error('Invalid historical source manifest. Playback is blocked.');
  }
  return manifest;
}

export function assertReplayRecord(record, publicId) {
  const manifest = assertReplayManifest(record?.manifest);
  if (manifest.public_id !== publicId || record.readonly !== true || record.model_calls !== false || !Array.isArray(record.events) || record.events.length !== manifest.event_count) {
    throw new Error('Historical source identity or read-only boundary mismatch. Playback is blocked.');
  }
  for (const event of record.events) {
    if (!event || !['Planner', 'Tool', 'Memory', 'Context', 'Checkpoint', 'Answer', 'Critic', 'Runtime'].includes(event.component) || typeof event.event !== 'string' || !event.event || typeof event.status !== 'string' || !event.status || !(event.timestamp === null || (Number.isFinite(event.timestamp) && event.timestamp >= 0)) || !event.metadata || typeof event.metadata !== 'object' || Array.isArray(event.metadata)) {
      throw new Error('Invalid historical event schema. Playback is blocked.');
    }
  }
  return record;
}

export function replayEvents(events, count) {
  return events.slice(0, Math.max(0, Math.min(count, events.length))).map((event, index) => ({
    ...event,
    event_id: index + 1,
    stage: event.metadata.stage ?? 'unknown',
    historical: true,
    payload: { event: event.event, ...event.metadata },
  }));
}

export function formatReplayTime(value) {
  return value === null || value === undefined ? 't = ?' : `+${Number(value).toFixed(2)}s`;
}

export function historicalSummary(events) {
  const last = events.at(-1);
  const checkpoints = events.filter((event) => event.component === 'Checkpoint');
  const lastNumber = (key) => [...events].reverse().find((event) => Number.isFinite(event.metadata?.[key]))?.metadata[key] ?? null;
  // Absence in a sanitized excerpt is unknown, never evidence of zero loss.
  const recovery = [...events].reverse().find((event) => ['replayed_steps', 'lost_steps', 'duplicate_tool_calls'].some((key) => Number.isSafeInteger(event.metadata?.[key])));
  return {
    current_stage: last?.metadata?.stage ?? null,
    last_event: last?.event ?? null,
    last_status: last?.status ?? null,
    terminal_status: [...events].reverse().find((event) => typeof event.metadata?.terminal_status === 'string')?.metadata.terminal_status ?? null,
    memory_size: lastNumber('memory_size'),
    token_budget: null,
    completed_steps: null,
    completed_steps_count: lastNumber('completed_steps_count'),
    last_native_input_tokens: lastNumber('native_input_tokens'),
    last_requested_reservation_tokens: lastNumber('requested_reservation_tokens'),
    max_input_tokens: lastNumber('max_input_tokens'),
    checkpoint_events_observed: checkpoints.length,
    recovery_counters: Object.fromEntries(['replayed_steps', 'lost_steps', 'duplicate_tool_calls'].map((key) => [key, Number.isSafeInteger(recovery?.metadata?.[key]) ? recovery.metadata[key] : null])),
  };
}

export function replayDelay(speed) {
  if (!REPLAY_SPEEDS.includes(Number(speed))) throw new Error('Unsupported replay speed.');
  // Fixed presentation pacing; historical event timing may be unavailable.
  return 850 / Number(speed);
}
