# Local dashboard and read-only replay

This UI demonstrates bounded Runtime concepts. Mock mode uses a separate deterministic CPU Actor, not the formal Agent or a model. Replay mode reads two sanitized historical excerpts and never starts an Actor. **Demo ≠ Benchmark; controlled recovery ≠ production recovery rate.**

## Install and run

From the release root, use Python 3.11 and Node.js 24:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements/demo.lock.txt
cd runtime_demo/frontend
npx pnpm@11.25.0 install --frozen-lockfile
npx pnpm@11.25.0 run build
cd ../..
.\.venv\Scripts\python.exe runtime_demo/backend/app.py
```

Open <http://127.0.0.1:8001>. Linux/macOS replace the Python executable with `.venv/bin/python`. `Ctrl+C` exits the owned backend normally. Dependencies are downloaded during installation; application operation has no outbound model requests. Do not enter private material: local Mock task labels are saved with demo records.

For development, start the backend as above and run `npx pnpm@11.25.0 run dev` in `runtime_demo/frontend` in a second terminal. Open <http://127.0.0.1:5175>; Vite proxies only to local 8001. If a port is occupied, identify its owner rather than stopping unrelated software. `npm install`, `npm run build` and `npm run dev` also work; npm does not consume the pnpm lock.

## Three-minute demonstration

1. Open **About System**. Explain the Model, Runtime and Observability layers and the two independent experiments. No model is connected to this UI.
2. In **Mock Runtime Demo**, Start the example task. Inspect six stages, live JSON state, E1/E2/E3 memory versus E1/E2 context, and one expanded Trace event. Counts 5200→2800/4096 are illustration-only, not tokenizer or model consumption.
3. Start a second run. During Planner/Tool, Inject Failure. When FAILED appears, Recover. The failed receipt is retained; the uncommitted synthetic Tool attempt is retried once. Committed Planner work is not replayed. Counts stay pending until the report is finalized.
4. Select **Historical Trace Replay**. Play the real-model Dev excerpt: its recorded generation failure remains a failure, with no invented recovery. Select the engineering restart excerpt: its restart/continuation events are historical CPU Mock evidence, not real-LLM recovery.
5. Pause/reset/change playback speed and expand Trace. `t=?` means timestamps were not persisted; playback pacing does not reconstruct measured wall-clock timing. Historical state counters are shown only when present in the sanitized source.

## Boundaries and storage

- Mock data: `runtime_demo/.demo_data/` by default; optional process-local `DEMO_DATA_ROOT` redirects it. These generated records are excluded from the release and must not be uploaded.
- Released historical data: `data/replay/trace_manifest.json` and allowlisted JSONL. SHA/schema failures block loading and never fall back to Mock.
- Same-session UI Recover does not rebuild the backend registry after a restart. See the separately labeled historical CPU Mock process-restart excerpt for that prior mechanism evidence.
- The run, event, task and request limits are reported by `/api/health`. They limit this display Actor and do not change frozen Agent budgets. A full demo-session capacity blocks new runs; replay remains available.
- SHA detects byte/identity changes; it does not authenticate against a writer who can forge every local file. File fsync/replace is not a Windows power-loss guarantee.
- Failure percentages are explicitly **Illustration only**, normalized to 100 units without sampled records. Replay instead lists recorded error categories without a denominator or invented rate.

## Docker / Hugging Face

The release-root Dockerfile sets `DEMO_DEPLOYMENT_MODE=space` and serves port 7860 as a non-root user. Local mode binds loopback 8001. Hosted mode is explicit, has same-host Origin checks and uses bounded ephemeral `/tmp/research-evidence-runtime-demo`; it ignores arbitrary local data-root settings. No key, secret, GPU, model or persistent paid resource is required by the app.

The Docker image and a live Space have not yet been accepted on the current machine. This is deployment preparation, not a claim of successful remote release. Consult the [official Docker Spaces documentation](https://huggingface.co/docs/hub/spaces-sdks-docker); do not upload internal audits, model artifacts, original questions/Gold/predictions or private logs.

## CPU checks

From the release root:

```powershell
$env:PYTHONPATH=(Resolve-Path src).Path
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m unittest discover -s runtime_demo/backend/tests -t runtime_demo -v
cd runtime_demo/frontend
npx pnpm@11.25.0 run test
```

The core tests use an explicitly synthetic corpus, not the private Benchmark. Windows TestClient uses a local asyncio socketpair; restricted sandboxes may deny it even though no external request is sent. The original experiment files, scoring results and research history are separate and read-only.
