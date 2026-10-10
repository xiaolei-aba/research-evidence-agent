# Reproduction scopes

## Independent candidate: CPU engineering checks

Use Python 3.11 or newer, without installing external packages:

```bash
python examples/public_demo.py
python scripts/verify_cpu.py
```

The first command uses the evaluated `ResearchEvidenceAgent`, BM25, evidence memory and provenance verifier with project-authored synthetic text and `MockProvider`. It makes no model request; token usage is null and semantic support is unknown. The second command blocks real sockets before discovering the portable test suite. Tests use only included synthetic fixtures and Mock HTTP clients. Model weights, llama.cpp, CUDA and API keys are unnecessary.

The 47 research source modules retain their executable content. 46 are byte-identical; the scorer has only the reviewed provenance comment prepended, with byte-identical original body and identical AST. `runtime/` modules are retained for code review; Windows resource monitoring and execution depend on the original research environment and excluded worker/controllers. Importing them does not start a backend. They are not advertised as a portable GPU runner.

## Identical benchmark experiments: local research repository

Exact reproduction requires the separately preserved frozen task IDs/Gold, manifest and corpus versions, scoring/configuration/Prompt hashes, preregistration and result records, official weight and backend hashes, Windows environment and resource gates. These are not bundled into this code candidate. Aggregate scores and origin hashes are sufficient to inspect reporting consistency, not to independently recompute scores without predictions and Gold.

The research environment used Python 3.11.17, official Qwen2.5-1.5B-Instruct Q4_K_M and llama.cpp b9757 CUDA13.3, with one backend slot, `--cache-ram 0`, seed 11 and temperature 0. Input/output caps, total reservation, tools and replans stayed frozen across B4 on/off. Local experiment resume commands are deliberately absent from the public quickstart: all planned experiments are complete and career delivery does not rerun Final.

The source lock contains only URL/revision/relative path/byte count/SHA/retrieval time. Restoring public archives alone does not recreate the exact selected task set. Any future release of task-selection metadata or data extraction code must first pass rights and Gold/privacy review and receive owner authorization. The original research `.git`, raw data, Gold, responses, model/environment/binaries and machine-specific logs remain local.

## License and release provenance

The owner confirmed rights to license project contributions under Apache-2.0 and authorized the independent Public release under xiaolei-aba. Third-party rights are retained in LICENSE, NOTICE and LICENSES/SQuAD-MIT.txt. This confirmation is separate from personal claims of independently implementing every module.

RELEASE_MANIFEST.json locks every payload and records the audited baseline hash plus each authorized licensing/documentation change. The original research Git history is excluded; the public snapshot starts a new main history. All actual uploaded files must match the final manifest, and publication is accepted only after Public visibility, owner, remote HEAD and remote bytes are verified. These gates do not rerun Final or change the frozen scorer body.
