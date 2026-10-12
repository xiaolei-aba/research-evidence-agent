import React from 'react';
import Icon from './Icon.jsx';

const FIXTURE = [
  { name: 'Retrieval', value: 30, description: 'Relevant evidence not found', icon: 'search', color: 'purple' },
  { name: 'Context', value: 25, description: 'Input exceeds the request budget', icon: 'code', color: 'blue' },
  { name: 'Tool', value: 20, description: 'Timeout or tool execution error', icon: 'bolt', color: 'amber' },
  { name: 'Generation', value: 25, description: 'Incomplete or invalid output', icon: 'layers', color: 'pink' },
];

export default function FailureDashboard() {
  return <section className="failure-section" aria-labelledby="failure-title"><div className="section-heading"><div><span className="eyebrow">DIAGNOSTICS</span><h2 id="failure-title">Failure analysis</h2></div><span className="fixture-label">ILLUSTRATION ONLY · 100 UNITS</span></div><div className="failure-grid">{FIXTURE.map((item) => <article className={`failure-card ${item.color}`} key={item.name}><div className="failure-card-top"><span className="failure-icon"><Icon name={item.icon} size={17} /></span><span className="mono">{item.value}<small>%</small></span></div><h3>{item.name}</h3><p>{item.description}</p><div className="failure-bar"><span style={{ width: `${item.value}%` }} /></div><span className="failure-count">{item.value} / 100 illustrative units</span></article>)}</div><p className="failure-note"><Icon name="info" size={14} />Illustration only: a fixed distribution normalized to 100; no sampled failure records. Independent of this run, not Benchmark statistics.</p></section>;
}
