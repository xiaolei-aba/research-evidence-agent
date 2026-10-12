# CPU presentation API

From the release root, install the pinned dependencies listed in the main README,
then run `python runtime_demo/backend/app.py`. Default binding is
`127.0.0.1:8001`. The built React page is served from `frontend/dist`.
No formal Agent, model, scorer, external service or GPU is executed by this server.

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | CPU Mock mode and resource limits |
| `POST /api/runs` with `{ "task": "..." }` | One synthetic presentation actor |
| `GET /api/runs/{id}` | State, events and recovery controls |
| `GET /api/runs/{id}/events` | SSE `runtime_event`; `Last-Event-ID` only replays transport |
| `POST /api/runs/{id}/inject-failure` | Queue a synthetic Tool Timeout |
| `POST /api/runs/{id}/recover` | Verify SHA and restore this same-session Mock fixture |
| `GET /api/replays` | Released historical trace catalog, verified before delivery |
| `GET /api/replays/{public_id}` | Read-only allowlisted events, no actor execution |

Historical replay rejects missing files, invalid schema, path escapes and SHA
failures with 503. Unknown trace IDs receive 404. It never falls back to generated
Mock events. A real model Dev failure and a historical CPU engineering restart
are different selections, each labelled by `source_kind`. The former contains no
observed recovery. Event wall-clock timestamps were not recorded; `timestamp` is
null and `duration_ms` is only the source operation duration. Playback speed is a
presentation setting and cannot reconstruct the historical timeline.

`DEMO_DEPLOYMENT_MODE=space` explicitly opts in to `0.0.0.0:7860` for the Docker
Space. This mode accepts only the same-origin UI on its own `*.hf.space` domain
and local test hosts. A different Space origin cannot mutate a run. Space data is
stored in `/tmp/research-evidence-runtime-demo`; it is ephemeral, not persistent
storage, and is not an exported research artifact. These settings prepare hosted
deployment; they do not establish that a Space has been published.

Local Mock data defaults to `runtime_demo/.demo_data`; optional `DEMO_DATA_ROOT`
selects a separate local QA directory. Space mode ignores that environment
variable. These directories must be excluded from the public snapshot. Run IDs
are retained, never automatically evicted or deleted. Total persisted runs are
capped at 32 across a backend restart, events at 96 per run, task length at 500
characters, and POST request bodies at 4096 bytes. At capacity, new runs are
rejected before a directory is created; read-only replay remains available.

The run registry is not rehydrated after backend restart. The interactive Mock
recovery continues within the current backend session, while the separate
historical engineering replay documents an already-completed subprocess restart.
It retries only an uncommitted synthetic Tool operation; committed Planner work
is preserved. The failed attempt remains in the trace. Neither is a production
recovery rate or real LLM recovery experiment. File fsync and atomic replacement
do not promise Windows power-loss durability.

Context `5200 -> 2800`, the three synthetic evidence chunks, and Failure Analysis
`30/25/20/25` are illustration fixtures. They are not Benchmark measurements.
Memory retains three fixture chunks; the Mock request selects two.

`DEMO_STEP_DELAY` controls only the presentation interval (default 1.7 seconds,
Tool twice that duration). It does not change any frozen experimental budget.

Run release CPU checks from `runtime_demo`:

```bash
python -m unittest discover -s backend/tests -v
```
