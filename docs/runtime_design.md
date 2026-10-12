# Runtime design

The central design choice is to make task execution explicit: what was requested, what completed, what was committed, and what remains eligible to execute. This makes failures diagnosable and lets eligible work resume without silently repeating committed calls.

This document describes the research runtime represented by the public core source. The public dashboard uses a separate CPU Mock Actor to make these ideas visible; it does not invoke the research Agent or model.

## State and budgets

| State | Why it is saved |
|---|---|
| Goal and fingerprint | Bind a checkpoint to the intended task and execution conditions |
| Current plan, stage, and cursor | Identify the next eligible boundary, round, subtask, and evidence operation |
| Completed boundaries | Preserve committed work; repeated boundary names are distinct occurrences |
| Evidence memory | Preserve evidence order, source identity, version, hashes, and locators |
| Tool history and receipts | Separate a logical request from its physical attempt and recorded outcome |
| Budget and original deadline | Prevent a restart from granting a fresh token/tool/time allowance |
| Pending intent | Detect a dispatch whose outcome has not been committed |
| Terminal result | Return an existing success or failure without resampling it |

The Agent experiment used a 4,096-token context window, a 3,072-token input limit, a 512-token output limit, at most 18 tools, and a 120-second task deadline. These are historical conditions, not knobs exposed by the public Demo. Requested tokens, reserved tokens, and reported consumed tokens are separate quantities.

## Checkpoint protocol

The [checkpoint implementation](../src/deepresearch/runtime/state.py) writes canonical JSON snapshots addressed by SHA256, then atomically replaces a small HEAD record that identifies the committed snapshot. An operating-system lease prevents two active workers from owning the same checkpoint. Loading verifies HEAD integrity, snapshot bytes, schema, execution fingerprint, and task identity before continuing.

```mermaid
sequenceDiagram
    participant R as Runtime
    participant J as Journal / checkpoint
    participant T as Tool or model
    R->>J: Save request intent and current budget
    R->>T: Dispatch eligible request
    T-->>R: Outcome
    R->>J: Save receipt, state, and committed boundary
    Note over R,J: Process interruption
    R->>J: Load and verify fingerprint / SHA / receipt
    alt Committed outcome exists
      J-->>R: Reuse receipt; advance eligible state
    else Intent exists without outcome
      J-->>R: Recovery blocked: ambiguous dispatch
    end
```

Resume does not mean “try until correct.” A saved normal failure is an outcome to preserve. An intent without a receipt may represent a request that already ran: automatically dispatching again could duplicate it, so that case fails closed. The project does not claim distributed exactly-once execution for arbitrary external side effects.

File `fsync` plus atomic replacement is the documented scope. Windows directory durability under sudden power loss is not established. SHA checks detect inconsistent bytes; they are not authentication against an attacker who can rewrite both data and hashes.

## Memory and context

Evidence Memory retains evidence for the task. Context is the evidence subset supplied to a particular request. The experimental [Context Manager](../src/deepresearch/runtime/context.py):

- Counts the complete rendered request with the provider's native tokenizer, including citation metadata.
- Leaves an already fitting request unchanged.
- For over-budget requests, removes duplicate units only when their complete source identity matches, then evicts whole units using query-term overlap.
- Preserves the original order of surviving evidence and pins evidence referenced by a candidate answer during Critic preparation.
- Records keep/drop reasons and checks answer citations against the evidence actually supplied.

It does not summarize with another model, truncate paragraphs, rewrite citation IDs, or use Gold to rank evidence. If pinned evidence or non-evidence metadata still exceeds the budget, it reports an unresolved budget condition rather than bypassing the limit.

Approximate selection complexity is `O(E log E + E × P)`, where `E` is the number of evidence units and `P` is the full request size. The worst case uses `E + 2` native-count invocations. Invocation count differs from physical RPC count because the counter can cache; historical physical counter RPC counts were not persisted.

The candidate improved some Dev outcomes but increased total reported tokens and latency. It remains **off by default**; the original C0/C1 observations were preserved. See [the matched Dev results](experiments.md#runtime-extension-matched-dev).

## Tools and validation

Search/read tools validate inputs, output schema, source version, hashes, and spans. Formal tool retry remained off in the Runtime comparison; finite transient retry was checked separately with engineering fixtures. Cooperative timeout checks cannot preempt a blocked operation on the same thread. An owned worker deadline provides a separate hard task boundary.

There are three different checks to discuss in an interview:

| Check | Establishes | Does not establish |
|---|---|---|
| Tool schema and source integrity | An operation produced a structurally valid, traceable result | Retrieval found all relevant evidence |
| Citation identity and scope | The citation refers to permitted registered/supplied evidence | The cited passage entails the claim |
| Benchmark evidence overlap | Agreement with the frozen reference units | Independent human citation correctness |

## Trace and resource protection

The research journal records task stages, request identities, receipts, errors, recovery decisions, and cost information. Only a 64-event Trace tail remains in the state; the complete event journal stays on disk. Evidence and receipts are not limited to 64 entries.

Resource monitoring distinguishes system Available/Committed memory, process Private Bytes/Working Set, and total-device GPU memory. Process working sets cannot be added to infer system available RAM. The historical run retained the original 1.25 GiB stop threshold and a 3 GiB initial buffer target. This public Demo makes no GPU requests.

The implementation uses full-state snapshots rather than an incremental checkpoint log. Repeated snapshots incur storage/I/O cost, and the historical controller retained its fixed evaluation results in memory. Infinite task-stream scalability, production recovery rate, and arbitrary GPU-failure recovery have not been demonstrated.

## Public demonstration scope

The interactive Demo uses its own bounded sessions, synthetic tool outcomes, and SHA-verified demonstration snapshots. A failed synthetic operation may receive a new attempt; completed operations must remain committed. Recovery counters describe that controlled episode, not real model correctness or production reliability. Its registry is not rebuilt after a backend restart, so UI recovery is limited to the active backend session.

Historical Replay is read-only playback. A historical failure remains a failure, and missing stages are not invented. See [Demo Guide](demo_guide.md).
