# Scope and limitations

This document explains the boundaries of the Runtime source, demonstration and reported research. The [README](../README.md) introduces the system; [Runtime Design](runtime_design.md), [Experiments](experiments.md) and the [Demo Guide](demo_guide.md) provide the technical detail.

## Demo and recorded replay

**The Demo is Runtime visualization, not a Benchmark evaluation interface. Mock output is synthetic, not a model result. Recovery Demo validates Runtime mechanisms, not production recovery rate.**

The dashboard runs an independent deterministic CPU Mock Actor or reads sanitized historical event fixtures. It does not execute the research Agent, load a model or request a model API. Task text labels a fixed fixture rather than triggering arbitrary semantic research. Evidence and final answers are synthetic in Mock mode.

Displayed context pressure, Token cards and fixed failure distributions are illustrative constants, not native token counts, model consumption or measured Benchmark accuracy. Local state and checkpoint files are actual engineering artifacts, but that does not make the fixture a model-quality evaluation.

Historical replay contains two selected excerpts: a real Agent Dev generation failure without recovery, and a prior controlled CPU Mock process restart. Playback cannot rerun, repair or resample either observation. Absent stages are not invented. Missing historical timestamps remain unknown; animation speed is not recorded task latency. These excerpts do not supply a denominator for a recovery rate.

The UI's Recover continues within the current backend session. Restarting that backend does not reconstruct its in-memory session registry. The separately labeled historical engineering restart records prior mechanism evidence; it does not make the UI a general process-restart recovery service.

## Checkpoint and execution guarantees

The research core persists explicit state, budgets, request intent, operation receipts and committed boundaries. Eligible continuation reuses recorded outcomes; it does not repeat a saved terminal result, including a normal failure, until an answer improves. An intent without an outcome receipt blocks automatic recovery because dispatch may already have occurred.

SHA256, canonical snapshots and checked task/config identities detect inconsistent bytes or execution identities. They do not authenticate records against someone who can rewrite both the data and its hashes. File `fsync` and atomic replacement describe the implemented scope; Windows directory durability under sudden power loss is unconfirmed. Distributed exactly-once execution for arbitrary external side effects is not established. See the [checkpoint protocol](runtime_design.md#checkpoint-protocol).

The implementation uses full-state snapshots rather than an incremental checkpoint log. Repeated snapshots have storage/I/O costs. The full Trace journal stays on disk while a bounded tail stays in memory; that does not bound every evidence item or receipt. The historical controller also retained its fixed evaluation results in memory. Infinite task-stream scalability has not been demonstrated.

Tool timeout checks are cooperative within an operation. Hard task deadlines use an owned worker boundary; arbitrary external operations cannot be assumed to be preemptible or safe to retry. Controlled CPU fault fixtures do not establish a real-LLM or production recovery rate.

## Context policy and cost tradeoff

Whole-evidence Context selection is optional and remains **off by default**. In the matched 152-observation Dev extension, failed states decreased from 59/76 to 56/76, about 3.95 percentage points, while reported tokens increased by 19.40% and mean parent-measured latency by 17.03%. This missed the preregistered quality/cost thresholds; it is not a new Final/Test result.

The policy preserves whole units and supplied citation identities. It does not guarantee that relevant evidence is retrieved or that selected passages answer the question. An unresolved budget condition is a failure to preserve, not a reason to bypass the frozen budget. See [Runtime Design](runtime_design.md#memory-and-context) and the [matched Dev results](experiments.md#runtime-extension-matched-dev).

## Research scope and measurement

The studies use small fixed public subsets and a single research seed. Hours-long operation, production-scale throughput and broad generalization were not evaluated. The original Agent study's 1,034 observations include comparisons on shared questions; they are not 1,034 independent questions. Small B5 diagnostics support engineering inspection rather than a robust efficiency claim.

Frozen quality denominators include failures. Operational completion, protocol validity and semantic correctness are separate properties. B4 improved over B4off but remained below B1 on the reported Final benchmarks, and used more reported tokens than B4off. Historical cache-on/cache-off conditions cannot be pooled to claim a matched latency improvement. Negative results are preserved in [Experiments](experiments.md).

Source exposure and pretraining contamination are not ruled out. SciFact's project Final uses selected official public dev claims, not a hidden leaderboard test. The studies' exposed Final/Test material is unavailable for further strategy selection; future methods need a separately frozen untouched evaluation protocol.

## Citations, evidence and answerability

Citation identity/scope validation checks that a citation refers to permitted evidence; it does not establish that the passage entails a scientific claim. Benchmark Evidence F1 measures reference overlap under each study's scoring rules. Independent human semantic review coverage is **0**; Citation Support Rate and Unsupported Claim Rate remain **unknown/null**.

The SFT Test contains four official unanswerable questions, all answered by P1. Zero abstention therefore does not establish robust answerability. A retrieval miss or insufficient input evidence is distinct from an officially unanswerable question. Complete output protocol pass also does not mean a human judged the answer correct.

## Independent SFT experiment

The full-Agent study used a 1.5B quantized GGUF model through llama.cpp. The Answer-only study used a 0.5B BF16 base with LoRA through Transformers/PEFT and fixed retrieved inputs. Models, prompts, evidence contracts, splits and scoring wrappers differ. The LoRA model was not integrated into the Agent; these scores cannot be merged or directly ranked as full-Agent improvements.

The checked SFT Test identities have no overlap with selected formal V1 tasks, while those Test papers were already in the stored official-train corpus. Stored corpus membership is not formal model-task exposure; broader semantic leakage and pretraining exposure remain unknown. Full historical input tensors and per-operation inference dtypes were not persisted, so bitwise equivalence is unconfirmed. Output-length differences and one run do not prove a universal adapter-only speedup.

**DPO/GRPO were not trained.** The development route was selected before Test. Post hoc Test analysis explains the completed comparison and was not used to select a subsequent method, reward or hyperparameter. See [Experiments](experiments.md#experiment-2-answer-model-adaptation) for the all-50 results and descriptive strata.

## Public reproducibility and provenance

The snapshot includes core source, selected CPU tests, Demo fixtures, sanitized replay and full-precision aggregate results. It excludes model weights, GGUF, LoRA adapters, Benchmark Gold, original predictions, raw paper text, private logs and internal source mappings. It is not a complete redistribution of the research environment or a one-command reproduction of frozen Benchmark runs.

Without Gold and per-task predictions, historical aggregate scores cannot be independently recomputed from this public package. The hashes in [results_summary.json](results_summary.json) record checked source provenance; hashes alone do not supply independent external verification. Replay SHA/schema checks bind the included sanitized bytes.

Public replay events contain only `timestamp`, `component`, `event`, `status` and `metadata`. Component/event values use fixed enums; metadata admits approved stages, error categories, statuses, numeric counters and boolean flags. There are no arbitrary response-text fields. Original task/paper identifiers, questions, evidence text, Gold, model outputs, HTTP responses, local paths, usernames, PIDs, credentials and internal source mappings are excluded. Checkpoint identifiers use public sequence numbers. See the [replay manifest](../runtime_demo/data/replay/trace_manifest.json) for the included bytes and their hashes.

The catalog contains 69 events from the recorded Agent Dev failure and 48 from the CPU Mock restart fixture. These are selected examples, not random samples. The failed answer remains failed; the engineering restart's saved prefix is presented once. SHA/schema or missing-source errors block replay with no synthetic fallback. Per-operation durations can overlap and cannot be added to invent wall-clock timestamps; null timestamps remain null.

## Deployment and storage

**container build not locally verified**. Docker was unavailable on the delivery machine; no local container build or run was claimed. Docker Space configuration is prepared, but an HF username/Space and hosted acceptance remain pending. Local CPU/browser checks do not substitute for container or hosted checks.

The Docker configuration runs as UID 1000 on port 7860. It follows the [official Docker Spaces guide](https://huggingface.co/docs/hub/spaces-sdks-docker) and [Space configuration reference](https://huggingface.co/docs/hub/spaces-config-reference). Image pulling, Linux execution, hosted SSE/proxy behavior and actual storage lifetime require deployment validation.

The hosted prototype is unauthenticated and bounded to a small number of synthetic sessions; it is not a production service. State is ephemeral, and the session registry is not rebuilt after restart. Capacity limits are exposed through `/api/health`; reaching them blocks new Mock runs while replay remains available. See the [Demo startup guide](../runtime_demo/README.md).

Do not enter private material. Mock task labels are saved with generated Demo records, which must remain outside the release. Dependency installation needs package access; application operation makes no outbound model requests.

## License and attribution

Rights-cleared project contributions are Apache-2.0. Third-party code, dependencies, model weights and dataset text retain their original licenses; this project does not relicense them. Required attribution is retained in [LICENSE](../LICENSE), [NOTICE](../NOTICE) and [LICENSES](../LICENSES). No upstream endorsement is implied.
