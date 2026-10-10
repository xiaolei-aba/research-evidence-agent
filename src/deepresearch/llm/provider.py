from typing import Protocol
import json
class LLMProvider(Protocol):
    def complete(self, operation: str, payload: dict) -> str: ...
class MockProvider:
    is_mock = True
    """Deterministic protocol simulator; no network and no token estimator."""
    def __init__(self, scenarios=None):
        self.scenarios = {k:list(v) for k,v in (scenarios or {}).items()}
    def complete(self, operation, payload):
        queue = self.scenarios.get(operation, [])
        if queue:
            value = queue.pop(0)
            if isinstance(value, Exception): raise value
            return value
        if operation in ('plan','replan'):
            queries = payload.get('missing_queries') or payload['query'].split(';')
            return json.dumps({'queries':[q.strip() for q in queries if q.strip()]})
        if operation == 'critic':
            return json.dumps({'sufficient':bool(payload['evidence']) and not payload['missing_queries'] and not payload['conflicts'],
                'reason':'Offline coverage and exact fixture fact checks only',
                'missing_queries':payload['missing_queries'], 'conflicts':payload['conflicts']})
        if operation == 'answer':
            return json.dumps({'text':'\n'.join(e['content'] for e in payload['evidence']),
                'citations':[{'evidence_id':e['evidence_id'],'claim':e['content']} for e in payload['evidence']]})
        raise ValueError('Unknown operation: '+operation)
