"""CPU-only read-only trace analysis; never invokes providers or Agent execution."""
from pathlib import Path
from collections import defaultdict
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from deepresearch.runtime.trace_analysis import analyze_trace, TraceSummary, file_sha256, safe_value, write_dashboard


def checked_path(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('source_outside_declared_root')
    if any(part.casefold() in {'final', 'test'} for part in path.parts):
        raise ValueError('Final_Test_trace_input_forbidden')
    return path


def verified_json(path, expected=None):
    actual = file_sha256(path)
    if expected is not None and actual != expected:
        raise ValueError('source_sha_mismatch:' + str(path))
    sidecar = path.with_suffix('.sha256')
    if not sidecar.exists():
        sidecar = Path(str(path) + '.sha256')
    if sidecar.exists():
        registered = sidecar.read_text(encoding='ascii').strip()
        if actual != registered:
            raise ValueError('source_sha_sidecar_mismatch:' + str(path))
    record = json.loads(path.read_text(encoding='utf-8'))
    if file_sha256(path) != actual:
        raise ValueError('source_changed_during_read:' + str(path))
    return record, actual, 'registered_sha_verified' if expected is not None or sidecar.exists() else 'observed_sha_only'


def input_records(args):
    source_root = Path(args.source_root).resolve()
    for name in args.dev_metrics or []:
        path = checked_path(source_root, name)
        metrics, _, _ = verified_json(path)
        if not isinstance(metrics, list):
            raise ValueError('dev_metrics_must_be_array')
        for metric in metrics:
            if metric.get('role') != 'development':
                raise ValueError('Non_Dev_metric_forbidden')
            result_path = checked_path(source_root, metric['result_path'])
            record, actual, integrity = verified_json(result_path, metric['result_sha256'])
            if any(record.get(k) != metric[k] for k in ('task_id', 'task_hash', 'group', 'role')):
                raise ValueError('metric_result_identity_mismatch')
            # Frozen score rows are displayed as reported only; no scorer edits/regrading.
            record['quality'] = metric.get('quality', {})
            record['benchmark'] = metric.get('benchmark', 'unknown')
            yield record, result_path, actual, integrity
    for name in args.records or []:
        path = checked_path(source_root, name)
        record, actual, integrity = verified_json(path)
        if not isinstance(record, dict):
            raise ValueError('record_must_be_object')
        yield record, path, actual, integrity


def prepare_evidence(record, source_root):
    raw_calls = []
    for name, expected in sorted(record.get('artifact_hashes', {}).items()):
        path = checked_path(source_root, name)
        if file_sha256(path) != expected:
            raise ValueError('artifact_sha_mismatch:' + name)
        if '/raw_calls/' not in name.replace('\\', '/') or not name.endswith('.json'):
            continue
        call, actual, _ = verified_json(path, expected)
        response = call.get('raw_response') or {}
        choices = response.get('choices') or []
        raw_calls.append({'operation': call.get('operation'), 'status': call.get('status'),
                          'finish_reason': choices[-1].get('finish_reason') if choices else None,
                          'usage': response.get('usage'), 'error': call.get('error'),
                          'source': str(path), 'sha256': actual, 'local_uri': path.as_uri()})
    record['raw_call_evidence'] = raw_calls
    return record


def complete_events(record, source_root):
    trace = (record.get('run', record).get('trace') or {})
    name = trace.get('event_log_path')
    if not name:
        return None, None
    path = checked_path(source_root, name)
    actual = file_sha256(path)
    expected = record.get('artifact_hashes', {}).get(name, trace.get('event_log_sha256'))
    if expected is not None and actual != expected:
        raise ValueError('event_log_sha_mismatch')
    source = {'path': str(path), 'sha256': actual, 'local_uri': path.as_uri(),
              'integrity': 'registered_sha_verified' if expected is not None else 'observed_sha_only'}
    def stream():
        with path.open(encoding='utf-8') as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)
        if file_sha256(path) != actual:
            raise ValueError('event_log_changed_during_analysis')
    return stream(), source


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', default=str(ROOT))
    parser.add_argument('--dev-metrics', nargs='*', help='Frozen Dev task metric JSON arrays; verifies result/artifact SHA')
    parser.add_argument('--records', nargs='*', help='Persisted single-task result/engineering receipt JSON files')
    parser.add_argument('--run-kind', required=True, choices=('real_dev', 'engineering'))
    parser.add_argument('--recovery-manifest', help='Engineering fault registry JSON with unique eligible_episode_ids')
    parser.add_argument('--output', required=True, help='New output directory; must not already exist')
    args = parser.parse_args(argv)
    if not args.dev_metrics and not args.records:
        parser.error('Provide --dev-metrics or --records')
    if args.dev_metrics and args.run_kind != 'real_dev':
        parser.error('Dev metrics require real_dev, never relabel them engineering')
    if args.recovery_manifest and args.run_kind != 'engineering':
        parser.error('Recovery fault manifest belongs to engineering metrics, not real Dev quality')
    output = Path(args.output).resolve()
    if not output.is_relative_to(ROOT) or any(p.casefold() in {'final', 'test'} for p in output.parts):
        raise ValueError('output_must_be_new_runtime_report_directory')
    output.mkdir(parents=True, exist_ok=False)
    source_root = Path(args.source_root).resolve()
    registry_ref = None
    expected_episodes = None
    if args.recovery_manifest:
        registry_path = checked_path(source_root, args.recovery_manifest)
        registry, registry_sha, registry_integrity = verified_json(registry_path)
        expected_episodes = registry['eligible_episode_ids']
        if (not isinstance(expected_episodes, list) or not all(isinstance(e, str) and e for e in expected_episodes)
                or len(set(expected_episodes)) != len(expected_episodes)):
            raise ValueError('invalid_recovery_manifest_episode_ids')
        registry_ref = {'path': str(registry_path), 'sha256': registry_sha, 'integrity': registry_integrity}
    summary = TraceSummary(expected_recovery_episode_ids=expected_episodes)
    arms = defaultdict(TraceSummary)
    rows_path = output / 'trace_rows.jsonl'
    input_count = 0
    try:
        with rows_path.open('x', encoding='utf-8', newline='\n') as handle:
            for record, path, actual, integrity in input_records(args):
                role = record.get('role', record.get('split'))
                if role in {'final', 'test'}:
                    raise ValueError('Final_Test_records_forbidden')
                if args.run_kind == 'real_dev' and role != 'development':
                    raise ValueError('real_dev_requires_development_role')
                if args.run_kind == 'engineering' and record.get('mock_used') is False:
                    raise ValueError('do_not_relabel_real_model_observations_as_engineering')
                record = prepare_evidence(record, source_root)
                events, event_source = complete_events(record, source_root)
                ref = {'path': str(path), 'sha256': actual, 'local_uri': path.as_uri(),
                       'integrity': integrity, 'event_log': event_source}
                row = analyze_trace(record, ref, event_iterable=events, run_kind=args.run_kind)
                if file_sha256(path) != actual:
                    raise ValueError('result_changed_during_analysis')
                summary.add(row)
                arms[(row['run_kind'], row['arm'], row['benchmark'])].add(row)
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
                input_count += 1
        result = safe_value(summary.result() | {'by_arm_benchmark': [dict(run_kind=k[0], arm=k[1], benchmark=k[2], summary=v.result())
                                                                    for k, v in arms.items()],
                                               'source_root': str(source_root), 'all_input_observations': input_count,
                                               'recovery_manifest_ref': registry_ref,
                                               'policy': 'Read-only persisted Dev/engineering results; no model calls, no prediction repair, no semantic score attribution'})
        (output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
        write_dashboard(rows_path, output / 'failure_dashboard.html', result)
        for name in ('trace_rows.jsonl', 'summary.json', 'failure_dashboard.html'):
            path = output / name
            Path(str(path) + '.sha256').write_text(file_sha256(path) + '\n', encoding='ascii')
        print(json.dumps({'output': str(output), 'n': input_count, 'run_kind': args.run_kind, 'status': 'complete'}))
    except BaseException as exc:
        (output / 'analysis_blocked.json').write_text(json.dumps(safe_value({'status': 'blocked', 'observations_written': input_count,
                                                                          'error': str(exc), 'partial_rows_are_not_final_summary': True}),
                                                          ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        raise


if __name__ == '__main__':
    main()
