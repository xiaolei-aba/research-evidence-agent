import React from 'react';
import Icon from './Icon.jsx';
import { STAGES, eventForStage, formatTime } from './runtime.js';

function EvidenceChips({ evidence }) {
  const entries = Array.isArray(evidence) ? evidence : [];
  return <div className="evidence-chips">{entries.map((item, index) => {
    const value = typeof item === 'string' ? { evidence_id: item } : item;
    return <span className="evidence-chip" key={value.evidence_id || value.id || index} title={value.source || value.document_id || 'Synthetic evidence fixture'}><Icon name="database" size={13} />{value.evidence_id || value.id || `E${index + 1}`}<small>{value.section || value.section_id || value.source || 'fixture'}</small></span>;
  })}</div>;
}

function StepPayload({ stage, event, state }) {
  const payload = event?.payload || {};
  const data = payload.details || payload;
  if (stage.key === 'planner') {
    const subtasks = data.subtasks || data.plan?.subtasks || (Array.isArray(data.plan) ? data.plan : null) || state?.plan?.subtasks;
    return subtasks?.length ? <div className="subtasks">{subtasks.map((task, index) => <div key={index}><span className="subtask-index">{String(index + 1).padStart(2, '0')}</span><span>{typeof task === 'string' ? task : task.goal || task.description || task.query || JSON.stringify(task)}</span></div>)}</div> : <p className="step-description">{data.message || stage.detail}</p>;
  }
  if (stage.key === 'tool') {
    const args = data.arguments || data.args || data.parameters || {};
    const query = data.query || args.query;
    const count = data.result_count ?? data.evidence_count ?? data.chunks_found ?? data.evidence_chunks ?? data.results?.length;
    return <div className="tool-payload"><div className="inline-code"><span className="code-function">{String(data.tool_name || data.tool || 'search_paper').replace(/\(\)$/, '')}</span><span className="muted">(</span>{query ? <span className="code-string">query: {JSON.stringify(query)}</span> : <span className="muted">…</span>}<span className="muted">)</span></div>{count !== undefined && <div className="tool-result"><span className="tiny-dot" />{count} evidence chunks found <span className="muted">· synthetic fixture</span></div>}{data.failure_type && <p className="failure-detail">{data.failure_type} · synthetic / no committed result</p>}{data.message && <p className="step-description">{data.message}</p>}</div>;
  }
  if (stage.key === 'memory') {
    const evidence = data.evidence || data.items || data.evidence_ids || state?.memory || state?.evidence || [];
    return <><EvidenceChips evidence={evidence} /><p className="step-description">{data.message || 'Evidence is retained in memory; context selection does not delete it.'}</p></>;
  }
  if (stage.key === 'context') {
    const before = data.before_tokens ?? data.input_tokens_before ?? state?.context?.before_tokens ?? state?.context_input_before;
    const after = data.after_tokens ?? data.input_tokens_after ?? state?.context?.after_tokens ?? state?.context_input_after;
    const selected = data.selected_evidence_ids || data.selected_ids || data.selected || state?.context?.selected_evidence_ids;
    return <><div className="context-flow"><div><span className="eyebrow">INPUT BEFORE</span><strong>{before === undefined ? '—' : Number(before).toLocaleString()}<small> tokens</small></strong></div><Icon name="arrow" size={20} /><div className="context-after"><span className="eyebrow">SELECTED CONTEXT</span><strong>{after === undefined ? '—' : Number(after).toLocaleString()}<small> tokens</small></strong></div><span className="micro-badge">MOCK COUNTS</span></div>{selected && <EvidenceChips evidence={selected} />}<p className="step-description">{data.action || data.message || 'Whole evidence selection; all retrieved evidence stays in memory.'}</p></>;
  }
  if (stage.key === 'answer') {
    const answer = data.answer?.text || data.answer || data.text || state?.answer?.text || state?.answer;
    return <p className={answer ? 'answer-preview' : 'step-description'}>{typeof answer === 'string' ? answer : data.message || stage.detail}</p>;
  }
  const pass = data.citation_check || data.verdict || data.result || data.validation;
  return <div className="validation-payload"><span className="validation-label">Citation check</span>{pass && <span className="validation-pass"><Icon name="check" size={13} />{typeof pass === 'string' ? pass : 'PASS'}</span>}<span className="muted">{data.message || 'Mock structural citation validation'}</span></div>;
}

export default function Timeline({ events, state }) {
  return <section className="panel timeline-panel" aria-labelledby="timeline-title"><div className="panel-heading"><div><span className="eyebrow">EXECUTION</span><h2 id="timeline-title">Agent execution timeline</h2></div><span className="subtle-label"><span className="tiny-dot" />Server event stream</span></div><div className="timeline">{STAGES.map((stage, index) => {
    const event = eventForStage(events, stage);
    const status = event?.status || 'pending';
    const normalized = status === 'completed' ? 'success' : status;
    const timing = event?.payload?.latency_ms ?? event?.payload?.duration_ms;
    return <article className={`timeline-step ${normalized}`} key={stage.key} aria-label={`Step ${index + 1}: ${stage.name}, ${normalized}`}><div className="step-rail"><span className="step-number">{normalized === 'success' || normalized === 'recovered' ? <Icon name="check" size={15} /> : normalized === 'failed' ? <Icon name="cross" size={15} /> : String(index + 1).padStart(2, '0')}</span></div><div className="step-card"><div className="step-card-heading"><div className="step-title"><Icon name={stage.icon} size={16} /><h3>{stage.name}</h3></div><div className="step-meta">{event && <span className="mono time-label">{timing !== undefined ? `${timing} ms` : formatTime(event.timestamp)}</span>}<span className={`status-label ${normalized}`}><span className="tiny-dot" />{normalized === 'success' ? 'Completed' : normalized === 'pending' ? 'Waiting' : normalized.charAt(0).toUpperCase() + normalized.slice(1)}</span></div></div>{event ? <StepPayload stage={stage} event={event} state={state} /> : <p className="step-description">{stage.detail}</p>}</div></article>;
  })}</div><div className="timeline-footer"><Icon name="info" size={14} /><span>Planner, answer and critic outputs are deterministic Mock fixtures. No LLM is connected.</span></div></section>;
}
