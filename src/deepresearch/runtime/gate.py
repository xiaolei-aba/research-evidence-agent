"""Strict automatic Final admission. Every required condition is persisted; failures never downgraded."""
from pathlib import Path
from hashlib import sha256
import json
from deepresearch.benchmarks.dataset import verify_benchmark


def final_gate(root, *, dev_complete, method_decided, registration, resume_verified, no_leakage, scorer_fixed,
               artifact_path='results/runtime_preflight/artifact_acquisition.json',output_path='results/experiment_reports/final_gate.json'):
    root = Path(root)
    verify_benchmark(root)
    status = lambda name: json.loads((root / name).read_text())
    artifacts = status(artifact_path)
    key=artifacts.get('weight_sha256','')[:12]
    smoke_path=f'results/runtime_preflight/smoke_status_{key}.json' if key else 'results/runtime_preflight/smoke_status.json'
    deadline_path=f'results/runtime_preflight/deadline_status_{key}.json' if key else 'results/runtime_preflight/deadline_status.json'
    checks = {
        'official_model_backend_sha_verified': artifacts['status'] == 'all_artifact_bytes_sha_verified',
        'license_noncommercial_scope_confirmed': 'non-commercial' in artifacts['scope'],
        'real_smoke_passed': status(smoke_path)['passed'],
        'external_deadline_and_recovery_passed': status(deadline_path)['passed'],
        'smoke_and_deadline_match_formal_model':not key or (status(smoke_path)['weight_sha256']==artifacts['weight_sha256'] and status(deadline_path)['weight_sha256']==artifacts['weight_sha256']),
        'cpu_tests_passed': status('results/runtime_preflight/cpu/latest_status.json')['passed'],
        'development_complete': bool(dev_complete), 'B4_decided_using_development_only': bool(method_decided),
        'all_formal_groups_and_budgets_registered': registration is not None,
        'task_resume_verified': bool(resume_verified), 'Gold_free_payload_verified': bool(no_leakage),
        'scorer_hashes_fixed': bool(scorer_fixed), 'frozen_benchmark_and_Gold_hashes_verified': True,
        'authorization': 'Master Prompt 4ae52b80 replaces per-stage/Final confirmation',
    }
    passed = all(value is True for key, value in checks.items() if key != 'authorization')
    report = {'passed': passed, 'checks': checks, 'registration': registration,
              'manifest_sha256': sha256((root/'data/benchmarks/final_manifest.json').read_bytes()).hexdigest(),
              'source_of_quality_labels': 'upstream human Gold mechanical scores; semantic citation support remains unreviewed'}
    target = root / output_path; target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        prior=json.loads(target.read_text())
        if prior != report: raise ValueError('existing_gate_differs; preserve and create new registration')
    else: target.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n',encoding='utf-8')
    if not passed: raise RuntimeError('final_gate_blocked:' + ','.join(k for k,v in checks.items() if v is False))
    return report
