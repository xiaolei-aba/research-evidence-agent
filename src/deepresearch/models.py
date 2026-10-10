from dataclasses import dataclass, field, asdict, fields, is_dataclass
from typing import get_type_hints, get_origin, get_args
import json
from uuid import uuid4

def uid(): return uuid4().hex
class JsonModel:
    def to_json(self): return json.dumps(asdict(self), ensure_ascii=False)
    @classmethod
    def from_json(cls, text):
        def decode(t, v):
            if get_origin(t) is list: return [decode(get_args(t)[0], x) for x in v]
            if is_dataclass(t):
                hints = get_type_hints(t)
                return t(**{k: decode(hints[k], x) for k, x in v.items()})
            return v
        return decode(cls, json.loads(text))
@dataclass
class UserQuery(JsonModel):
    text: str
    query_id: str = field(default_factory=uid)
@dataclass
class SubTask(JsonModel):
    task_id: str
    query: str
    status: str = "pending"
    error: str = ""
@dataclass
class ResearchPlan(JsonModel):
    query_id: str
    tasks: list[SubTask]
    plan_id: str = field(default_factory=uid)
@dataclass
class ToolRequest(JsonModel):
    task_id: str
    tool: str
    argument: str
    request_id: str = field(default_factory=uid)
@dataclass
class RetrievedDocument(JsonModel):
    document_id: str
    source: str
    title: str
    content: str
    facts: dict
    source_id: str = ""
    source_version: str = "legacy-unversioned"
    license: str = "unspecified"
    section_id: str = ""
    chunk_id: str = ""
    document_sha256: str = ""
    chunk_sha256: str = ""
    span_start: int = 0
    span_end: int = 0
    source_text: str = ""
    retrieval_score: float = 0.0
@dataclass
class ToolResult(JsonModel):
    request_id: str
    status: str
    documents: list[RetrievedDocument] = field(default_factory=list)
    error: str = ""
@dataclass
class EvidenceItem(JsonModel):
    evidence_id: str
    document_id: str
    source: str
    content: str
    task_ids: list[str]
    retrieved_at: str
    locator: str
    facts: dict
    source_id: str = ""
    source_version: str = "legacy-unversioned"
    license: str = "unspecified"
    section_id: str = ""
    chunk_id: str = ""
    document_sha256: str = ""
    chunk_sha256: str = ""
    span_start: int = 0
    span_end: int = 0
    source_text: str = ""
@dataclass
class CriticFeedback(JsonModel):
    sufficient: bool
    reason: str
    missing_queries: list[str]
    conflicts: list[str]
    feedback_id: str = field(default_factory=uid)
@dataclass
class Citation(JsonModel):
    evidence_id: str
    claim: str
    citation_id: str = field(default_factory=uid)
@dataclass
class FinalAnswer(JsonModel):
    query_id: str
    text: str
    citations: list[Citation]
    status: str
    error: str = ""
    answer_id: str = field(default_factory=uid)
@dataclass
class TraceEvent(JsonModel):
    kind: str
    status: str
    elapsed_ms: float
    payload: dict
    error: str = ""
@dataclass
class AgentTrace(JsonModel):
    query_id: str
    events: list[TraceEvent] = field(default_factory=list)
    tool_calls: int = 0
    token_usage: int | None = None
    latency_ms: float = 0
    trace_id: str = field(default_factory=uid)
    model_calls: int = 0
    http_attempts: int = 0
    token_usage_reported: int = 0
    prompt_tokens_reported: int = 0
    completion_tokens_reported: int = 0
    usage_unreported_attempts: int = 0
    token_usage_complete: bool = False
    versions: dict = field(default_factory=dict)
    budget: dict = field(default_factory=dict)
