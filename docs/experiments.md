# Experiments

The project has two related, independent experiments: system-level Agent execution and Answer-model adaptation. Their model sizes, backends, prompts, evidence contracts, splits, and scoring wrappers differ. Scores and costs are reported within each study, never pooled into a single project accuracy or described as a 0.5B-versus-1.5B improvement.

The [machine-readable summary](results_summary.json) retains full-precision aggregate numbers, fixed denominators, and the SHA256 of the checked source records. Raw questions, Gold, predictions, private traces, and internal reports are not distributed. These hashes establish recorded provenance, not an independent external reproduction of the results.

## Experiment 1: Agent Runtime Evaluation

**Question:** What happens when planning, memory, critique, replanning, and execution controls are added to a small evidence-grounded Agent?

The Agent used **Qwen2.5-1.5B-Instruct Q4_K_M GGUF** with llama.cpp, temperature 0, seed 11, a 3,072-token input limit, and a 512-token output limit. QASPER measures paper QA; SciFact measures claim labels and evidence selection. The study preserved both operational failures and insufficient-evidence outcomes.

### Design and observation counts

| Phase | Comparison | Saved task observations | Boundary |
|---|---|---:|---|
| B1–B3 Dev | B1 fixed RAG path; B2 adds Planner; B3 adds Memory, Critic, and bounded Replan | 228 | Three configurations on 76 Dev tasks |
| B4 Dev | B4 adds source-identity deduplication; B4off disables it | 152 | Two configurations on the same 76 Dev tasks |
| B5 | Cost/ablation engineering diagnostics | 30 | Only 3 tasks per benchmark/configuration; no robust efficiency claim |
| Frozen Final | B1, B4, B4off | 624 | 108 QASPER and 100 SciFact tasks per configuration |

The total **1,034 task observations** includes repeated evaluation of configurations on shared tasks. It is not 1,034 independent questions. QASPER Dev/Final use selected tasks from official validation/test respectively. SciFact uses official train for development and official public dev for the project Final; it is not a hidden leaderboard test.

### Frozen Final results

Scores below are ratios on `[0, 1]`. Main quality denominators include runtime failures, which receive zero quality credit. Insufficient evidence remains a separate operational state and follows the frozen benchmark's abstention/unanswerable mapping. `Completed` means an operational terminal state, not human-verified correctness.

QASPER Answer F1 uses normalized answer-token overlap against official references; V1 Evidence F1 uses the frozen text/quote matching and list-denominator rules. SciFact label accuracy divides correct labels by all registered claims. Its sentence-label micro F1 pools correct, retrieved, and relevant sentence/label pairs; it is not an average answer score. These evidence metrics cannot be interchanged or combined across benchmarks.

| Benchmark | Arm | All tasks | Completed | Failed | Insufficient | Answer F1 / label accuracy | Evidence F1 / sentence-label micro F1 |
|---|---|---:|---:|---:|---:|---:|---:|
| QASPER | B1 | 108 | 69 | 39 | 0 | 0.1672 | 0.1345 |
| QASPER | B4 | 108 | 55 | 53 | 0 | 0.1345 | 0.1221 |
| QASPER | B4off | 108 | 40 | 68 | 0 | 0.0747 | 0.0871 |
| SciFact | B1 | 100 | 97 | 3 | 0 | 0.1000 | 0.0524 |
| SciFact | B4 | 100 | 16 | 71 | 13 | 0.0200 | 0.0141 |
| SciFact | B4off | 100 | 8 | 89 | 3 | 0.0000 | 0.0000 |

B4 improved over its matched B4off ablation: QASPER Answer F1 increased by about **0.0598**, while SciFact label accuracy increased by **0.0200**. **B4 still scored below B1 on both benchmarks.** The result supports a narrow deduplication comparison under the tested configuration, not a claim that adding Agent modules universally improves performance.

| Benchmark / arm | Reported input + output tokens, all tasks | Mean tools/task | Mean warm task latency |
|---|---:|---:|---:|
| QASPER B1 | 233,696 | 5.963 | 2.214 s |
| QASPER B4 | 421,391 | 8.815 | 2.805 s |
| QASPER B4off | 292,422 | 8.759 | 2.088 s |
| SciFact B1 | 140,277 | 6.000 | 0.898 s |
| SciFact B4 | 714,763 | 16.140 | 3.206 s |
| SciFact B4off | 684,550 | 16.380 | 3.099 s |

Cost includes the failure outcomes. Warm task timing includes worker/corpus preparation and native counting; cold model load is separate. B4 did not reduce total tokens against B4off in this Final. Historical cache-on/cache-off development conditions are not pooled into a matched latency improvement.

### Runtime extension: matched Dev

A later **152-observation Dev comparison** added durable state, checkpoint integrity, trace analysis, and an optional whole-unit Context Manager. Both C0/C1 used the same shared runtime harness and 1.5B backend; Context selection and supplied-citation scope were the treatment. This is part of the Agent Runtime experiment, not a third model-adaptation experiment or a new Final/Test result.

| Benchmark | Arm | All tasks | Completed | Failed | Insufficient | Answer F1 / label accuracy | Evidence F1 / sentence-label micro F1 |
|---|---|---:|---:|---:|---:|---:|---:|
| QASPER | C0 | 36 | 10 | 26 | 0 | 0.0538 | 0.0406 |
| QASPER | C1 | 36 | 13 | 23 | 0 | 0.0845 | 0.0406 |
| SciFact | C0 | 40 | 5 | 33 | 2 | 0.0250 | 0.0000 |
| SciFact | C1 | 40 | 5 | 33 | 2 | 0.0250 | 0.0000 |

Failures fell from **59/76 to 56/76**, an absolute **3.947 percentage-point** decrease. Reported tokens increased **366,964 → 438,151 (+19.399%)**; mean parent-measured task latency increased **4.150 → 4.856 seconds (+17.035%)**. This missed the preregistered 5-point failure reduction target and exceeded the 10% token-cost increase threshold. **Context remains off by default.** Durable state and trace mechanisms were retained as engineering capabilities; no new full-Agent quality superiority was established.

Important limits are the single seed, selected public subsets, unknown pretraining exposure, small B5 diagnostics, and absence of independent semantic citation review. Citation ID validity is distinct from citation support. CPU fault fixtures verify eligible recovery mechanisms; they do not estimate production recovery or real-LLM recovery success.

## Experiment 2: Answer Model Adaptation

**Question:** Can supervised adaptation improve structured, evidence-conditioned Answer output under one fixed input protocol?

This independent study used **Qwen2.5-0.5B-Instruct BF16** with Transformers/PEFT. P0 is the unadapted base; P1 adds unmerged FP32 LoRA weights to that same base. It is not the 1.5B GGUF Agent and was not integrated into its Planner, Critic, or Answer runtime.

QASPER official train was split by paper: the accepted Train set contains **500 examples from 360 papers**, and Dev/Test each contain **50 questions from 50 papers**. Inputs use frozen paper-local BM25 top-12 evidence, a 3,072-token input limit, a 512-token output limit, and deterministic decoding. The LoRA configuration is rank 8, alpha 16, dropout 0.05, targeting q/k/v/o attention projections; **1,081,344 parameters** are trainable. Completion-only supervision masks prompt tokens. The accepted training lineage spans 3 epochs and 1,500 example instances; repeated epochs do not create new independent samples. The selected checkpoint was chosen from Dev before Test.

### Fixed 50-question Test

| Metric | P0 base | P1 LoRA |
|---|---:|---:|
| Answer F1 | 0.0602 | 0.3533 |
| Evidence F1 | 0.0270 | 0.1080 |
| JSON parse rate | 72% | 100% |
| Complete output protocol | 16/50 (32%) | 50/50 (100%) |
| Truncated output | 4/50 (8%) | 0/50 |
| Mean output tokens | 114.02 | 31.28 |
| Mean Answer-only inference latency | 2.690 s | 1.287 s |

The full-precision paired Answer F1 difference is **0.2931607975212872**, which rounds to **0.2932**. Subtracting independently rounded displayed scores gives 0.2931. Full-protocol failures receive zero score; the denominator remains all 50 tasks. Here Evidence F1 measures normalized, deduplicated whole-paragraph sets, unlike V1's original list matching; neither measures human citation entailment. A 2,000-draw paper-cluster paired bootstrap gives a 95% percentile interval **[0.1915, 0.4019]**, conditional on these 50 papers and the single training seed, without a p-value or universal-effectiveness claim.

### Interpreting the improvement

| Post hoc protocol stratum | Tasks | P0 Answer F1 | P1 Answer F1 | Contribution to the all-50 Answer difference |
|---|---:|---:|---:|---:|
| Both pass the full protocol | 16 | 0.1880 | 0.3238 | +0.043462 |
| P0 fails, P1 passes | 34 | 0.0000 | 0.3672 | +0.249699 |

The failed-to-pass stratum supplies about 85.17% of the observed Answer difference. This is descriptive arithmetic rather than proof of a format-only causal mechanism. On the 16 both-passing tasks, Evidence F1 changed **0.08428 → 0.08333**. Conditional slices explain the completed comparison and do not replace its all-50 main result or select subsequent methods.

The official Test contains **4 unanimous unanswerable questions**. P1 answered all four; zero abstention is therefore not evidence of strong answerability judgment. Dev diagnostics also found retrieval/schema coverage gaps. An exact reference miss is not automatically an officially unanswerable task.

Both Test arms used the same technical-continuation execution fingerprint, frozen inputs, prompt construction, budgets, decoding, and scorer. The base precision stayed BF16; the FP32 adapter branch is the treatment. Full historical input tensors and per-operation inference dtypes were not persisted, so bitwise equivalence is unconfirmed. The single run and differing output lengths do not establish a universal adapter-only speedup.

Independent human semantic review coverage is **0**; Citation Support Rate and Unsupported Claim Rate remain **unknown/null**. **DPO/GRPO were not trained.** The pre-Test development decision was to improve evidence coverage and independently reviewed examples first. Neither this Test nor the exposed Agent Final is available for further strategy selection.

The two studies use different official QASPER splits and scope. The checked Test paper and original-question identities have **zero overlap with the selected formal V1 tasks**, while all 50 Test papers already existed in the stored official-train corpus. Corpus storage is distinct from formal model-task exposure; pretraining exposure and broader semantic leakage remain unknown. Paper-level splitting within adaptation is not a claim of completely new source material. A future integration would need explicit Answer input/output and citation mapping, matched same-base controls, a new execution fingerprint, and a separately frozen untouched evaluation protocol. Existing scores cannot certify that integration.
