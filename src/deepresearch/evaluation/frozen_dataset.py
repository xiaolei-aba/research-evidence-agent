"""Frozen original-source dataset. Final execution requires explicit authorization and review acknowledgement."""
from hashlib import sha256
import json
from pathlib import Path
from deepresearch.models import UserQuery


def verify_frozen_dataset(root,manifest_path='data/manifests/raw_ai_v1_final.json'):
    root=Path(root).resolve()
    manifest=json.loads((root/manifest_path).read_text(encoding='utf-8'))
    if manifest['status']!='content_frozen': raise ValueError('final_content_not_frozen')
    # New phases may update working documentation. The immutable old manifest is not edited;
    # only explicitly recorded documentation snapshots may resolve its original byte hashes.
    resolution={}
    history=root/'data/manifests/raw_ai_v1_history_resolution.json'
    if manifest_path=='data/manifests/raw_ai_v1_final.json' and history.exists():
        overlay=json.loads(history.read_text(encoding='utf-8'))
        if overlay['frozen_manifest_sha256']!=sha256((root/manifest_path).read_bytes()).hexdigest():
            raise ValueError('historical_manifest_binding_mismatch')
        resolution=overlay['documentation_only']
    for name,expected in manifest['files'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root): raise ValueError('frozen_path_outside_project')
        if sha256(path.read_bytes()).hexdigest()!=expected:
            record=resolution.get(name)
            if not name.startswith('docs/') or record is None or record['sha256']!=expected:
                raise ValueError('frozen_hash_mismatch: '+name)
            archived=(root/record['archive_path']).resolve()
            if not archived.is_relative_to(root/'docs/history') or sha256(archived.read_bytes()).hexdigest()!=expected:
                raise ValueError('frozen_documentation_archive_mismatch: '+name)
    return manifest


def load_frozen_queries(root,split='development',*,final_authorized=False,acknowledge_ai_only_gold=False):
    if split not in ('development','final'): raise ValueError('invalid_frozen_split')
    if split=='final' and not final_authorized: raise PermissionError('final_is_not_for_development_or_prompt_tuning')
    manifest=verify_frozen_dataset(root)
    if split=='final' and manifest['review']['human_reviewers']==0 and not acknowledge_ai_only_gold:
        raise PermissionError('human_gold_review_missing; explicit acknowledgement required')
    rows=json.loads((Path(root)/f'data/raw_ai_v1/tasks/{split}.json').read_text(encoding='utf-8'))
    # Never load reference answers, task types, gold chunks or scoring rubric into an Agent input.
    return [UserQuery(row['query'],row['task_id']) for row in rows]
