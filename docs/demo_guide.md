# Demo guide

The dashboard makes execution, state, and failure handling visible. It runs on CPU and makes no model or external inference API requests. Installation and launch commands are in [the Demo README](../runtime_demo/README.md); use the address printed by that launch command. The Docker Space files prepare a deployment candidate, not a claim that a Space is already live.

**This demo visualizes runtime mechanisms. It is not a benchmark evaluation interface.**

## A three-minute walkthrough

| Time | Show | Explain |
|---|---|---|
| 0:00–0:30 | About System | Model, Runtime, and Observability are separate responsibilities. The two research experiments are related but independent. |
| 0:30–1:15 | Mock Runtime Demo | Start a synthetic run and follow Planner → Tool → Memory → Context → Answer → Critic. Watch state and committed boundaries change. |
| 1:15–2:00 | Inject Failure / Recover | In a new run, inject the synthetic tool timeout during Planner/Tool, then recover after FAILED. Inspect the checkpoint and retained failed attempt. |
| 2:00–2:30 | Historical Trace Replay | Read a sanitized recorded failure or CPU recovery episode; expand events and discuss the preserved error category. |
| 2:30–3:00 | Experiment boundary | Show aggregate results, B4 below B1, Context default OFF, and the separate Answer-only LoRA comparison. |

## Mock Runtime Demo

The task field labels a fixed research fixture. It does not cause semantic research. Avoid entering private material. Start a run, watch the six-stage timeline, then expand Trace events to see component, status, and state updates.

Memory retains three synthetic evidence units while Context supplies two. The displayed **5,200 → 2,800** context pressure and **4,096** demonstration limit are illustrative constants, not native token measurements or Benchmark consumption. The synthetic final answer and citation check do not verify a scientific claim.

For failure handling, start a new run and click **Inject Failure** while the Planner/Tool window is active. Injection after committed tool execution is rejected. After FAILED, click **Recover** and inspect the loaded checkpoint, recovery point, and attempt counts. Recovery must retain completed work and the failed receipt. A new attempt of an uncommitted synthetic tool operation is separately counted; zero duplicate completed calls does not mean no retry happened.

The UI's Recover operates inside the current backend session. Its in-memory run registry is not rebuilt after restarting the backend. It does not demonstrate process-restart recovery or a real-LLM recovery rate.

## Historical Trace Replay

Choose **Historical Trace Replay** and select a record. The public fixtures contain two clearly labeled kinds of history:

- **Recorded Agent Dev failure:** sanitized stage/event/status records from a real historical model experiment. Playback does not run that model or replace its failed outcome.
- **Recorded CPU Mock process restart:** sanitized events from a controlled engineering recovery episode. This verifies the original fixture's mechanism, not real-model or production recovery.

Replay reads the curated events in source order. An unknown historical timestamp remains null; animation pacing is a presentation choice, not measured execution latency. Stages absent from a source record are not invented to complete the visual sequence. Playback cannot inject a new failure, recover the original experiment, change a terminal outcome, or create a new Benchmark observation.

The replay manifest binds the public fixture bytes with SHA256. [The replay publication policy](limitations.md#public-reproducibility-and-provenance) explains the field allowlist: stage, component, event, status, and error category can remain; local paths, usernames, PIDs, private task IDs, questions, Gold, and raw responses are excluded.

## Panels and their meaning

| Panel | Read it as |
|---|---|
| Execution Timeline | Stage transitions in the selected synthetic run or recorded playback |
| Agent State | Mock server-owned state, or the limited sanitized replay metadata; not hidden model reasoning |
| Checkpoint | Demonstration integrity and committed-boundary information, not production persistence guarantees |
| Trace Viewer | Expandable events for inspection; reconnecting must not dispatch the task again |
| Failure Analysis | Labeled illustrative distribution or counts from the selected replay source; neither is a benchmark accuracy |
| About System | Model/Runtime/Observability layers and the boundary between research and presentation |

Any fixed 30/25/20/25% failure chart is **Illustration only**. It is not measured from the current run or formal experiments.

## What this demonstration establishes

The included code can generate bounded synthetic events, stream them to the dashboard, update explicit state, save/load checked demonstration checkpoints, preserve failed attempts, and replay a sanitized event record without model execution. The core source separately exposes the research state/context/recovery logic for inspection and CPU tests.

**Recovery Demo validates runtime mechanisms; it does not represent a real production recovery rate.** Model quality, corpus coverage, semantic citation support, arbitrary crash recovery, and throughput under many concurrent users require separate evidence. The prepared Docker Space runs this CPU demonstration only and ships no model weights, Adapter, Benchmark Gold, raw predictions, or internal audit files.
