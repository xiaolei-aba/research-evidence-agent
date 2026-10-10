from deepresearch.models import ResearchPlan, SubTask

def parse_plan(parsed, query_id, round_id):
    queries=parsed.get('queries')
    if not isinstance(queries,list) or not queries or not all(isinstance(q,str) and q.strip() for q in queries):
        raise ValueError('Invalid plan queries')
    return ResearchPlan(query_id,[SubTask(f'{round_id}:{i}',q) for i,q in enumerate(queries)])
