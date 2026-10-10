"""Explicit historical-result references; terminal failures are never resampled."""
from pathlib import Path
from deepresearch.benchmarks.adapters import canonical_hash
from deepresearch.runtime.integrity import verify_result, file_sha256, project_path
from deepresearch.runtime.execution import TaskExecutor
import json


def observation_key(task_id, group):
    return task_id + '|' + group


def read_manifest(root, path):
    path = project_path(root, path)
    expected = path.with_suffix('.sha256').read_text(encoding='ascii').strip()
    if file_sha256(path) != expected:
        raise ValueError('continuation_manifest_sha256_mismatch')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest['role'] != 'development' or manifest['groups'] != ['B1', 'B2', 'B3']:
        raise ValueError('continuation_scope_mismatch')
    keys = [observation_key(r['task_id'], r['group']) for r in manifest['historical_results'] + manifest['pending']]
    if len(keys) != 228 or len(set(keys)) != 228:
        raise ValueError('continuation_task_coverage_mismatch')
    return manifest


class ContinuationExecutor(TaskExecutor):
    def __init__(self, root, server, run_id, *, versions, manifest_path):
        super().__init__(root, server, run_id, versions=versions)
        self.manifest = read_manifest(root, manifest_path)
        if self.manifest['new_run_id'] != run_id or self.manifest['new_versions'] != versions:
            raise ValueError('continuation_execution_identity_mismatch')
        self.historical = {observation_key(r['task_id'], r['group']): r for r in self.manifest['historical_results']}
        self.pending = {observation_key(r['task_id'], r['group']): r for r in self.manifest['pending']}
        # Audit before any server start or task access, including all old failure artifacts.
        for reference in self.historical.values():
            row = verify_result(self.root, reference['result_path'], expected_sha256=reference['result_sha256'])
            if (row['task_hash'] != reference['task_hash'] or row['task_id'] != reference['task_id']
                    or row['group'] != reference['group'] or row['run_id'] != self.manifest['old_run_id']
                    or row['configuration_hash'] != reference['configuration_hash']
                    or canonical_hash(row['configuration']) != reference['semantic_config_hash']):
                raise ValueError('historical_result_identity_mismatch')

    def execute(self, task, role, config):
        if any(k in task for k in ('gold_reference', 'gold_evidence', 'answer_type', 'task_family_id', 'label_type')):
            raise ValueError('inference_executor_received_gold')
        if role != 'development': raise ValueError('continuation_never_allows_Final')
        key = observation_key(task['task_id'], config['group'])
        reference = self.historical.get(key) or self.pending.get(key)
        if reference is None or reference['task_hash'] != task['task_hash'] or reference['semantic_config_hash'] != canonical_hash(config):
            raise ValueError('continuation_task_or_configuration_mismatch')
        if key in self.historical:
            return verify_result(self.root, reference['result_path'], expected_sha256=reference['result_sha256']), True
        return super().execute(task, role, config)
