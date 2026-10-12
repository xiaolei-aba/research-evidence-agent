"""CPU-only presentation transport with explicit local / Docker Space modes."""
import os
import math
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.mock_agent import DemoError, MockRuntime, MOCK_NOTICE
    from backend.schemas import StartRunRequest
    from backend.replay import ReplayCatalog
else:
    from .mock_agent import DemoError, MockRuntime, MOCK_NOTICE
    from .schemas import StartRunRequest
    from .replay import ReplayCatalog

from fastapi import FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles


class RequestSizeLimitMiddleware:
    """Bound POST body bytes before parsing or creating any presentation run."""
    def __init__(self, app, limit=4096):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.limit:
                response = JSONResponse(status_code=413, content={"detail": {"code": "request_body_limit", "message": "Presentation request exceeds the 4096-byte limit; no run was created."}})
                return await response(scope, receive, send)
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return await self.app(scope, bounded_receive, send)


def deployment_mode():
    mode = os.environ.get("DEMO_DEPLOYMENT_MODE", "local")
    if mode not in {"local", "space"}:
        raise ValueError("DEMO_DEPLOYMENT_MODE must be local or explicitly space")
    return mode


def create_app(data_root: Path | None = None, step_delay: float | None = None,
               replay_root: Path | None = None, mode: str | None = None, max_runs: int = 32):
    demo_root = Path(__file__).resolve().parents[1]
    mode = deployment_mode() if mode is None else mode
    if mode not in {"local", "space"}:
        raise ValueError("mode must be local or space")
    default_data_root = Path("/tmp/research-evidence-runtime-demo") if mode == "space" else Path(os.environ.get("DEMO_DATA_ROOT", str(demo_root / ".demo_data")))
    data_root = Path(data_root or default_data_root)
    catalog = ReplayCatalog(replay_root or demo_root / "data" / "replay")
    delay = float(os.environ.get("DEMO_STEP_DELAY", "1.7")) if step_delay is None else step_delay
    if not math.isfinite(delay) or delay < 0 or delay > 30:
        raise ValueError("DEMO_STEP_DELAY must be between 0 and 30 presentation seconds")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = MockRuntime(data_root, delay, max_runs=max_runs)
        app.state.replays = catalog
        try:
            yield
        finally:
            await app.state.runtime.close()

    app = FastAPI(title="ResearchEvidence Agent Runtime Presentation", version="1.1.0", lifespan=lifespan,
                  description=MOCK_NOTICE)
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:5174", "http://localhost:5174"],
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Last-Event-ID"], allow_credentials=False)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"] + (["*.hf.space"] if mode == "space" else []))
    app.add_middleware(RequestSizeLimitMiddleware, limit=4096)

    @app.middleware("http")
    async def local_mutation_guard(request: Request, call_next):
        origin = request.headers.get("origin")
        allowed = {"http://127.0.0.1:8001", "http://localhost:8001", "http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:5174", "http://localhost:5174"}
        same_origin = False
        if origin:
            try:
                parsed = urlsplit(origin)
                same_origin = parsed.scheme in {"http", "https"} and parsed.netloc == request.headers.get("host") and not parsed.username and not parsed.password and parsed.path == "" and not parsed.query and not parsed.fragment
            except ValueError:
                pass
        if request.method == "POST" and origin and not same_origin and not (mode == "local" and origin in allowed):
            return JSONResponse(status_code=403, content={"detail": {"code": "nonlocal_origin", "message": "Only the same-origin demonstration UI may mutate a run."}})
        return await call_next(request)

    @app.exception_handler(DemoError)
    async def demo_error(request: Request, exc: DemoError):
        return JSONResponse(status_code=exc.status, content={"detail": {"code": exc.code, "message": exc.message}, "engineering_mock": True})

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "mode": "engineering_mock", "deployment_mode": mode, "real_model_calls": False, "gpu": False, "outbound_api_calls": False, "limits": {"total_runs": max_runs, "events_per_run": 96, "task_characters": 500, "request_bytes": 4096}, "notice": MOCK_NOTICE}

    @app.get("/api/replays")
    async def replays(request: Request):
        return request.app.state.replays.list()

    @app.get("/api/replays/{public_id}")
    async def replay(public_id: str, request: Request):
        return request.app.state.replays.get(public_id)

    @app.post("/api/runs", status_code=201)
    async def start(body: StartRunRequest, request: Request):
        run = await request.app.state.runtime.start(body.task)
        return {**run.snapshot(), "events_url": f"/api/runs/{run.run_id}/events"}

    @app.get("/api/runs/{run_id}")
    async def snapshot(run_id: str, request: Request):
        run = request.app.state.runtime.get(run_id)
        async with run.lock:
            return run.snapshot()

    @app.post("/api/runs/{run_id}/inject-failure")
    async def inject(run_id: str, request: Request):
        runtime = request.app.state.runtime
        return await runtime.inject(runtime.get(run_id))

    @app.post("/api/runs/{run_id}/recover")
    async def recover(run_id: str, request: Request):
        runtime = request.app.state.runtime
        return await runtime.recover(runtime.get(run_id))

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request, after: int = Query(default=0, ge=0), last_event_id: str | None = Header(default=None)):
        run = request.app.state.runtime.get(run_id)
        if last_event_id is not None:
            try:
                parsed_cursor = int(last_event_id)
                if parsed_cursor < 0:
                    raise ValueError("negative")
                after = max(after, parsed_cursor)
            except ValueError as exc:
                raise DemoError(400, "invalid_event_cursor", "Last-Event-ID must be a nonnegative integer.") from exc
        if after > run.journal.sequence:
            raise DemoError(409, "event_cursor_ahead", "Event cursor is ahead of this run; no operation was restarted.")
        if run.journal.events and after < run.journal.events[0]["event_id"] - 1:
            raise DemoError(409, "event_cursor_expired", "Requested events left the bounded buffer; fetch the run snapshot.")
        return StreamingResponse(run.journal.stream(run, after), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/{unknown:path}", include_in_schema=False)
    async def unknown_api(unknown: str):
        raise DemoError(404, "api_not_found", "Unknown presentation endpoint.")

    dist = demo_root / "frontend" / "dist"
    if (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    hosted = deployment_mode() == "space"
    uvicorn.run(app, host="0.0.0.0" if hosted else "127.0.0.1", port=7860 if hosted else 8001, log_level="info", limit_concurrency=64, timeout_keep_alive=5)
