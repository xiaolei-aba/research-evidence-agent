"""Project-local JSON checkpoints. No pickle, model imports or hidden replay."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4


class CheckpointIntegrityError(RuntimeError):
    pass


class RecoveryBlocked(RuntimeError):
    pass


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def identity_hash(value):
    return sha256(canonical_bytes(value)).hexdigest()


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Directory fsync is unavailable on Windows. File fsync+replace is the
        # documented scope; this does not claim power-loss durability there.
        if os.name != 'nt':
            handle = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(handle)
            finally:
                os.close(handle)
    finally:
        if temporary.exists():
            temporary.unlink()


@dataclass
class AgentState:
    task_id: str
    goal: str
    fingerprint: str
    schema_version: int = 1
    stage: str = 'start'
    cursor: dict = field(default_factory=lambda: {'round': 0, 'subtask': 0, 'hit': 0})
    current_plan: dict | None = None
    completed_steps: list = field(default_factory=list)
    memory_state: dict = field(default_factory=dict)
    tool_history: list = field(default_factory=list)
    last_checkpoint: str | None = None
    budget: dict = field(default_factory=dict)
    deadline: dict = field(default_factory=dict)
    cost: dict = field(default_factory=dict)
    pending_intent: dict | None = None
    receipts: dict = field(default_factory=dict)
    execution: dict = field(default_factory=dict)
    terminal_result: dict | None = None


class CheckpointStore:
    """Content-addressed snapshots + atomic HEAD and exact OS lock ownership."""
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def lease(self):
        # Kernel-owned lock is released even when our worker exits abruptly.
        path = self.directory / 'runtime.lock'
        with path.open('a+b') as stream:
            # Reading an already locked byte also raises PermissionError on
            # Windows. Inspect file size without touching that locked range.
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RecoveryBlocked('checkpoint_owned_by_active_worker') from exc
            try:
                yield self
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def load(self, expected_fingerprint, task_id, goal):
        head_path = self.directory / 'HEAD.json'
        if not head_path.exists():
            # A snapshot without HEAD is uncommitted, not permission to dispatch.
            if any((self.directory / 'snapshots').glob('*.json')):
                raise CheckpointIntegrityError('checkpoint_HEAD_missing_with_existing_snapshots')
            return None
        try:
            head = json.loads(head_path.read_bytes())
            body = {k: v for k, v in head.items() if k != 'head_sha256'}
            if head.get('head_sha256') != identity_hash(body):
                raise CheckpointIntegrityError('checkpoint_HEAD_SHA_mismatch')
            digest = head['snapshot_sha256']
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise CheckpointIntegrityError('checkpoint_invalid_snapshot_name')
            raw = (self.directory / 'snapshots' / (digest + '.json')).read_bytes()
            if sha256(raw).hexdigest() != digest:
                raise CheckpointIntegrityError('checkpoint_snapshot_SHA_mismatch')
            capsule = json.loads(raw)
            if capsule['schema_version'] != 1 or capsule['fingerprint'] != expected_fingerprint:
                raise CheckpointIntegrityError('checkpoint_fingerprint_or_schema_mismatch')
            state = AgentState(**capsule['state'])
            if state.fingerprint != expected_fingerprint or state.task_id != task_id or state.goal != goal:
                raise CheckpointIntegrityError('checkpoint_task_identity_mismatch')
            state.last_checkpoint = digest
            return state
        except (KeyError, TypeError, ValueError, OSError) as exc:
            if isinstance(exc, CheckpointIntegrityError):
                raise
            raise CheckpointIntegrityError('checkpoint_invalid_or_unreadable') from exc

    def save(self, state, redactor=None):
        value = asdict(state)
        if redactor is not None:
            value = redactor(value)
        # Parent points at the previous checkpoint, avoiding a self-hash cycle.
        capsule = {'schema_version': 1, 'fingerprint': state.fingerprint,
                   'parent_snapshot_sha256': state.last_checkpoint, 'state': value}
        raw = canonical_bytes(capsule)
        digest = sha256(raw).hexdigest()
        path = self.directory / 'snapshots' / (digest + '.json')
        if path.exists():
            if sha256(path.read_bytes()).hexdigest() != digest:
                raise CheckpointIntegrityError('existing_content_addressed_checkpoint_corrupt')
        else:
            atomic_write(path, raw)
        head = {'schema_version': 1, 'snapshot_sha256': digest, 'fingerprint': state.fingerprint}
        head['head_sha256'] = identity_hash(head)
        atomic_write(self.directory / 'HEAD.json', canonical_bytes(head))
        state.last_checkpoint = digest
        return digest
