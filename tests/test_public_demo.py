"""Portable public demonstration checks; no data download, backend or sockets."""
from pathlib import Path
import importlib.util
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("public_demo", ROOT / "examples/public_demo.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class PublicDemoTests(unittest.TestCase):
    def test_evaluated_workflow_records_real_provenance_with_mock_boundary(self):
        result = demo.run_demo()
        self.assertEqual(result["answer"]["status"], "completed")
        self.assertEqual(len(result["evidence"]), 1)
        self.assertEqual(result["evidence"][0]["task_ids"], ["0:0", "0:1"])
        self.assertEqual(result["trace"]["tool_calls"], 4)
        self.assertIsNone(result["trace"]["token_usage"])
        self.assertTrue(result["citation_report"]["structural_valid"])
        self.assertFalse(result["citation_report"]["semantic_support_complete"])
        self.assertTrue(any(e["kind"] == "token_budget" for e in result["trace"]["events"]))


if __name__ == "__main__":
    unittest.main()
