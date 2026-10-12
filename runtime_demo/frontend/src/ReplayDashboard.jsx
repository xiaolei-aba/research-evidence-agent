import React, { useEffect, useRef, useState } from 'react';
import Icon from './Icon.jsx';
import TraceViewer from './TraceViewer.jsx';
import { SOURCE_KINDS, assertReplayManifest, assertReplayRecord, formatReplayTime, historicalSummary, replayDelay, replayEvents } from './replay.js';

async function readSource(path, signal) {
  const response = await fetch(path, { method: 'GET', signal });
  let body;
  try { body = await response.json(); } catch { throw new Error(`Invalid replay response (${response.status}).`); }
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : body.message || body.detail?.message || `Replay unavailable (${response.status}).`);
  return body;
}

function ReplayTimeline({ events, manifest }) {
  return <section className="panel timeline-panel" aria-labelledby="timeline-title"><div className="panel-heading"><div><span className="eyebrow">RECORDED EVENT ORDER</span><h2 id="timeline-title">Historical execution timeline</h2></div><span className="subtle-label">Read-only excerpt</span></div><div className="timeline historical-timeline">{events.length ? events.map((event, index) => <article className={`timeline-step ${event.status}`} key={event.event_id} aria-label={`Historical event ${index + 1}: ${event.component}, ${event.event}`}><div className="step-rail"><span className="step-number">{index + 1}</span></div><div className="step-card"><div className="step-card-heading"><div className="step-title"><Icon name={event.component === 'Checkpoint' ? 'database' : event.status === 'failed' ? 'flag' : 'trace'} size={16} /><h3>{event.component}</h3></div><span className={`status-label ${event.status}`}>{event.status}</span></div><strong className="historical-event-name mono">{event.event}</strong><p className="step-description">Stage: {event.metadata.stage ?? 'unknown'} · {formatReplayTime(event.timestamp)}{typeof event.metadata.duration_ms === 'number' ? ` · recorded operation ${event.metadata.duration_ms} ms` : ''}</p>{event.metadata.error_category && <p className="failure-detail">Error category: {event.metadata.error_category}</p>}{event.metadata.terminal_status && <p className="step-description">Recorded terminal state: {event.metadata.terminal_status}</p>}</div></article>) : <div className="empty-trace"><Icon name="trace" size={27} /><strong>{manifest ? 'Ready to play this recorded excerpt' : 'Choose a historical source'}</strong><span>Only recorded events appear. Missing stages are not synthesized.</span></div>}</div><div className="timeline-footer"><Icon name="info" size={14} /><span>Playback speed controls presentation pacing, not historical latency. Unknown event timing is shown as t = ?.</span></div></section>;
}

function ReplayState({ events, manifest, count, total }) {
  const summary = historicalSummary(events);
  const known = manifest?.known ?? null;
  const unknown = manifest?.unknown ?? null;
  const display = { source_kind: manifest?.source_kind ?? null, readonly: true, observed_events: count, current_stage: summary.current_stage, last_event_status: summary.last_status, recorded_terminal_status: summary.terminal_status, completed_steps_count: summary.completed_steps_count, memory_size: summary.memory_size, last_native_input_tokens: summary.last_native_input_tokens, last_requested_reservation_tokens: summary.last_requested_reservation_tokens, max_input_tokens: summary.max_input_tokens };
  return <aside className="state-column"><section className="panel" aria-labelledby="state-title"><div className="panel-heading"><div><span className="eyebrow">PARTIAL HISTORICAL PROJECTION</span><h2 id="state-title">Agent state</h2></div><Icon name="code" size={19} /></div><div className="state-strip"><Icon name="shield" size={13} /><span>Source excerpt · {count} / {total} events</span><span className="micro-badge">READ ONLY</span></div><pre className="state-json">{JSON.stringify(display, null, 2)}</pre><div className="budget-section"><p>Null means unknown or withheld, not zero. No memory or token counts are inferred from missing data.</p></div></section><section className="panel recovery-panel" aria-labelledby="recovery-title"><div className="panel-heading"><div><span className="eyebrow">OBSERVED BOUNDARIES</span><h2 id="recovery-title">Checkpoint & recovery</h2></div><Icon name="shield" size={19} /></div><div className="checkpoint-identity"><Icon name="database" size={17} /><div><span className="eyebrow">CHECKPOINT EVENTS IN PLAYED EXCERPT</span><code>{summary.checkpoint_events_observed} observed · total runtime checkpoints unknown</code></div></div><div className="recovery-stats">{Object.entries(summary.recovery_counters).map(([key, value]) => <div key={key}><strong>{value === null ? '?' : value}</strong><span>{({ replayed_steps: 'Replayed steps', lost_steps: 'Lost steps', duplicate_tool_calls: 'Duplicate tools' })[key]}</span></div>)}</div><div className="recovery-disclosure"><Icon name="info" size={14} /><p>{manifest?.source_kind === 'engineering_mock_history' ? 'Recorded CPU engineering Process Restart. These counters belong to that synthetic historical demo, not a real LLM or production service.' : 'Real-model Dev excerpt. No historical recovery is observed in this source. This player performs no recovery.'}</p></div></section>{manifest && <section className="panel source-audit"><span className="eyebrow">SOURCE BOUNDARY</span><h3>Known / unknown</h3><pre>{JSON.stringify({ known, unknown }, null, 2)}</pre><p>Sanitized excerpt SHA</p><code title={manifest.sha256}>{manifest.sha256.slice(0, 20)}…</code></section>}</aside>;
}

export default function ReplayDashboard({ isFocused }) {
  const [sources, setSources] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [record, setRecord] = useState(null);
  const [count, setCount] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const loadSequence = useRef(0);
  const playingRef = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    readSource('/api/replays', controller.signal).then((body) => {
      if (!current) return;
      if (body.available !== true || !Array.isArray(body.replays) || !body.replays.length || body.replays.length > 10) throw new Error('No audited historical replay sources are available.');
      const manifests = body.replays.map(assertReplayManifest);
      if (new Set(manifests.map((entry) => entry.public_id)).size !== manifests.length) throw new Error('Duplicate historical source identities.');
      setSources(manifests);
      setSelectedId(manifests[0].public_id);
    }).catch((failure) => { if (current && failure.name !== 'AbortError') { setError(failure.message); setLoading(false); } });
    return () => { current = false; controller.abort(); };
  }, []);

  useEffect(() => {
    if (!selectedId) return undefined;
    const controller = new AbortController();
    const sequence = ++loadSequence.current;
    let current = true;
    playingRef.current = false;
    setPlaying(false);
    setCount(0);
    setRecord(null);
    setLoading(true);
    setError(null);
    readSource(`/api/replays/${encodeURIComponent(selectedId)}`, controller.signal).then((body) => {
      if (!current || sequence !== loadSequence.current) return;
      const source = assertReplayRecord(body, selectedId);
      const listed = sources.find((entry) => entry.public_id === selectedId);
      if (!listed || listed.sha256 !== source.manifest.sha256 || listed.source_kind !== source.manifest.source_kind || listed.event_count !== source.events.length) throw new Error('Source changed between listing and loading. Playback is blocked.');
      setRecord(source);
    }).catch((failure) => { if (current && sequence === loadSequence.current && failure.name !== 'AbortError') setError(failure.message); })
      .finally(() => { if (current && sequence === loadSequence.current) setLoading(false); });
    return () => { current = false; controller.abort(); };
  }, [selectedId, sources]);

  useEffect(() => {
    if (!playing || !record || count >= record.events.length) { if (playing) { playingRef.current = false; setPlaying(false); } return undefined; }
    const sequence = loadSequence.current;
    const timer = setTimeout(() => { if (playingRef.current && sequence === loadSequence.current) setCount((value) => Math.min(record.events.length, value + 1)); }, replayDelay(speed));
    return () => clearTimeout(timer);
  }, [playing, record, count, speed]);

  const manifest = record?.manifest;
  const events = replayEvents(record?.events || [], count);
  const playerState = loading ? 'LOADING' : playing ? 'PLAYING' : record && count === record.events.length ? 'ENDED' : count ? 'PAUSED' : 'READY';
  function play() { playingRef.current = true; setPlaying(true); }
  function pause() { playingRef.current = false; setPlaying(false); }
  function selectSource(event) { loadSequence.current += 1; pause(); setCount(0); setRecord(null); setLoading(true); setSelectedId(event.target.value); }
  function reset() { pause(); setCount(0); }

  return <div className="replay-dashboard">
    <div className="replay-notice"><Icon name="shield" size={18} /><div><strong>Sanitized historical trace · read only</strong><p>This player reads audited events. It does not start an Agent, call a model, resample a task or execute recovery. Original question, evidence and raw responses are withheld.</p></div><span className="notice-tag">NO INFERENCE</span></div>
    {error && <div className="error-banner" role="alert"><Icon name="info" size={17} /><span>{error} No Mock fallback is used.</span></div>}
    <section className="panel task-panel" aria-labelledby="task-title"><div className="task-heading"><div><Icon name="trace" size={17} /><h2 id="task-title">Historical source</h2><span className="micro-badge">READ ONLY</span></div><span className="connection-label" role="status">{playerState}</span></div><div className="replay-source-controls"><label><span>Audited excerpt</span><select aria-label="Historical trace" value={selectedId} onChange={selectSource} disabled={!sources.length}>{!sources.length && <option value="">Loading audited sources…</option>}{sources.map((source) => <option key={source.public_id} value={source.public_id}>{source.title || source.public_id}</option>)}</select></label><label><span>Original task text</span><textarea rows="2" aria-label="Research task" value="Withheld from public replay · use the source's stage metadata" disabled readOnly /></label></div><div className="task-footer"><p><strong>{manifest ? SOURCE_KINDS[manifest.source_kind] : 'Awaiting source validation'}</strong><br />Only recorded stages are shown; unobserved stages and counters remain unknown.</p><div className="recovery-actions"><button className="button ghost danger" disabled title="Historical replay is read only">Inject Failure · disabled</button><button className="button ghost" disabled title="Replay does not perform recovery">Recovery · read only</button></div></div><div className="replay-controls"><button className="button primary" aria-label="Play replay" disabled={loading || !record || playing || count >= record.events.length} onClick={play}><Icon name="play" size={15} />Play</button><button className="button ghost" aria-label="Pause replay" disabled={!playing} onClick={pause}><Icon name="clock" size={15} />Pause</button><button className="button ghost" aria-label="Reset replay" disabled={!record || loading} onClick={reset}><Icon name="refresh" size={15} />Reset</button><label className="speed-control">Pacing<select aria-label="Replay speed" value={speed} onChange={(event) => setSpeed(Number(event.target.value))}><option value="0.5">0.5×</option><option value="1">1×</option><option value="2">2×</option><option value="4">4×</option></select></label><span className="mono replay-position">{count} / {record?.events.length ?? '?'} events</span></div></section>
    <div className="run-summary"><div><span className="summary-label">PUBLIC SOURCE ID</span><code>{manifest?.public_id || 'Unknown'}</code></div><div><span className="summary-label">SOURCE KIND</span><strong className="replay-summary-kind">{manifest?.source_kind || 'Unknown'}</strong></div><div><span className="summary-label">PLAYER</span><strong>{playerState}</strong></div><div><span className="summary-label">MODEL CONNECTION</span><strong className="no-model"><Icon name="shield" size={14} />None · Read only</strong></div></div>
    <div className="execution-grid"><ReplayTimeline events={events} manifest={manifest} /><ReplayState events={events} manifest={manifest} count={count} total={record?.events.length ?? '?'} /></div><TraceViewer key={manifest?.public_id || 'none'} events={events} isFocused={isFocused} historical />
    <section className="panel replay-failures" id="failure-title"><span className="eyebrow">FAILURE ANALYSIS</span><h2>Recorded categories, no aggregate rate</h2>{events.some((event) => event.metadata.error_category) ? <ul>{[...new Set(events.map((event) => event.metadata.error_category).filter(Boolean))].map((category) => <li key={category}><Icon name="flag" size={15} />{category}</li>)}</ul> : <p>No error category observed in the played excerpt yet. This does not establish a zero failure rate.</p>}<p>A selected excerpt has no evaluation denominator. The Mock illustration is not displayed in historical mode.</p></section>
  </div>;
}
