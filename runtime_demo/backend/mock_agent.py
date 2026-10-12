"""Independent synthetic presentation actor. No LLM, tools, GPU or outbound I/O.

This does not execute StatefulResearchAgent. Checkpoints and receipts implement
only the bounded UI fixture, and are not evidence of real model recovery.
"""
import asyncio
import copy
import hashlib
import json
import os
import re
from collections import OrderedDict
from pathlib import Path
from uuid import uuid4

from .event_stream import EventJournal
from .schemas import AgentState, RecoveryReport

MOCK_NOTICE = "Mock events demonstrate Runtime flow; not real model inference or Benchmark results."
FAILURE_FIXTURE = {"Retrieval": 30, "Context": 25, "Tool": 20, "Generation": 25,
                   "label": "Mock fixture; not Benchmark statistics", "denominator": 100}
EVIDENCE = [
    {"evidence_id": "E1", "source": "Synthetic method note", "content": "The proposed method uses evidence selection before answering.", "relevance": 0.96},
    {"evidence_id": "E2", "source": "Synthetic comparison note", "content": "A hypothetical comparison reports an improvement, but this demo contains no measured experiment.", "relevance": 0.91},
    {"evidence_id": "E3", "source": "Synthetic unrelated note", "content": "An unrelated implementation detail has lower relevance.", "relevance": 0.18},
]


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def atomic_write(path: Path, data: bytes):
    # A 64-character content SHA is already a long filename. Repeating it in
    # the temporary name can exceed Windows MAX_PATH in an otherwise valid
    # checkout. The sibling remains unique and os.replace stays on one volume.
    temporary = path.with_name(".tmp-" + uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class DemoError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


class DemoRun:
    def __init__(self, run_id: str, task: str, directory: Path):
        self.run_id, self.task, self.directory = run_id, task, directory
        directory.mkdir(parents=True, exist_ok=False)
        self.identity = hashlib.sha256(canonical({"schema": 1, "run_id": run_id, "task": task, "kind": "engineering_mock"})).hexdigest()
        self.state = AgentState(task_id=run_id, current_stage="Planner")
        self.lock = asyncio.Lock()
        self.journal = EventJournal(directory, self.lock)
        self.cursor = "Planner"
        self.memory: list[dict] = []
        self.receipts: list[dict] = []
        self.inject_requested = False
        self.actor_active = True
        self.stream_closed = False
        self.recovered = False
        self.recovering = False
        self.actor: asyncio.Task | None = None
        self.failure_type: str | None = None
        self.recovery_report: dict | None = None
        self.checkpoint_before_failure: str | None = None
        self.execution_counts: dict[str, int] = {}
        self.recovery_committed_steps: list[str] = []

    def snapshot(self) -> dict:
        return {"run_id": self.run_id, "task_id": self.run_id, "task": self.task,
                "execution_fingerprint": self.identity,
                "fingerprint_scope": "Synthetic presentation identity; not a model/backend execution fingerprint",
                "state": self.state.model_dump(), "events": copy.deepcopy(list(self.journal.events)),
                "can_inject": self.actor_active and not self.recovered and self.state.status == "RUNNING" and self.state.current_stage in {"Planner", "Tool"} and "tool" not in self.state.completed_steps and not self.inject_requested,
                "can_recover": self.state.status == "FAILED" and self.failure_type == "Tool Timeout" and not self.actor_active and not self.recovering,
                "recovery_report": copy.deepcopy(self.recovery_report),
                "failure_fixture": FAILURE_FIXTURE, "engineering_mock": True, "notice": MOCK_NOTICE}

    def emit(self, component: str, stage: str, status: str, **payload):
        return self.journal.append(component, stage, status, {**payload, "state": self.state.model_dump()})

    def checkpoint(self, cursor: str):
        self.cursor = cursor
        document = {"schema_version": 1, "kind": "engineering_mock", "identity": self.identity,
                    "run_id": self.run_id, "task": self.task, "cursor": cursor,
                    "state": self.state.model_dump(), "memory": self.memory, "receipts": self.receipts,
                    "execution_counts": self.execution_counts}
        content = canonical(document)
        digest = hashlib.sha256(content).hexdigest()
        checkpoint_dir = self.directory / "checkpoints"
        checkpoint_dir.mkdir(exist_ok=True)
        target = checkpoint_dir / (digest + ".json")
        if target.exists() and target.read_bytes() != content:
            raise DemoError(409, "checkpoint_integrity_error", "Existing content-addressed checkpoint differs.")
        if not target.exists():
            atomic_write(target, content)
        atomic_write(target.with_suffix(".json.sha256"), (digest + "\n").encode("ascii"))
        atomic_write(self.directory / "HEAD.json", canonical({"sha256": digest, "identity": self.identity, "run_id": self.run_id}))
        self.state.checkpoint_id = digest
        self.emit("Checkpoint", cursor, "success", event="checkpoint_saved", checkpoint_id=digest, sha256=digest,
                  durability="file fsync + atomic replace; no Windows power-loss guarantee")
        return digest

    def load_checkpoint(self):
        try:
            head = json.loads((self.directory / "HEAD.json").read_text(encoding="utf-8"))
            digest = head["sha256"]
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("invalid SHA")
            if digest != self.state.checkpoint_id or head["identity"] != self.identity or head["run_id"] != self.run_id:
                raise ValueError("HEAD identity differs")
            path = self.directory / "checkpoints" / (digest + ".json")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != digest or path.with_suffix(".json.sha256").read_text().strip() != digest:
                raise ValueError("SHA differs")
            document = json.loads(content)
            if document["identity"] != self.identity or document["run_id"] != self.run_id or document["task"] != self.task or document["kind"] != "engineering_mock" or document["schema_version"] != 1:
                raise ValueError("checkpoint identity differs")
            state = AgentState.model_validate(document["state"])
            if document["cursor"] != "Tool" or state.current_stage != "Tool" or state.status != "FAILED":
                raise ValueError("checkpoint not eligible for the synthetic recovery fixture")
            return document, digest
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DemoError(409, "checkpoint_integrity_error", "Recovery blocked: checkpoint SHA or task identity failed validation.") from exc


class MockRuntime:
    def __init__(self, data_root: Path, step_delay: float = 1.7, max_runs: int = 32):
        self.data_root = Path(data_root).resolve()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.step_delay, self.max_runs = step_delay, max_runs
        self.runs: OrderedDict[str, DemoRun] = OrderedDict()
        self.manager_lock = asyncio.Lock()
        self.total_runs = sum(1 for path in self.data_root.iterdir() if path.is_dir() and re.fullmatch(r"demo_[0-9a-f]{12}", path.name))

    def get(self, run_id: str) -> DemoRun:
        try:
            return self.runs[run_id]
        except KeyError as exc:
            raise DemoError(404, "run_not_found", "Run does not exist in this presentation session.") from exc

    async def start(self, task: str):
        async with self.manager_lock:
            if self.total_runs >= self.max_runs:
                # Total persisted runs are bounded across restarts. Reject before
                # creating a directory; completed traces are never auto-deleted.
                raise DemoError(429, "run_capacity", "Presentation storage limit reached. No new run was created; use read-only historical replay.")
            run_id = "demo_" + uuid4().hex[:12]
            run = DemoRun(run_id, task, self.data_root / run_id)
            self.total_runs += 1
            self.runs[run_id] = run
            async with run.lock:
                run.emit("Runtime", "task_start", "running", event="task_started", goal=task)
            run.actor = asyncio.create_task(self._actor(run, "Planner"))
            return run

    async def inject(self, run: DemoRun):
        async with run.lock:
            if not run.snapshot()["can_inject"]:
                raise DemoError(409, "failure_injection_unavailable", "Tool Timeout can only be queued before the Tool stage finishes, once per run.")
            run.inject_requested = True
            run.emit("Runtime", run.state.current_stage, "running", event="failure_queued", failure_type="Tool Timeout",
                     explanation="Synthetic timeout will occur in the uncommitted Tool operation.")
            return run.snapshot()

    async def recover(self, run: DemoRun):
        async with run.lock:
            if not run.snapshot()["can_recover"]:
                raise DemoError(409, "recovery_unavailable", "Only the completed synthetic Tool Timeout can be recovered once.")
            document, digest = run.load_checkpoint()  # Fail closed before changing state or spawning an actor.
            run.recovering = True
            run.state = AgentState.model_validate(document["state"])
            run.state.checkpoint_id = digest
            run.memory = copy.deepcopy(document["memory"])
            run.receipts = copy.deepcopy(document["receipts"])
            run.execution_counts = copy.deepcopy(document["execution_counts"])
            run.recovery_committed_steps = list(run.state.completed_steps)
            run.state.status = "RECOVERED"
            run.recovered = True
            run.stream_closed = False
            run.actor_active = True
            run.inject_requested = False
            run.recovery_report = RecoveryReport(task_id=run.run_id, checkpoint_before_failure=run.checkpoint_before_failure, loaded_checkpoint_id=digest).model_dump()
            run.emit("Checkpoint", "recovery", "recovered", event="checkpoint_loaded", checkpoint_id=digest, sha_validated=True,
                     completed_steps_restored=list(run.state.completed_steps),
                     resume_policy="Retry only the failed uncommitted synthetic Tool operation. Planner is not re-executed.")
            run.actor = asyncio.create_task(self._actor(run, "Tool"))
            return run.snapshot()

    async def _actor(self, run: DemoRun, cursor: str):
        stages = ["Planner", "Tool", "Memory", "Context", "Answer", "Critic"]
        try:
            for stage in stages[stages.index(cursor):]:
                async with run.lock:
                    run.state.current_stage = stage
                    run.emit(stage, stage, "running", event="stage_started")
                # Tool has a larger presentation window, not an actual networking timeout.
                await asyncio.sleep(self.step_delay * (2 if stage == "Tool" else 1))
                async with run.lock:
                    if stage == "Planner":
                        run.execution_counts["planner"] = run.execution_counts.get("planner", 0) + 1
                        run.state.completed_steps.append("planner")
                        run.state.token_budget.used = 180
                        run.emit(stage, stage, "success", event="plan_generated", subtasks=["Find method description", "Find baseline comparison", "Collect evidence"])
                        run.checkpoint("Tool")
                    elif stage == "Tool":
                        attempt = len(run.receipts) + 1
                        if run.inject_requested and not run.recovered:
                            run.checkpoint_before_failure = run.state.checkpoint_id
                            receipt = {"operation_id": "search_paper", "attempt_id": f"tool-{attempt}", "status": "failed", "error": "SyntheticToolTimeout", "completed": False, "engineering_mock": True}
                            run.receipts.append(receipt)
                            atomic_write(run.directory / (f"tool_receipt_{attempt}.json"), canonical(receipt))
                            run.state.status, run.failure_type = "FAILED", "Tool Timeout"
                            run.actor_active = False
                            run.checkpoint("Tool")
                            run.emit(stage, stage, "failed", event="tool_timeout", failure_type="Tool Timeout", receipt=receipt,
                                     checkpoint_before_failure=run.checkpoint_before_failure,
                                     failure_class="tool_timeout (synthetic)", inflight=False,
                                     explanation="The failed operation has no committed successful result; only that operation may be retried in the UI fixture.")
                            run.stream_closed = True
                            run.journal.changed.notify_all()
                            return
                        receipt = {"operation_id": "search_paper", "attempt_id": f"tool-{attempt}", "status": "success", "completed": True, "engineering_mock": True}
                        run.receipts.append(receipt)
                        atomic_write(run.directory / (f"tool_receipt_{attempt}.json"), canonical(receipt))
                        run.execution_counts["tool"] = run.execution_counts.get("tool", 0) + 1
                        run.state.completed_steps.append("tool")
                        run.emit(stage, stage, "success", event="tool_call", tool="search_paper()", parameters={"query": run.task},
                                 evidence_chunks=3, evidence=EVIDENCE, receipt=receipt,
                                 retry_of_uncommitted_failure=run.recovered)
                        run.checkpoint("Memory")
                    elif stage == "Memory":
                        run.execution_counts["memory"] = run.execution_counts.get("memory", 0) + 1
                        run.memory = copy.deepcopy(EVIDENCE)
                        run.state.memory_size = 3
                        run.state.completed_steps.append("memory")
                        run.emit(stage, stage, "success", event="evidence_updated", evidence=run.memory, retained_ids=["E1", "E2", "E3"])
                        run.checkpoint("Context")
                    elif stage == "Context":
                        run.execution_counts["context"] = run.execution_counts.get("context", 0) + 1
                        run.state.token_budget.used = 2800
                        run.state.completed_steps.append("context")
                        run.emit(stage, stage, "success", event="context_selected", before_tokens=5200, after_tokens=2800, token_limit=4096,
                                 selected_ids=["E1", "E2"], excluded_ids=["E3"], memory_retained_ids=["E1", "E2", "E3"],
                                 action="Removed low relevance evidence from the request; retained all evidence in memory.",
                                 measurement="Explicit presentation fixture values, not native tokenizer counts.")
                        run.checkpoint("Answer")
                    elif stage == "Answer":
                        run.execution_counts["answer"] = run.execution_counts.get("answer", 0) + 1
                        run.state.completed_steps.append("answer")
                        run.emit(stage, stage, "success", event="answer_generated",
                                 answer="The synthetic notes illustrate how an Agent would assess a method against a baseline [E1, E2]. They contain no measured performance claim.", citations=["E1", "E2"],
                                 generation="Fixed CPU mock text; no model call")
                        run.checkpoint("Critic")
                    else:
                        run.execution_counts["critic"] = run.execution_counts.get("critic", 0) + 1
                        run.state.completed_steps.append("critic")
                        run.emit(stage, stage, "success", event="citation_check", result="PASS", citations=["E1", "E2"],
                                 validation="Synthetic citation IDs are present in the selected fixture; this is not semantic correctness verification.")
                        run.checkpoint("complete")
            async with run.lock:
                run.state.current_stage = "complete"
                run.state.status = "RECOVERED" if run.recovered else "SUCCESS"
                run.actor_active = False
                run.recovering = False
                if run.recovery_report:
                    successful = [receipt for receipt in run.receipts if receipt["completed"]]
                    duplicate = max(0, len(successful) - len({row["operation_id"] for row in successful}))
                    replayed = sum(max(0, count - 1) for count in run.execution_counts.values())
                    lost = len(set(run.recovery_committed_steps) - set(run.state.completed_steps))
                    run.recovery_report.update(success=duplicate == 0 and replayed == 0 and lost == 0, in_progress=False,
                                               duplicate_tool_calls=duplicate, replayed_steps=replayed, lost_steps=lost)
                    atomic_write(run.directory / "recovery_demo_report.json", canonical(run.recovery_report))
                    run.emit("Runtime", "recovery", "recovered", event="recovery_completed", **run.recovery_report)
                run.emit("Runtime", "complete", "recovered" if run.recovered else "success", event="task_completed", completed_steps=run.state.completed_steps)
                run.stream_closed = True
                run.journal.changed.notify_all()
        except asyncio.CancelledError:
            async with run.lock:
                run.actor_active = False
                run.stream_closed = True
                run.journal.changed.notify_all()
            raise
        except Exception as exc:
            # Do not imply recovery eligibility for unknown/disk/implementation failures.
            async with run.lock:
                run.state.status = "FAILED"
                run.actor_active = False
                run.failure_type = "Presentation internal error"
                run.stream_closed = True
                try:
                    if run.journal.sequence < run.journal.max_total_events:
                        run.emit("Runtime", run.state.current_stage, "failed", event="presentation_error", error_type=type(exc).__name__)
                finally:
                    run.journal.changed.notify_all()

    async def close(self):
        active = [run.actor for run in self.runs.values() if run.actor and not run.actor.done()]
        for actor in active:
            actor.cancel()
        await asyncio.gather(*active, return_exceptions=True)
