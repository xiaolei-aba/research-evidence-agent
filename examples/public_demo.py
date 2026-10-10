"""CPU-only synthetic demonstration of the evaluated workflow; no real LLM."""
from pathlib import Path
import json
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))
from deepresearch.agent.research import ResearchEvidenceAgent
from deepresearch.benchmarks.runtime import BenchmarkCorpusReader
from deepresearch.llm.provider import MockProvider
from deepresearch.models import UserQuery
from deepresearch.retrieval.bm25 import BM25SearchProvider


def run_demo():
    reader = BenchmarkCorpusReader(
        {"synthetic-note": [{"unit_id": "synthetic-note:p0", "locator": "paragraph0",
                              "text": "Alpha retrieval uses exact source identifiers."}]},
        revision="synthetic-v1", license="project-authored synthetic example; Apache-2.0",
        source_url="https://example.invalid/synthetic-note",
    )
    retriever = BM25SearchProvider(reader)
    # A repeated planned subtask exercises memory deduplication in the real workflow.
    provider = MockProvider({"plan": [json.dumps({"queries": ["Alpha retrieval", "Alpha retrieval"]})]})
    with patch("socket.socket", side_effect=AssertionError("Network disabled in synthetic demo")):
        return ResearchEvidenceAgent(provider, retriever, reader, deduplicate=True).run(
            UserQuery("Alpha retrieval", "synthetic-demo"))


if __name__ == "__main__":
    result = run_demo()
    print(json.dumps({"mode": "synthetic engineering demo; not measured Agent quality",
                      "answer": result["answer"], "evidence_items": len(result["evidence"]),
                      "tool_calls": result["trace"]["tool_calls"],
                      "token_usage": result["trace"]["token_usage"],
                      "semantic_support_complete": result["citation_report"]["semantic_support_complete"]},
                     ensure_ascii=False, indent=2))
