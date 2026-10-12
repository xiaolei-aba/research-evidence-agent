"""Public presentation contract; none of these objects are Benchmark results."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = Field(min_length=1, max_length=500)

    @field_validator("task")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("task must contain visible text")
        return value


class TokenBudget(BaseModel):
    limit: int = 4096
    used: int = 0


class AgentState(BaseModel):
    task_id: str
    current_stage: str
    completed_steps: list[str] = Field(default_factory=list)
    memory_size: int = 0
    token_budget: TokenBudget = Field(default_factory=TokenBudget)
    checkpoint_id: str | None = None
    status: Literal["RUNNING", "SUCCESS", "FAILED", "RECOVERED"] = "RUNNING"


class RuntimeEvent(BaseModel):
    event_id: int
    timestamp: str
    component: Literal["Planner", "Tool", "Memory", "Context", "Checkpoint", "Answer", "Critic", "Runtime"]
    stage: str
    status: Literal["running", "success", "failed", "recovered"]
    payload: dict[str, Any]


class RecoveryReport(BaseModel):
    task_id: str
    failure_type: str = "Tool Timeout"
    checkpoint_before_failure: str | None
    loaded_checkpoint_id: str | None = None
    recovery_point: str = "Tool: retry only the uncommitted synthetic operation"
    replayed_steps: int = 0
    lost_steps: int = 0
    duplicate_tool_calls: int = 0
    retry_attempts: int = 1
    success: bool = False
    in_progress: bool = True
    engineering_mock: bool = True
    scope: str = "Controlled presentation mock; not real model or production recovery rate"
