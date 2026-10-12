// All live values are server-owned CPU Mock events. This module never synthesizes a run.
export const EXAMPLE_TASK = 'Analyze whether the proposed method improves baseline performance.';
export const STAGES = [
  { key: 'planner', component: 'Planner', name: 'Planner', detail: 'Break the research goal into evidence-backed subtasks', icon: 'layers' },
  { key: 'tool', component: 'Tool', name: 'Tool Executor', detail: 'Execute a search and retain the tool receipt', icon: 'search' },
  { key: 'memory', component: 'Memory', name: 'Evidence Memory', detail: 'Store retrieved evidence with stable source identifiers', icon: 'database' },
  { key: 'context', component: 'Context', name: 'Context Manager', detail: 'Select whole evidence within the request budget', icon: 'code' },
  { key: 'answer', component: 'Answer', name: 'Answer Generator', detail: 'Generate a deterministic fixture answer', icon: 'bolt' },
  { key: 'critic', component: 'Critic', name: 'Critic / Validation', detail: 'Check fixture citations and complete the run', icon: 'shield' },
];

export function eventName(event) { return event?.payload?.event || event?.event || event?.stage || 'runtime_event'; }
export function assertRunIdentity(record, runId = record?.run_id) {
  if (!runId || record?.run_id !== runId || record?.state?.task_id !== runId) throw new Error('Run identity mismatch: the UI rejected a state from another task.');
}
export function assertEventIdentity(event, runId) {
  if (!Number.isSafeInteger(event?.event_id) || event.event_id < 1 || event?.payload?.state?.task_id !== runId) throw new Error('Invalid event or task identity: the UI rejected the event.');
}
export function mergeEvents(current, incoming) {
  const byId = new Map(current.map((event) => [String(event.event_id), event]));
  for (const event of incoming || []) {
    if (event && event.event_id !== undefined) byId.set(String(event.event_id), event);
  }
  return [...byId.values()].sort((a, b) => Number(a.event_id) - Number(b.event_id));
}
export function statusOf(state) { return String(state?.status || state?.run_status || 'IDLE').toUpperCase(); }
export function isTerminal(state) {
  const status = statusOf(state);
  return status === 'SUCCESS' || status === 'FAILED' || (status === 'RECOVERED' && (state?.current_stage === 'complete' || state?.finished === true || state?.completed === true || state?.terminal === true));
}
export function canStart(state) { return !state || statusOf(state) === 'IDLE' || isTerminal(state); }
export function eventForStage(events, stage) {
  return events.filter((event) => String(event.component).toLowerCase() === stage.component.toLowerCase()).at(-1);
}
export function formatTime(timestamp) {
  if (!timestamp) return '—';
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return String(timestamp);
  return date.toLocaleTimeString('en-GB', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
export function compactState(state, runId) {
  if (!state) return { task_id: null, current_stage: 'idle', completed_steps: 0, memory_size: 0, token_budget: null, checkpoint_id: null };
  return {
    task_id: state.task_id || runId || null,
    current_stage: state.current_stage || state.stage || 'idle',
    completed_steps: state.completed_steps ?? [],
    event_cursor: state.event_cursor ?? null,
    memory_size: state.memory_size ?? state.memory_items ?? state.memory?.length ?? 0,
    token_budget: state.token_budget ?? state.budget_limit ?? null,
    budget_used: state.budget_used ?? null,
    checkpoint_id: state.checkpoint_id ?? null,
  };
}
