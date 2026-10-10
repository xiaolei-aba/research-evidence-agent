"""Protocol v2 metrics. Quality requires explicit independent human labels."""
import math
import statistics


def _boolean(value,name):
    if value is not None and type(value) is not bool: raise ValueError('invalid_review_label: '+name)
    return value


def evaluate_record(run,reference,review=None,k=5,peak_vram_mib=None):
    if type(k) is not int or k<1: raise ValueError('invalid_recall_k')
    if peak_vram_mib is not None and (type(peak_vram_mib) not in (int,float) or not math.isfinite(peak_vram_mib) or peak_vram_mib<0):
        raise ValueError('invalid_vram_measurement')
    review=review or {}; trace=run['trace']; citations=run['answer']['citations']
    labels=review.get('citation_support')
    if labels is not None:
        if len(labels)!=len(citations) or any(type(v) is not bool for v in labels): raise ValueError('invalid_citation_review')
    claims=review.get('claim_support')
    if claims is not None and any(type(v) is not bool for v in claims): raise ValueError('invalid_claim_review')
    answer=_boolean(review.get('answer_correct'),'answer_correct')
    success=_boolean(review.get('task_success'),'task_success')
    if run['answer']['status']=='failed': answer=False; success=False
    groups=reference.get('required_evidence_groups',[])
    if any(not isinstance(g,list) or not g or not all(isinstance(x,str) for x in g) for g in groups):
        raise ValueError('invalid_gold_evidence_groups')
    retrieved=list(dict.fromkeys(run.get('retrieved_chunk_ids',[])))[:k]
    recall=sum(bool(set(group)&set(retrieved)) for group in groups)/len(groups) if groups else None
    return {'answer_accuracy':answer,'citation_support_rate':sum(labels)/len(labels) if labels else None,
        'citation_supported_count':sum(labels) if labels is not None else None,'citation_count':len(citations),
        'unsupported_claim_rate':sum(not value for value in claims)/len(claims) if claims else None,
        'unsupported_claim_count':sum(not value for value in claims) if claims is not None else None,
        'claim_count':len(claims) if claims is not None else None,
        'evidence_recall_at_k':recall,'recall_k':k,'task_success':success,
        'tool_calls':trace['tool_calls'],'input_tokens':trace['prompt_tokens_reported'] if trace['token_usage_complete'] else None,
        'output_tokens':trace['completion_tokens_reported'] if trace['token_usage_complete'] else None,
        'reported_input_tokens':trace['prompt_tokens_reported'],'reported_output_tokens':trace['completion_tokens_reported'],
        'latency_ms':trace['latency_ms'],'peak_vram_mib':peak_vram_mib,
        'status':run['answer']['status'],'labels_origin':review.get('origin','unreviewed')}


def aggregate_records(rows):
    def mean_complete(key):
        return statistics.mean(r[key] for r in rows) if rows and all(r[key] is not None for r in rows) else None
    latencies=sorted(r['latency_ms'] for r in rows)
    citations=sum(r['citation_count'] for r in rows)
    claims=sum(r['claim_count'] or 0 for r in rows)
    return {'n':len(rows),'answer_accuracy':mean_complete('answer_accuracy'),'task_success_rate':mean_complete('task_success'),
        'citation_support_rate':sum(r['citation_supported_count'] or 0 for r in rows)/citations
            if citations and all(r['citation_supported_count'] is not None or r['citation_count']==0 for r in rows) else None,
        'unsupported_claim_rate':sum(r['unsupported_claim_count'] or 0 for r in rows)/claims
            if claims and all(r['claim_count'] is not None for r in rows) else None,
        'evidence_recall_at_k':statistics.mean(r['evidence_recall_at_k'] for r in rows if r['evidence_recall_at_k'] is not None)
            if any(r['evidence_recall_at_k'] is not None for r in rows) else None,
        'evidence_recall_eligible_tasks':sum(r['evidence_recall_at_k'] is not None for r in rows),
        'tool_calls_per_task':mean_complete('tool_calls'),'input_tokens_mean':mean_complete('input_tokens'),
        'output_tokens_mean':mean_complete('output_tokens'),'latency_mean_ms':mean_complete('latency_ms'),
        'latency_p50_ms':statistics.median(latencies) if rows else None,
        'latency_p95_ms':latencies[math.ceil(.95*len(rows))-1] if rows else None,
        'peak_vram_mib':max(r['peak_vram_mib'] for r in rows) if rows and all(r['peak_vram_mib'] is not None for r in rows) else None,
        'runs_without_citations':sum(r['citation_count']==0 for r in rows)}
