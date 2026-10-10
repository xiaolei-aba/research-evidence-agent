"""Frozen public-benchmark loading; inference defaults never return references/labels/evidence."""
from pathlib import Path
from hashlib import sha256
import json
from deepresearch.benchmarks.adapters import canonical_hash


def verify_benchmark(root):
    root = Path(root).resolve()
    manifest = json.loads((root / 'data/benchmarks/final_manifest.json').read_text(encoding='utf-8'))
    if not manifest['status'].startswith('content_frozen'):
        raise ValueError('benchmark_not_frozen')
    resolution={};history=root/'data/benchmarks/history_resolution.json'
    if history.exists():
        overlay=json.loads(history.read_text(encoding='utf-8'))
        if overlay['manifest_sha256']!=sha256((root/'data/benchmarks/final_manifest.json').read_bytes()).hexdigest():
            raise ValueError('benchmark_history_binding_mismatch')
        resolution=overlay['documentation_only']
    for name, expected in {**manifest['files'], **manifest['local_cache_files']}.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError('benchmark_path_escape')
        if sha256(path.read_bytes()).hexdigest() != expected:
            record=resolution.get(name)
            if not name.startswith('docs/') or record is None or record['sha256']!=expected:
                raise ValueError('benchmark_hash_mismatch: ' + name)
            archived=(root/record['archive_path']).resolve()
            if not archived.is_relative_to(root/'docs/history') or sha256(archived.read_bytes()).hexdigest()!=expected:
                raise ValueError('benchmark_documentation_archive_mismatch')
    return manifest


def load_tasks(root, benchmark, role='development', *, final_authorized=False, offline_scoring=False):
    if benchmark not in {'qasper', 'scifact'} or role not in {'development', 'final'}:
        raise ValueError('invalid_benchmark_or_role')
    if role == 'final' and not final_authorized:
        raise PermissionError('final_requires_registered_evaluation_authorization')
    root = Path(root); verify_benchmark(root)
    rows = json.loads((root / f'data/benchmarks/private_cache/selected/{benchmark}_{role}.json').read_text(encoding='utf-8'))
    for row in rows:
        if canonical_hash({k: v for k, v in row.items() if k != 'task_hash'}) != row['task_hash']:
            raise ValueError('benchmark_task_hash_mismatch')
    if offline_scoring:
        return rows
    return [{key: row[key] for key in ('benchmark_id', 'task_id', 'task_hash', 'question_or_claim')}
            | {'source_doc_ids': row['source_doc_ids'] if benchmark == 'qasper' else []} for row in rows]
