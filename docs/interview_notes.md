# Interview notes

## A concise project introduction

**English:** “Built an evidence-grounded Agent Runtime for long-horizon tasks, integrating planning, tool execution, state management, checkpoint recovery and trace-based evaluation. I kept system-level Agent experiments separate from an Answer-only LoRA study, preserved failures, and used the dashboard to make execution boundaries inspectable.”

**中文：**“构建面向长程知识任务的证据驱动 Agent Runtime，实现任务规划、工具调用、状态管理、Checkpoint 恢复和 Trace 评测闭环。系统实验与 Answer-only LoRA 实验分开报告；保留失败与负结果，并通过 Dashboard 展示执行状态和恢复边界。”

Be ready to explain the implementation and its tradeoffs, distinguish measured results from engineering demonstrations, and show the code that supports each technical claim.

## Questions and grounded answers

### Why do more Agent modules sometimes hurt?

Planning, memory, and critique add requests, evidence, and failure surfaces. On the frozen Agent Final, QASPER B4 improved against B4off but remained below B1: Answer F1 **0.1345 versus 0.0747 versus 0.1672**. SciFact showed the same ordering. This is evidence against “more modules always help” under this small-model budget; it does not isolate every module's causal effect. Inspect stage-level budgets and truncation before proposing another loop.

### Why does Context management matter?

Stored evidence need not all enter each model request. The implementation counts the full rendered request and can evict whole source units while preserving provenance and citation scope. Yet a smaller request can still omit useful evidence or cause more downstream work. The matched Dev Context candidate reduced failures **59/76 → 56/76** but increased tokens **19.399%**, so it remains **off by default**.

### What is the difference between Memory and model memory?

Evidence Memory is an explicit task store: content, source/version, hashes, locators, and ordering. Checkpoints also store the plan, cursor, budgets, and receipts. This does not update model weights. The independent 0.5B LoRA study changes a model branch; it has not been plugged into the 1.5B Agent.

### How do checkpoints avoid duplicate tool calls?

They preserve a cursor, request identity, and committed receipts. Recovery verifies task/configuration fingerprints and SHA before reusing a saved outcome. If a dispatched request has an intent but no recorded receipt, the outcome is ambiguous and the runtime blocks rather than silently repeating it. This protects committed boundaries; it is not a distributed exactly-once guarantee for arbitrary external writes.

### What does the recovery demonstration prove?

The interactive UI proves controlled synthetic state transitions and checkpoint loading inside one backend session. Historical CPU Mock replay can show a separately recorded process-restart fixture. Neither establishes real-model quality or a production recovery rate. Failed synthetic tool attempts and new attempts of uncommitted work are visible, while already committed steps remain preserved.

### How does Trace help diagnose failures?

It connects stage, request, native input count, reservation, outcome, finish state, and error category. An input-budget rejection occurs before generation; a length-truncated response occurs after generation; a citation-schema rejection is another boundary. These should not be collapsed into “the model reasoned incorrectly.” Technical tool success also does not establish retrieval coverage or semantic answer support.

### Is a valid citation a supported claim?

No. A registered ID and matching source hashes establish provenance and permitted scope. Benchmark paragraph/sentence overlap is another automatic proxy. Semantic entailment needs independent review or a separately validated verification method. This project's independent human semantic review coverage is **0**; Citation Support Rate and Unsupported Claim Rate remain **unknown/null**.

### What does the LoRA result say?

On a fixed **50-question Answer-only Test**, the 0.5B base-to-LoRA comparison changed Answer F1 **0.0602 → 0.3533** and full-protocol completion **16/50 → 50/50**. Most observed score difference comes from tasks moving from protocol failure to passing output. It does not show the whole Agent improved, does not show 0.5B beat 1.5B, and does not establish human semantic accuracy. All four unanimous official unanswerable Test questions were answered by P1, so zero abstention is not excellent answerability handling.

### Why not directly train DPO/GRPO?

The pre-Test Train/Dev decision identified evidence-coverage and source-unit gaps and lacked independently reviewed preference labels or a verified semantic reward. Format success is not a sufficient reward for evidence-grounded correctness. DPO/GRPO were not trained. Any future method needs Train/Dev-grounded development, matched extra-budget controls, and a new untouched evaluation protocol.

### Could the SFT model replace the Agent's Answer model?

Not directly. It is a different-size model and backend with a different prompt, evidence representation, and citation schema. An integration needs an explicit Answer-only interface, provenance-preserving citation mapping, native budget accounting, same-base P0/P1 controls, and a new fingerprint/test protocol. Existing results do not certify that integration, and the Adapter cannot be attached to the 1.5B GGUF as though it were the same base.

### How do resource guards differ from memory-leak diagnosis?

Available RAM is a system quantity; Private Bytes and Working Set describe different process properties; GPU memory is separate again. A fall in available RAM alone does not prove a leak. The research run kept its **1.25 GiB** stop threshold and measured each role separately. Full-state checkpoints and retained aggregate results have known storage/memory costs; infinite-stream scalability was not demonstrated.

## Resume wording with boundaries

**中文：**“实现证据驱动 Agent Runtime，集成 Planner、BM25 工具、Evidence Memory、状态/Checkpoint、Trace 与固定分母评测；完成 1,034 条系统任务观察，保留 B4 优于 B4off 但未超过 B1 的负结果；另以 152 条配对 Dev 观察检验 Context 质量/成本取舍。”

**English:** “Implemented an evidence-grounded Agent Runtime with planning, BM25 tools, evidence memory, state/checkpoints and trace-based evaluation; analyzed 1,034 system observations and a separate 152-observation matched Dev runtime study, preserving failed outcomes and quality/cost tradeoffs.”

If the Answer study is relevant to the role, describe it separately: “Ran an independent 0.5B BF16 Answer-only LoRA pilot with 500 training examples and a fixed 50-question Test; Answer F1 changed from 0.0602 to 0.3533, with semantic citation support unreviewed.”

All figures above are historical aggregates from [Experiments](experiments.md) and [the full-precision summary](results_summary.json). They are not results from the dashboard. Avoid claims of SOTA, production readiness, a human-verified citation accuracy, trained DPO/GRPO, or an already demonstrated model/system joint improvement.
