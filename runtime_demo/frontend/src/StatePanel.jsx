import React from 'react';
import Icon from './Icon.jsx';
import { compactState } from './runtime.js';

function JsonView({ value }) {
  return <pre className="state-json"><span className="json-bracket">{'{'}</span>{Object.entries(value).map(([key, item], index, entries) => <span className="json-line" key={key}><span className="json-key">  "{key}"</span>: <span className={item === null ? 'json-null' : typeof item === 'number' ? 'json-number' : 'json-string'}>{JSON.stringify(item)}</span>{index < entries.length - 1 ? ',' : ''}</span>)}<span className="json-bracket">{'}'}</span></pre>;
}

export default function StatePanel({ state, runId, recoveryReport, executionFingerprint, eventCursor }) {
  const display = compactState(state && { ...state, event_cursor: eventCursor ?? state.event_cursor ?? null }, runId);
  const usedRaw = state?.budget_used ?? state?.token_budget?.used ?? state?.context?.after_tokens;
  const used = typeof usedRaw === 'string' && usedRaw.includes('/') ? Number(usedRaw.split('/')[0]) : Number(usedRaw);
  const limitRaw = state?.token_budget ?? state?.budget_limit ?? state?.context?.budget_limit;
  const limit = typeof limitRaw === 'object' ? Number(limitRaw?.max_tokens ?? limitRaw?.limit) : Number(limitRaw);
  const ratio = used > 0 && limit > 0 ? Math.min(100, used / limit * 100) : 0;
  const checkpoint = state?.checkpoint_id || recoveryReport?.checkpoint_before_failure;
  const fingerprint = executionFingerprint || state?.fingerprint || state?.execution_fingerprint || recoveryReport?.fingerprint;
  const recoveryComplete = recoveryReport?.in_progress === false;
  const counters = ['replayed_steps', 'lost_steps', 'duplicate_tool_calls'];
  const counterNames = ['Replayed steps', 'Lost steps', 'Duplicate tools'];
  return <aside className="state-column">
    <section className="panel" aria-labelledby="state-title">
      <div className="panel-heading"><div><span className="eyebrow">LIVE SNAPSHOT</span><h2 id="state-title">Agent state</h2></div><Icon name="code" size={19} /></div>
      <div className="state-strip"><span className="tiny-dot" /><span className="mono">{runId ? 'state.json' : 'Awaiting a task'}</span><span className="micro-badge">MOCK</span></div>
      <JsonView value={display} />
      <div className="budget-section"><div className="budget-heading"><span>Selected input budget</span><span className="mono">{Number.isFinite(used) && limit > 0 ? `${used.toLocaleString()} / ${limit.toLocaleString()}` : '— / —'}</span></div><div className="budget-track"><span style={{ width: `${ratio}%` }} /></div><p>Fixture input size, not measured model Token usage.</p></div>
    </section>
    <section className="panel recovery-panel" aria-labelledby="recovery-title">
      <div className="panel-heading"><div><span className="eyebrow">DURABLE STATE</span><h2 id="recovery-title">Checkpoint & recovery</h2></div><Icon name="shield" size={19} /></div>
      <div className="checkpoint-identity"><Icon name="database" size={17} /><div><span className="eyebrow">CHECKPOINT ID · SHA CHECKED ON RECOVERY</span><code title={typeof checkpoint === 'string' ? checkpoint : undefined}>{checkpoint ? String(checkpoint).slice(0, 22) : 'No checkpoint yet'}{String(checkpoint || '').length > 22 ? '…' : ''}</code></div></div>
      <div className="recovery-stats">{counters.map((key, index) => <div key={key}><strong>{recoveryComplete ? recoveryReport[key] ?? '—' : '—'}</strong><span>{counterNames[index]}</span></div>)}</div>
      {fingerprint && <div className="fingerprint"><span>Mock fingerprint</span><code title={String(fingerprint)}>{String(fingerprint).slice(0, 16)}…</code></div>}
      {recoveryReport && <div className={`recovery-result ${recoveryComplete && recoveryReport.success ? 'good' : ''}`}><Icon name={recoveryComplete && recoveryReport.success ? 'check' : 'info'} size={14} />{!recoveryComplete ? 'Controlled recovery in progress' : recoveryReport.success ? 'Controlled recovery checks passed' : 'Recovery is not confirmed'}</div>}
      <div className="recovery-disclosure"><Icon name="info" size={14} /><p>Controlled CPU Mock recovery within this server session. No backend process or real LLM worker restart; no production recovery rate.</p></div>
    </section>
    <div className="architecture-note"><div className="architecture-mark"><Icon name="layers" size={19} /></div><div><strong>Runtime first</strong><p>Execution · State · Context · Trace</p><span>Existing mechanisms, synthetic demonstration.</span></div></div>
  </aside>;
}
