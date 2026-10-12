"""Bounded SSE journal; replaying events never executes an Agent operation."""
import asyncio
import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .schemas import RuntimeEvent


class EventJournal:
    def __init__(self, directory: Path, lock: asyncio.Lock, capacity: int = 96):
        self.events: deque[dict] = deque(maxlen=capacity)
        self.sequence = 0
        self.path = directory / "events.jsonl"
        self.changed = asyncio.Condition(lock)
        self.max_total_events = capacity

    def append(self, component: str, stage: str, status: str, payload: dict) -> dict:
        """Caller holds the run lock. Disk history survives in-memory eviction."""
        if self.sequence >= self.max_total_events:
            raise RuntimeError("Presentation event limit reached; no further disk writes are allowed.")
        self.sequence += 1
        event = RuntimeEvent(
            event_id=self.sequence,
            timestamp=datetime.now(timezone.utc).isoformat(),
            component=component,
            stage=stage,
            status=status,
            payload={**payload, "engineering_mock": True},
        ).model_dump()
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
        self.events.append(event)
        self.changed.notify_all()
        return event

    async def stream(self, run, after: int = 0):
        """Last-Event-ID / after resumes transport, never the actor."""
        cursor = after
        while True:
            async with self.changed:
                available = [row for row in self.events if row["event_id"] > cursor]
                ended = run.stream_closed
                if not available and not ended:
                    try:
                        await asyncio.wait_for(self.changed.wait(), timeout=15)
                    except asyncio.TimeoutError:
                        pass
                    available = [row for row in self.events if row["event_id"] > cursor]
                    ended = run.stream_closed
            if not available and not ended:
                yield ": heartbeat\n\n"
            for event in available:
                cursor = event["event_id"]
                yield f"id: {cursor}\nevent: runtime_event\ndata: {json.dumps(event, ensure_ascii=False, allow_nan=False)}\n\n"
            if ended:
                return
