"""Read-only, fail-closed loading of the released allowlisted historical events.

This module never executes an actor. Public IDs select entries in a
sealed manifest; callers cannot supply a file path or arbitrary event metadata.
"""
import hashlib
import json
import math
import re
from pathlib import Path

from .mock_agent import DemoError


COMPONENTS = {"Planner", "Tool", "Memory", "Context", "Checkpoint", "Answer", "Critic", "Runtime"}
STATUSES = {"running", "success", "failed", "recovered", "unknown"}
EVENTS = {
    "task_started", "context_unchanged", "context_selected", "context_rejected",
    "token_budget_requested", "token_budget_accepted", "token_budget_rejected",
    "model_completed", "model_failed", "tool_attempt_started", "tool_attempt_completed",
    "tool_completed", "evidence_selected", "evidence_stored", "task_stage_started",
    "task_stage_completed", "task_failed", "task_completed", "checkpoint_saved",
    "process_exit_injected", "checkpoint_restored", "recovery_verified", "citation_validated",
}
STAGES = {"start", "plan", "task", "search", "read", "select", "answer", "critic", "decide", "finish", "complete", "context", "recovery", "unknown"}
ERROR_CATEGORIES = {"GenerationLengthFailure", "incomplete_or_refused_completion", "ProcessRestart", "unknown"}
NUMBERS = {"duration_ms", "before_tokens", "after_tokens", "native_input_tokens", "output_tokens",
           "requested_reservation_tokens", "reserved_tokens", "max_input_tokens", "selected_count",
           "memory_size", "completed_steps_count", "checkpoint_number", "source_event_index",
           "source_event_count", "physical_attempt", "document_count", "replayed_steps",
           "lost_steps", "duplicate_tool_calls", "input_tokens", "verified_snapshot_count"}
FLAGS = {"checkpoint_verified", "counter_is_exact", "recovery_observed", "mechanism_checks_passed", "real_llm_used"}
TEXT_ENUMS = {
    "stage": STAGES,
    "error_category": ERROR_CATEGORIES,
    "terminal_status": {"completed", "failed", "insufficient_evidence"},
    "timing_basis": {"unrecorded", "operation_duration_only"},
    "source_record": {"agent_trace", "checkpoint_snapshot", "failure_marker", "recovery_report"},
    "phase": {"before_failure", "after_recovery"},
}
ENTRY_KEYS = {"public_id", "title", "source_kind", "file", "sha256", "event_count", "recovery_observed", "known", "unknown"}
KINDS = {"real_model_dev_history", "engineering_mock_history"}
TITLES = {"Real model Dev: generation length failure", "Engineering mock: committed evidence process restart"}
KNOWN = {"record_order", "operation_durations", "terminal_failure_category", "checkpoint_chain_verified", "cpu_mock_process_restart", "no_completed_tool_replay"}
UNKNOWN = {"event_wall_clock_timestamps", "semantic_answer_correctness", "production_recovery_rate", "real_model_recovery"}
ID_RE = re.compile(r"^[a-z][a-z0-9_\-]{0,63}$")
FILE_RE = re.compile(r"^[a-z][a-z0-9_\-]{0,63}\.jsonl$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _integer(value, minimum=0, maximum=1000000):
    return type(value) is int and minimum <= value <= maximum


def validate_event(event):
    if not isinstance(event, dict) or set(event) != {"timestamp", "component", "event", "status", "metadata"}:
        raise ValueError("event fields")
    timestamp = event["timestamp"]
    if timestamp is not None and (type(timestamp) not in {int, float} or not math.isfinite(timestamp) or timestamp < 0):
        raise ValueError("event timestamp")
    if event["component"] not in COMPONENTS or event["event"] not in EVENTS or event["status"] not in STATUSES:
        raise ValueError("event enum")
    metadata = event["metadata"]
    if not isinstance(metadata, dict) or "stage" not in metadata or len(metadata) > 24:
        raise ValueError("metadata schema")
    for key, value in metadata.items():
        if key in NUMBERS:
            if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1000000000:
                raise ValueError("metadata number")
        elif key in FLAGS:
            if type(value) is not bool:
                raise ValueError("metadata flag")
        elif key in TEXT_ENUMS:
            if value not in TEXT_ENUMS[key]:
                raise ValueError("metadata enum")
        else:
            raise ValueError("unapproved metadata")
    return event


class ReplayCatalog:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def _read(self):
        try:
            path = self.root / "trace_manifest.json"
            content = path.read_bytes()
            if len(content) > 32768:
                raise ValueError("manifest size")
            expected = (self.root / "trace_manifest.sha256").read_text(encoding="ascii").strip()
            if not SHA_RE.fullmatch(expected) or hashlib.sha256(content).hexdigest() != expected:
                raise ValueError("manifest SHA")
            manifest = json.loads(content)
            if set(manifest) != {"schema_version", "notice", "timestamp_policy", "traces"} or manifest["schema_version"] != 1:
                raise ValueError("manifest schema")
            if manifest["notice"] != "Sanitized historical events. Playback is read-only and makes no model calls.":
                raise ValueError("manifest notice")
            if manifest["timestamp_policy"] != "null_when_unrecorded; duration_ms_is_operation_duration_not_a_timeline":
                raise ValueError("manifest timing")
            entries = manifest["traces"]
            if not isinstance(entries, list) or not 1 <= len(entries) <= 8:
                raise ValueError("catalog size")
            ids, filenames = set(), set()
            loaded = []
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
                    raise ValueError("entry fields")
                public_id, filename = entry["public_id"], entry["file"]
                if not isinstance(public_id, str) or not ID_RE.fullmatch(public_id) or public_id in ids:
                    raise ValueError("public ID")
                if not isinstance(filename, str) or not FILE_RE.fullmatch(filename) or filename in filenames:
                    raise ValueError("trace filename")
                ids.add(public_id); filenames.add(filename)
                if entry["source_kind"] not in KINDS or entry["title"] not in TITLES:
                    raise ValueError("entry enum")
                if not isinstance(entry["sha256"], str) or not SHA_RE.fullmatch(entry["sha256"]) or not _integer(entry["event_count"], 1, 256):
                    raise ValueError("entry integrity fields")
                if type(entry["recovery_observed"]) is not bool:
                    raise ValueError("entry recovery")
                if entry["source_kind"] == "real_model_dev_history" and entry["recovery_observed"]:
                    raise ValueError("formal recovery was not observed")
                for key, allowed in [("known", KNOWN), ("unknown", UNKNOWN)]:
                    values = entry[key]
                    if not isinstance(values, list) or len(values) != len(set(values)) or any(value not in allowed for value in values):
                        raise ValueError("entry disclosure")
                file_path = (self.root / filename).resolve()
                if file_path.parent != self.root:
                    raise ValueError("trace path escape")
                raw = file_path.read_bytes()
                if len(raw) > 262144 or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                    raise ValueError("trace SHA or size")
                events = [validate_event(json.loads(line)) for line in raw.decode("utf-8").splitlines() if line.strip()]
                if len(events) != entry["event_count"]:
                    raise ValueError("event count")
                if entry["source_kind"] == "real_model_dev_history" and any(e["event"] in {"checkpoint_restored", "recovery_verified", "process_exit_injected"} for e in events):
                    raise ValueError("fabricated formal recovery")
                loaded.append((entry, events))
            return loaded
        except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
            raise DemoError(503, "replay_integrity_unavailable", "Historical replay unavailable: released manifest, schema or SHA validation failed. No actor was executed.") from exc

    def list(self):
        return {"available": True, "readonly": True, "model_calls": False,
                "replays": [{key: value for key, value in entry.items() if key != "file"} for entry, _ in self._read()]}

    def get(self, public_id: str):
        if not ID_RE.fullmatch(public_id):
            raise DemoError(404, "replay_not_found", "Unknown released trace ID.")
        for entry, events in self._read():
            if entry["public_id"] == public_id:
                return {"manifest": {key: value for key, value in entry.items() if key != "file"},
                        "events": events, "readonly": True, "model_calls": False}
        raise DemoError(404, "replay_not_found", "Unknown released trace ID.")
