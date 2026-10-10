def metrics(run, reviewed_answer_correct=None, reviewed_supported=None):
    """Quality labels must come from independent reference/human review; None is unknown."""
    citations=run['answer']['citations']
    if reviewed_answer_correct is not None and type(reviewed_answer_correct) is not bool:
        raise ValueError('Answer label must be an independently reviewed boolean')
    if reviewed_supported is not None and len(reviewed_supported)!=len(citations): raise ValueError('One label per citation required')
    if reviewed_supported is not None and any(type(label) is not bool for label in reviewed_supported):
        raise ValueError('Citation labels must be independently reviewed booleans')
    cache=run['cache']
    return {'answer_accuracy':reviewed_answer_correct,
      'citation_accuracy':sum(reviewed_supported)/len(citations) if citations and reviewed_supported is not None else None,
      'task_success':run['answer']['status']=='completed', 'tool_calls':run['trace']['tool_calls'],
      'token_usage':run['trace']['token_usage'],'latency_ms':run['trace']['latency_ms'],
      'token_usage_reported':run['trace'].get('token_usage_reported',0),
      'usage_unreported_attempts':run['trace'].get('usage_unreported_attempts',0),
      'model_calls':run['trace'].get('model_calls',0),'http_attempts':run['trace'].get('http_attempts',0),
      'cache_hit_rate':cache['hits']/cache['lookups'] if cache['lookups'] else None}


def summarize(records):
    """Each record carries a run and optional independent review, including failed runs."""
    import math
    import statistics
    values=[]
    supported=0
    pairs=0
    citation_reviews_complete=True
    for record in records:
        review=record.get('review') or {}
        values.append(metrics(record['run'],review.get('answer_correct'),review.get('supported')))
        count=len(record['run']['answer']['citations'])
        pairs += count
        if count and review.get('supported') is None: citation_reviews_complete=False
        else: supported += sum(review.get('supported') or [])
    count=len(values)
    labelled=[v['answer_accuracy'] for v in values if v['answer_accuracy'] is not None]
    latencies=sorted(v['latency_ms'] for v in values)
    return {
        'n_runs':count,
        'answer_accuracy':sum(labelled)/count if count and len(labelled)==count else None,
        'answer_annotation_coverage':len(labelled)/count if count else None,
        'citation_accuracy':supported/pairs if pairs and citation_reviews_complete else None,
        'citation_pairs':pairs,
        'runs_without_citations':sum(not r['run']['answer']['citations'] for r in records),
        'tool_calls_mean':statistics.mean(v['tool_calls'] for v in values) if count else None,
        'token_usage_total':sum(v['token_usage'] for v in values) if count and all(v['token_usage'] is not None for v in values) else None,
        'token_usage_reported_total':sum(v['token_usage_reported'] for v in values),
        'usage_unreported_attempts':sum(v['usage_unreported_attempts'] for v in values),
        'latency_median_ms':statistics.median(latencies) if count else None,
        'latency_p95_ms':latencies[math.ceil(.95*count)-1] if count else None,
        'engineering_completion_rate':sum(v['task_success'] for v in values)/count if count else None,
    }
