"""Fail-closed checks for immutable terminal results and project-local artifacts."""
from hashlib import sha256
from pathlib import Path
import json


def file_sha256(path):
    value = sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            value.update(block)
    return value.hexdigest()


def project_path(root, name):
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError('artifact_path_outside_project')
    return path


def verify_result(root, path, *, expected_sha256=None):
    path = project_path(root, path)
    sidecar = path.with_suffix('.sha256')
    digest = file_sha256(path)
    if expected_sha256 is None:
        if not sidecar.exists():
            raise ValueError('terminal_result_missing_trusted_sha256')
        expected_sha256 = sidecar.read_text(encoding='ascii').strip()
    if digest != expected_sha256:
        raise ValueError('terminal_result_sha256_mismatch')
    if sidecar.exists() and sidecar.read_text(encoding='ascii').strip() != digest:
        raise ValueError('terminal_result_sidecar_sha256_mismatch')
    row = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(row.get('artifact_hashes'), dict):
        raise ValueError('terminal_result_missing_artifact_hashes')
    for name, expected in row['artifact_hashes'].items():
        if file_sha256(project_path(root, name)) != expected:
            raise ValueError('terminal_artifact_sha256_mismatch:' + name)
    return row
