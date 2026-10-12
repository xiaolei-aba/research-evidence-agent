# Reproduction scopes

## CPU Runtime dashboard

Follow [the Demo README](../runtime_demo/README.md) for dependency installation and launch commands, and [the walkthrough](demo_guide.md) for a three-minute presentation.

The dashboard runs synthetic Mock execution and sanitized historical replay on CPU. It makes no model or external inference API requests. It is a Runtime visualization, not a benchmark runner. Mock outputs are not model results, and recovery demonstrations do not establish a production recovery rate or SLA.

## Legacy portable engineering checks

The earlier CPU demonstration and test entrypoint remain available from the repository root with Python 3.11 or newer:

```bash
python examples/public_demo.py
python scripts/verify_cpu.py
```

The demonstration uses `ResearchEvidenceAgent`, BM25, evidence memory, and provenance verification with project-authored synthetic text and `MockProvider`. It makes no model request; token usage is null and semantic support is unknown. The test entrypoint blocks real sockets during discovery and execution. These commands do not require model weights, llama.cpp, CUDA, or API credentials.

The source exposes the Agent and Runtime implementation for inspection and CPU verification. Windows resource monitoring and the original experiment execution controllers have a separate environment; these commands are not a portable GPU benchmark runner.

## Benchmark result reproduction

The original selected task identities, Gold, raw predictions, model files, and machine-specific experiment artifacts are not distributed. Released aggregate scores cannot independently reconstruct or recompute the frozen task-level results. The [benchmark source lock](../data/benchmarks/source_lock.json) contains upstream URLs, revisions, relative paths, byte counts, hashes, and retrieval times, not benchmark records or paper text. Restoring upstream archives alone does not recreate the selected task set.

See [Experiments](experiments.md) for the frozen configuration, benchmark definitions, and denominators; see [Limitations](limitations.md) for the rights and reproducibility boundaries. The Answer-only SFT study is independent of the Agent experiment and is not installed or executed by this dashboard.

## Public release and licensing

This Runtime update continues the existing GitHub commit chain. Earlier release assets and documentation remain accessible in that history. No old release manifest is presented as a lock for the updated repository tree.

Rights-cleared project contributions use [Apache-2.0](../LICENSE). Third-party obligations remain in [NOTICE](../NOTICE) and [LICENSES](../LICENSES); they do not grant redistribution rights for omitted models or datasets.
