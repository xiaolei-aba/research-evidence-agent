"""Immutable task records, inference-only subprocess, exact-handle deadlines, resume without resampling failures."""
from pathlib import Path
from hashlib import sha256
from uuid import uuid4
import json
import os
import subprocess
import time
from deepresearch.benchmarks.adapters import canonical_hash
from deepresearch.runtime.integrity import verify_result, file_sha256


def configuration(group, *, optimization=None, context=4096, seed=11):
    flags = {'planner': group != 'B1', 'memory': group not in {'B1', 'B2'},
             'critic': group not in {'B1', 'B2'}, 'replan': group not in {'B1', 'B2'},
             'cache': False, 'deduplicate': group == 'B4' and optimization == 'source_identity_dedup'}
    return {'group': group, 'flags': flags, 'optimization': optimization if group == 'B4' else None,
            'context': context, 'seed': seed, 'temperature': 0, 'max_output_tokens': 512,
            'max_input_tokens': context - 1024, 'max_tools': 18, 'max_replans': 1,
            'deadline_seconds': 120, 'retry_policy': 'one transport retry, zero answer/JSON resampling',
            'query_packaging': 'original query/claim; benchmark instructions in model system message only'}


class TaskExecutor:
    def __init__(self, root, server, run_id, *, versions):
        self.root = Path(root).resolve(); self.server = server; self.run_id = run_id; self.versions = versions
        if not run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for c in run_id):
            raise ValueError('invalid_run_id')
        self.base = self.root / 'results/real_experiments' / run_id
        self.base.mkdir(parents=True, exist_ok=True)

    def execute(self, task, role, config):
        if any(key in task for key in ('gold_reference', 'gold_evidence', 'answer_type', 'task_family_id', 'label_type')):
            raise ValueError('inference_executor_received_gold')
        config_hash = canonical_hash({'configuration': config, 'versions': self.versions})
        key = sha256(task['task_id'].encode()).hexdigest()[:20]
        directory = self.base / role / task['benchmark_id'] / config['group'] / config_hash[:16] / key
        directory.mkdir(parents=True, exist_ok=True); target = directory / 'result.json'
        if target.exists():
            row = verify_result(self.root, target)
            if row['configuration_hash'] != config_hash or row['task_hash'] != task['task_hash'] or row['task_id'] != task['task_id']:
                raise ValueError('existing_result_identity_mismatch')
            # Genuine errors and normal timeouts are completed observations, never rerun until correct.
            return row, True
        used=sum(p.stat().st_size for p in self.base.rglob('*') if p.is_file())
        if used > 2_147_483_648-8_388_608:
            raise RuntimeError('immutable_log_capacity_budget_exhausted; preserve all results')
        if self.server.guard_error: raise RuntimeError('hardware_guard_blocked:' + self.server.guard_error)
        self.server.start()
        attempt = directory / ('a-' + uuid4().hex[:16])
        if len(str(attempt/'raw_calls/call-001.json'))>=240:
            raise RuntimeError('Windows_runtime_artifact_path_too_long; refuse before inference')
        attempt.mkdir()
        packet = {'task': task, 'role': role, 'configuration': config, 'configuration_hash': config_hash, 'versions': self.versions}
        (attempt / 'inference_packet.json').write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding='utf-8')
        cursor = len(self.server.samples); start = time.monotonic()
        child = subprocess.Popen([str(self.root / '.envs/deepresearch/python.exe'), '-u', str(self.root / 'scripts/task_worker.py'),
                                  str(attempt / 'inference_packet.json'), self.server.base_url, str(attempt)], cwd=self.root,
                                 env=dict(os.environ, PYTHONUTF8='1'), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        self.server.register_worker(child)
        timed_out = False; technical_error = None
        try:
            stdout, stderr = child.communicate(timeout=config['deadline_seconds'])
        except subprocess.TimeoutExpired:
            timed_out = True; child.terminate(); self.server.terminate_owned()
            try: stdout, stderr = child.communicate(timeout=10)
            except subprocess.TimeoutExpired: child.kill(); stdout, stderr = child.communicate(timeout=5)
        except BaseException:
            child.terminate(); self.server.terminate_owned()
            try: child.wait(timeout=10)
            except subprocess.TimeoutExpired: child.kill()
            raise  # No terminal result: technical interruption may resume into a fresh attempt directory.
        finally:
            self.server.unregister_worker(child)
        elapsed = (time.monotonic() - start) * 1000
        (attempt / 'worker_stdout.txt').write_bytes(stdout); (attempt / 'worker_stderr.txt').write_bytes(stderr)
        path = attempt / 'worker_result.json'
        if path.exists() and not timed_out:
            result = json.loads(path.read_text(encoding='utf-8'))
        else:
            reason = 'external_task_deadline' if timed_out else 'worker_crashed'
            events = [json.loads(line) for line in (attempt / 'events.jsonl').read_text(encoding='utf-8').splitlines()] if (attempt / 'events.jsonl').exists() else []
            calls = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((attempt / 'raw_calls').glob('*.json'))]
            usages = [call.get('raw_response', {}).get('usage') for call in calls]
            complete = bool(calls) and all(isinstance(u, dict) and 'total_tokens' in u for u in usages)
            reported_in = sum(u.get('prompt_tokens', 0) for u in usages if u); reported_out = sum(u.get('completion_tokens', 0) for u in usages if u)
            trace = {'query_id': task['task_id'], 'events': events, 'model_calls': len(calls), 'http_attempts': len(calls),
                     'tool_calls': None, 'token_usage_complete': complete, 'prompt_tokens_reported': reported_in,
                     'completion_tokens_reported': reported_out, 'token_usage': reported_in + reported_out if complete else None,
                     'latency_ms': elapsed, 'error': reason, 'versions': self.versions}
            result = {'task_id': task['task_id'], 'task_hash': task['task_hash'], 'configuration_hash': config_hash,
                      'mock_used': False, 'prediction': {'status': 'failed'}, 'run': {'answer': {'query_id': task['task_id'],
                      'text': '', 'citations': [], 'status': 'failed', 'error': reason}, 'trace': trace,
                      'evidence': [], 'retrieved_chunk_ids': []}}
        result['run']['trace']['agent_controller_latency_ms'] = result['run']['trace'].get('latency_ms')
        result['run']['trace']['latency_ms'] = elapsed
        samples = self.server.samples[cursor:]
        result['resource_samples'] = samples
        result['minimum_available_ram_bytes'] = min((s['ram_available_bytes'] for s in samples), default=None)
        result['backend_execution_fingerprint'] = self.versions.get('backend_execution_fingerprint')
        result.update(run_id=self.run_id, role=role, group=config['group'], configuration=config,
                      peak_device_used_mib=max((s['memory_used_mib'] for s in samples), default=None),
                      peak_temperature_c=max((s['temperature_c'] for s in samples), default=None),
                      latency_scope='parent task launch through worker exit, including corpus setup and native token RPCs',
                      technical_attempt_path=attempt.relative_to(self.root).as_posix())
        if result.get('task_hash') != task['task_hash'] or result.get('configuration_hash') != config_hash:
            raise ValueError('worker_result_identity_mismatch')
        hashes = {p.relative_to(self.root).as_posix(): sha256(p.read_bytes()).hexdigest() for p in attempt.rglob('*') if p.is_file()}
        result['artifact_hashes'] = hashes
        if target.exists(): raise ValueError('never_overwrite_completed_result')
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        target.with_suffix('.sha256').write_text(file_sha256(target) + '\n', encoding='ascii')
        return result, False
