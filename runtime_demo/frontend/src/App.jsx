import React, { useCallback, useEffect, useRef, useState } from 'react';
import Timeline from './Timeline.jsx';
import StatePanel from './StatePanel.jsx';
import TraceViewer from './TraceViewer.jsx';
import FailureDashboard from './FailureDashboard.jsx';
import Icon from './Icon.jsx';
import AboutSystem from './AboutSystem.jsx';
import ReplayDashboard from './ReplayDashboard.jsx';
import { EXAMPLE_TASK, assertEventIdentity, assertRunIdentity, canStart, eventName, isTerminal, mergeEvents, statusOf } from './runtime.js';

async function request(path, body, signal) {
  const response = await fetch(path, { method: body === undefined ? 'GET' : 'POST', headers: body === undefined ? {} : { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal });
  let value;
  try { value = await response.json(); } catch { throw new Error(`Backend returned an invalid response (${response.status}).`); }
  if (!response.ok) throw new Error(value.message || value.detail?.message || (typeof value.detail === 'string' ? value.detail : null) || `Request failed (${response.status}).`);
  if (value.engineering_mock !== true) throw new Error('Unrecognised run: the UI accepts engineering Mock sessions only.');
  assertRunIdentity(value);
  return value;
}

export default function App() {
  const [task, setTask] = useState(EXAMPLE_TASK);
  const [snapshot, setSnapshot] = useState(null);
  const [events, setEvents] = useState([]);
  const [pending, setPending] = useState(null);
  const [error, setError] = useState(null);
  const [connection, setConnection] = useState('idle');
  const [activeNav, setActiveNav] = useState('overview');
  const [page, setPage] = useState(window.location.hash === '#about-system' ? 'about' : 'dashboard');
  const [mode, setMode] = useState('mock');
  const modeRef = useRef('mock');
  const streamRef = useRef(null);
  const timerRef = useRef(null);
  const runRef = useRef(null);
  const eventCursorRef = useRef(0);
  const stateRef = useRef(null);
  const generationRef = useRef(0);
  const mountedRef = useRef(true);
  const connectRef = useRef(null);
  const requestControllers = useRef(new Set());
  const operationRef = useRef(false);

  const closeStream = useCallback(() => {
    streamRef.current?.close();
    streamRef.current = null;
    clearTimeout(timerRef.current);
    timerRef.current = null;
  }, []);

  const acceptSnapshot = useCallback((record) => {
    if (!mountedRef.current || modeRef.current !== 'mock' || runRef.current !== record.run_id) return;
    setEvents((current) => mergeEvents(current, record.events));
    const incomingCursor = Math.max(0, ...(record.events || []).map((event) => Number(event.event_id)));
    // A delayed POST/GET response may precede events already received over SSE.
    if (incomingCursor < eventCursorRef.current) return;
    stateRef.current = record.state;
    setSnapshot(record);
    for (const event of record.events || []) eventCursorRef.current = Math.max(eventCursorRef.current, Number(event.event_id));
  }, []);

  const fetchTracked = useCallback(async (path, body) => {
    const controller = new AbortController();
    requestControllers.current.add(controller);
    try { return await request(path, body, controller.signal); }
    finally { requestControllers.current.delete(controller); }
  }, []);

  const connectRun = useCallback((runId, reconnectAttempt = 0) => {
    closeStream();
    if (!mountedRef.current || modeRef.current !== 'mock' || runRef.current !== runId) return;
    const generation = ++generationRef.current;
    const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events?after=${eventCursorRef.current}`);
    streamRef.current = source;
    const current = () => mountedRef.current && modeRef.current === 'mock' && runRef.current === runId && generationRef.current === generation && streamRef.current === source;
    setConnection(reconnectAttempt ? 'reconnecting' : 'connecting');
    source.onopen = () => { if (current()) setConnection('live'); };
    source.addEventListener('runtime_event', (message) => {
      if (!current()) return;
      try {
        const event = JSON.parse(message.data);
        assertEventIdentity(event, runId);
        setEvents((previous) => mergeEvents(previous, [event]));
        if (Number(event.event_id) <= eventCursorRef.current) return;
        eventCursorRef.current = Math.max(eventCursorRef.current, Number(event.event_id));
        stateRef.current = event.payload.state;
        setSnapshot((previous) => previous?.run_id === runId ? { ...previous, state: event.payload.state } : previous);
        setError(null);
        if (isTerminal(event.payload.state)) {
          source.close();
          if (streamRef.current === source) streamRef.current = null;
          setConnection('complete');
          fetchTracked(`/api/runs/${encodeURIComponent(runId)}`).then((record) => {
            if (mountedRef.current && runRef.current === runId && generationRef.current === generation) acceptSnapshot(record);
          }).catch((failure) => { if (mountedRef.current && runRef.current === runId && generationRef.current === generation) setError(failure.message); });
        }
      } catch (failure) {
        source.close();
        setConnection('error');
        setError(failure.message);
      }
    });
    source.onerror = () => {
      if (!current()) return;
      source.close();
      streamRef.current = null;
      if (isTerminal(stateRef.current)) { setConnection('complete'); return; }
      setConnection('reconnecting');
      fetchTracked(`/api/runs/${encodeURIComponent(runId)}`).then((record) => {
        if (!mountedRef.current || runRef.current !== runId || generationRef.current !== generation) return;
        acceptSnapshot(record);
        if (isTerminal(record.state)) { setConnection('complete'); return; }
        if (reconnectAttempt < 2) {
          timerRef.current = setTimeout(() => connectRef.current?.(runId, reconnectAttempt + 1), 1000 * (reconnectAttempt + 1));
        } else { setConnection('error'); setError('Event stream disconnected. Reconnect to read the saved run; no new task will be started.'); }
      }).catch((failure) => {
        if (!mountedRef.current || runRef.current !== runId || generationRef.current !== generation) return;
        if (reconnectAttempt < 2) timerRef.current = setTimeout(() => connectRef.current?.(runId, reconnectAttempt + 1), 1000 * (reconnectAttempt + 1));
        else { setConnection('error'); setError(`Backend unavailable: ${failure.message} Reconnect when it is running.`); }
      });
    };
  }, [acceptSnapshot, closeStream, fetchTracked]);
  connectRef.current = connectRun;

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; generationRef.current += 1; closeStream(); for (const controller of requestControllers.current) controller.abort(); };
  }, [closeStream]);

  useEffect(() => {
    const onHash = () => setPage(window.location.hash === '#about-system' ? 'about' : 'dashboard');
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  async function startRun() {
    if (modeRef.current !== 'mock' || operationRef.current || !canStart(stateRef.current) || !task.trim()) return;
    operationRef.current = true;
    setPending('start');
    setError(null);
    closeStream();
    const generation = ++generationRef.current;
    try {
      const record = await fetchTracked('/api/runs', { task: task.trim() });
      if (!mountedRef.current || modeRef.current !== 'mock' || generationRef.current !== generation) return;
      runRef.current = record.run_id;
      eventCursorRef.current = 0;
      setEvents([]);
      acceptSnapshot(record);
      connectRun(record.run_id);
    } catch (failure) { if (mountedRef.current && modeRef.current === 'mock' && failure.name !== 'AbortError') setError(`Could not start the Mock task: ${failure.message}`); }
    finally { operationRef.current = false; if (mountedRef.current) setPending(null); }
  }

  async function action(name) {
    const runId = runRef.current;
    if (modeRef.current !== 'mock' || !runId || operationRef.current) return;
    operationRef.current = true;
    setPending(name);
    setError(null);
    try {
      const record = await fetchTracked(`/api/runs/${encodeURIComponent(runId)}/${name === 'inject' ? 'inject-failure' : 'recover'}`, {});
      if (!mountedRef.current || modeRef.current !== 'mock' || runRef.current !== runId) return;
      acceptSnapshot(record);
      if (name === 'recover') connectRun(runId);
    } catch (failure) { if (mountedRef.current && modeRef.current === 'mock' && runRef.current === runId && failure.name !== 'AbortError') setError(failure.message); }
    finally { operationRef.current = false; if (mountedRef.current && runRef.current === runId) setPending(null); }
  }

  function navigate(name, target) {
    setPage('dashboard');
    if (window.location.hash !== '#dashboard') window.location.hash = 'dashboard';
    setActiveNav(name);
    requestAnimationFrame(() => document.getElementById(target)?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' }));
  }

  function showAbout() {
    setPage('about');
    window.location.hash = 'about-system';
    window.scrollTo({ top: 0, behavior: 'instant' });
  }

  function changeMode(nextMode) {
    if (operationRef.current || nextMode === modeRef.current || !['mock', 'replay'].includes(nextMode)) return;
    modeRef.current = nextMode;
    const generation = ++generationRef.current;
    closeStream();
    for (const controller of requestControllers.current) controller.abort();
    // Replay owns separate state. Keep the Mock identity, trace and cursor so
    // returning reads the existing actor instead of creating another run.
    setError(null);
    setConnection('idle');
    setMode(nextMode);
    const runId = runRef.current;
    if (nextMode !== 'mock' || !runId) return;
    operationRef.current = true;
    setPending('readback');
    setConnection('connecting');
    const current = () => mountedRef.current && modeRef.current === 'mock' && runRef.current === runId && generationRef.current === generation;
    fetchTracked(`/api/runs/${encodeURIComponent(runId)}`).then((record) => {
      if (!current()) return;
      assertRunIdentity(record, runId);
      acceptSnapshot(record);
      if (isTerminal(stateRef.current)) setConnection('complete');
      else connectRun(runId);
    }).catch((failure) => {
      if (!current() || failure.name === 'AbortError') return;
      setConnection('error');
      setError(`Could not read the existing Mock run: ${failure.message} No new task was started.`);
    }).finally(() => {
      operationRef.current = false;
      if (mountedRef.current && modeRef.current === 'mock' && runRef.current === runId) setPending(null);
    });
  }

  const state = snapshot?.state;
  const status = statusOf(state);
  const queued = events.some((event) => eventName(event) === 'failure_queued') && status !== 'FAILED' && status !== 'RECOVERED';
  const canInject = !pending && snapshot?.can_inject === true && status === 'RUNNING' && ['planner', 'tool'].includes(String(state?.current_stage).toLowerCase()) && !queued;
  const canRecover = !pending && snapshot?.can_recover === true && status === 'FAILED' && events.some((event) => eventName(event) === 'tool_timeout');
  const completedCount = Array.isArray(state?.completed_steps) ? state.completed_steps.length : state?.completed_steps || 0;
  const checkpointEvents = events.filter((event) => event.component === 'Checkpoint');
  const lastCheckpoint = checkpointEvents.at(-1);

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand-mark" href="#dashboard" aria-label="ResearchEvidence overview" onClick={(event) => { event.preventDefault(); navigate('overview', 'overview'); }}><Icon name="layers" size={26} /></a>
      <span className="sidebar-divider" />
      <nav aria-label="Dashboard navigation">
        <button className={page === 'dashboard' && activeNav === 'overview' ? 'nav-item active' : 'nav-item'} title="Dashboard" aria-label="Dashboard" onClick={() => navigate('overview', 'overview')}><Icon name="grid" size={20} /><span>Dashboard</span></button>
        <button className={page === 'dashboard' && activeNav === 'trace' ? 'nav-item active' : 'nav-item'} title="Trace" aria-label="Trace" onClick={() => navigate('trace', 'trace-viewer')}><Icon name="trace" size={21} /><span>Trace</span></button>
        <button className={page === 'dashboard' && activeNav === 'failures' ? 'nav-item active' : 'nav-item'} title="Failures" aria-label="Failures" onClick={() => navigate('failures', 'failure-title')}><Icon name="flag" size={20} /><span>Failures</span></button>
        <button className={page === 'about' ? 'nav-item active' : 'nav-item'} title="About System" aria-label="About System" onClick={showAbout}><Icon name="layers" size={20} /><span>About</span></button>
      </nav>
      <div className="sidebar-bottom"><span className="local-indicator" /><span>DEMO</span><Icon name="cpu" size={18} /></div>
    </aside>
    <main className="main-content" id="overview">
      <div className="topbar"><span className="breadcrumb">ResearchEvidence <Icon name="chevron" size={12} /><strong>{page === 'about' ? 'About System' : 'Runtime observability'}</strong></span><span className="local-label"><span className="tiny-dot" />No model connected</span></div>
      <header className="page-header"><div><div className="header-eyebrow">AGENT RUNTIME / HARNESS / TRACE / EVAL</div><h1>ResearchEvidence Agent Runtime</h1><p>Long-horizon Agent Execution & Observability Demo</p></div><div className="header-badges"><span className="mock-pill"><Icon name="cpu" size={14} />{page === 'about' ? 'CPU DEMO / NO INFERENCE' : mode === 'mock' ? 'MOCK / CPU ONLY' : 'SANITIZED REPLAY / READ ONLY'}</span>{page === 'dashboard' && mode === 'mock' && <span className={`run-status ${status.toLowerCase()}`} role="status"><span className="tiny-dot" />{status}</span>}</div></header>
      {page === 'about' ? <AboutSystem /> : <>
        <section className="mode-panel" aria-label="Execution source"><label htmlFor="run-mode"><span className="eyebrow">RUN MODE</span><select id="run-mode" aria-label="Run mode" value={mode} onChange={(event) => changeMode(event.target.value)} disabled={Boolean(pending)}><option value="mock">Mock Runtime Demo</option><option value="replay">Historical Trace Replay</option></select></label><p>{mode === 'mock' ? 'Synthetic execution · controlled interaction' : 'Audited source excerpts · local read-only playback'}</p><button className="text-link" onClick={showAbout}>How experiments connect <Icon name="arrow" size={14} /></button></section>
        {mode === 'replay' ? <ReplayDashboard isFocused={activeNav === 'trace'} /> : <>
          <div className="mock-notice"><Icon name="info" size={17} /><p><strong>Synthetic execution, real observability concepts.</strong> Mock events demonstrate Runtime flow. No model inference, outbound API or Benchmark results.</p><span className="notice-tag">ENGINEERING DEMO</span></div>
          {error && <div className="error-banner" role="alert"><Icon name="info" size={17} /><span>{error}</span>{runRef.current && connection === 'error' && <button onClick={() => connectRun(runRef.current)}>Reconnect</button>}<button className="dismiss-error" aria-label="Dismiss error" onClick={() => setError(null)}><Icon name="cross" size={15} /></button></div>}
          <section className="panel task-panel" aria-labelledby="task-title">
            <div className="task-heading"><div><Icon name="play" size={16} /><h2 id="task-title">Research task</h2><span className="micro-badge">EXAMPLE TASK</span></div><span className={`connection-label ${connection}`}><span className="tiny-dot" />{({ idle: 'Ready to run', connecting: 'Connecting', live: 'Stream connected', reconnecting: 'Reconnecting', complete: 'Stream complete', error: 'Stream disconnected' })[connection]}</span></div>
            <div className="task-controls"><textarea rows="2" aria-label="Research task" value={task} onChange={(event) => setTask(event.target.value)} disabled={Boolean(pending) || !canStart(state)} maxLength={500} /><button className="button primary" disabled={Boolean(pending) || !canStart(state) || !task.trim()} onClick={startRun}><Icon name="play" size={16} />{pending === 'start' ? 'Starting…' : pending === 'readback' ? 'Reading saved run…' : 'Start Agent Run'}</button></div>
            <div className="task-footer"><p>Task text is a label for a fixed research fixture; it is not interpreted by a model.</p><div className="recovery-actions"><button className="button ghost danger" disabled={!canInject} onClick={() => action('inject')} title="Queue a synthetic Tool Timeout before the Tool stage finishes"><Icon name="bolt" size={15} />{pending === 'inject' ? 'Queuing…' : queued ? 'Failure queued' : 'Inject Failure'}</button><button className="button ghost" disabled={!canRecover} onClick={() => action('recover')} title="Validate and load the saved Mock checkpoint"><Icon name="refresh" size={15} />{pending === 'recover' ? 'Restoring…' : 'Recover'}</button></div></div>
            {queued && <div className="queued-notice"><Icon name="clock" size={14} />Tool Timeout is queued. The uncommitted Mock tool operation will fail; its completed Planner step is retained.</div>}
          </section>
          <div className="run-summary"><div><span className="summary-label">RUN ID</span><code>{snapshot?.run_id || 'No active run'}</code></div><div><span className="summary-label">STEPS COMMITTED</span><strong>{completedCount}<span> / 6</span></strong></div><div><span className="summary-label">MEMORY ITEMS</span><strong>{state?.memory_size ?? 0}<span> evidence</span></strong></div><div><span className="summary-label">LLM CONNECTION</span><strong className="no-model"><Icon name="shield" size={14} />None · CPU Mock</strong></div></div>
          <div className="execution-grid"><Timeline events={events} state={state} /><StatePanel state={{ ...state, ...(lastCheckpoint?.payload?.fingerprint ? { fingerprint: lastCheckpoint.payload.fingerprint } : {}) }} runId={snapshot?.run_id} recoveryReport={snapshot?.recovery_report} executionFingerprint={snapshot?.execution_fingerprint} eventCursor={eventCursorRef.current} /></div>
          <TraceViewer events={events} isFocused={activeNav === 'trace'} />
          <FailureDashboard />
        </>}
      </>}
      <footer className="page-footer"><div><Icon name="layers" size={17} /><span>ResearchEvidence Runtime</span><span className="footer-separator">/</span><span>Independent presentation layer</span></div><span>Controlled recovery ≠ production recovery rate</span></footer>
    </main>
  </div>;
}
