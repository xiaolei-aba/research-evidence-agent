"""Current AI development dataset; there is deliberately no independent final set yet."""
from hashlib import sha256
import json
from pathlib import Path
from deepresearch.models import UserQuery


def load_ai_queries(root,split='development'):
    if split=='final': raise FileNotFoundError('independent_final_test_not_constructed_or_locked')
    if split!='development': raise ValueError('invalid_current_dataset_split')
    root=Path(root).resolve()
    manifest=json.loads((root/'data/manifests/ai_knowledge_v0.json').read_text(encoding='utf-8'))
    for name,digest in manifest['files'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root) or sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('ai_dataset_integrity_mismatch')
    rows=json.loads((root/'data/ai_knowledge_v0/dev_tasks.json').read_text(encoding='utf-8'))
    return [UserQuery(row['query'],row['task_id']) for row in rows]
