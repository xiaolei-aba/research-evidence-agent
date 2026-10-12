import asyncio
import hashlib
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.mock_agent import DemoError, MockRuntime, atomic_write


def decoded_events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


class DemoEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="runtime_mock_ui_")
        self.root = Path(self.temporary.name)
        self.app = create_app(self.root, step_delay=0.025)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    def start(self):
        response = self.client.post("/api/runs", json={"task": "Analyze whether the proposed method improves baseline performance."})
        self.assertEqual(response.status_code, 201)
        return response.json()["run_id"]

    def wait_status(self, run_id, statuses):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = self.client.get(f"/api/runs/{run_id}").json()
            if row["state"]["status"] in statuses and (row["state"]["status"] == "FAILED" or row["state"]["current_stage"] == "complete"):
                return row
            time.sleep(0.01)
        self.fail("Synthetic CPU run did not reach a terminal presentation state")

    def make_failure(self):
        run_id = self.start()
        response = self.client.post(f"/api/runs/{run_id}/inject-failure")
        self.assertEqual(response.status_code, 200)
        return run_id, self.wait_status(run_id, {"FAILED"})

    def test_health_and_input_validation(self):
        health = self.client.get("/api/health").json()
        self.assertEqual(health["mode"], "engineering_mock")
        self.assertFalse(health["real_model_calls"])
        self.assertFalse(health["gpu"])
        self.assertFalse(health["outbound_api_calls"])
        self.assertEqual(self.client.post("/api/runs", json={"task": "   "}).status_code, 422)
        self.assertEqual(self.client.post("/api/runs", json={"task": "ok", "model": "unsafe"}).status_code, 422)

    def test_normal_run_and_every_checkpoint_sha(self):
        run_id = self.start()
        row = self.wait_status(run_id, {"SUCCESS"})
        self.assertEqual(row["state"]["completed_steps"], ["planner", "tool", "memory", "context", "answer", "critic"])
        self.assertEqual(row["state"]["memory_size"], 3)
        self.assertEqual(row["state"]["token_budget"], {"limit": 4096, "used": 2800})
        context = next(event for event in row["events"] if event["payload"].get("event") == "context_selected")
        self.assertEqual(context["payload"]["before_tokens"], 5200)
        self.assertEqual(context["payload"]["memory_retained_ids"], ["E1", "E2", "E3"])
        self.assertEqual(context["payload"]["selected_ids"], ["E1", "E2"])
        self.assertIn("fixture", context["payload"]["measurement"])
        fixture = row["failure_fixture"]
        self.assertEqual(sum(fixture[key] for key in ["Retrieval", "Context", "Tool", "Generation"]), fixture["denominator"])
        checkpoints = list((self.root / run_id / "checkpoints").glob("*.json"))
        self.assertEqual(len(checkpoints), 6)
        for checkpoint in checkpoints:
            digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            self.assertEqual(digest, checkpoint.stem)
            self.assertEqual(checkpoint.with_suffix(".json.sha256").read_text().strip(), digest)
        self.assertTrue(all(event["payload"]["engineering_mock"] for event in row["events"]))

    def test_timeout_recovery_no_committed_replay(self):
        run_id, failed = self.make_failure()
        self.assertEqual(failed["state"]["completed_steps"], ["planner"])
        self.assertTrue(failed["can_recover"])
        first_receipt = (self.root / run_id / "tool_receipt_1.json").read_bytes()
        response = self.client.post(f"/api/runs/{run_id}/recover")
        self.assertEqual(response.status_code, 200)
        recovered = self.wait_status(run_id, {"RECOVERED"})
        report = recovered["recovery_report"]
        self.assertTrue(report["success"])
        self.assertTrue(report["engineering_mock"])
        self.assertEqual(report["retry_attempts"], 1)
        for key in ["replayed_steps", "lost_steps", "duplicate_tool_calls"]:
            self.assertEqual(report[key], 0)
        self.assertEqual(first_receipt, (self.root / run_id / "tool_receipt_1.json").read_bytes())
        self.assertEqual(json.loads(first_receipt)["status"], "failed")
        kinds = [event["payload"].get("event") for event in recovered["events"]]
        self.assertEqual(kinds.count("plan_generated"), 1)
        self.assertEqual(kinds.count("tool_timeout"), 1)
        self.assertEqual(kinds.count("tool_call"), 1)
        self.assertIn("checkpoint_loaded", kinds)
        self.assertEqual(json.loads((self.root / run_id / "recovery_demo_report.json").read_text()), report)

    def test_terminal_failure_is_not_implicitly_resampled(self):
        run_id, failed = self.make_failure()
        before = len(failed["events"])
        time.sleep(0.12)
        same = self.client.get(f"/api/runs/{run_id}").json()
        self.assertEqual(same["state"]["status"], "FAILED")
        self.assertEqual(len(same["events"]), before)
        self.assertEqual(self.client.post(f"/api/runs/{run_id}/inject-failure").status_code, 409)

    def test_success_cannot_be_resampled_or_failed(self):
        run_id = self.start()
        self.wait_status(run_id, {"SUCCESS"})
        self.assertEqual(self.client.post(f"/api/runs/{run_id}/inject-failure").status_code, 409)
        self.assertEqual(self.client.post(f"/api/runs/{run_id}/recover").status_code, 409)

    def test_checkpoint_tamper_blocks_recovery(self):
        run_id, failed = self.make_failure()
        path = self.root / run_id / "checkpoints" / (failed["state"]["checkpoint_id"] + ".json")
        path.write_bytes(path.read_bytes() + b" ")
        response = self.client.post(f"/api/runs/{run_id}/recover")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "checkpoint_integrity_error")
        row = self.client.get(f"/api/runs/{run_id}").json()
        self.assertEqual(row["state"]["status"], "FAILED")
        self.assertEqual(len(row["events"]), len(failed["events"]))

    def test_checkpoint_identity_tamper_blocks_recovery(self):
        run_id, _ = self.make_failure()
        path = self.root / run_id / "HEAD.json"
        head = json.loads(path.read_text())
        head["run_id"] = "another_task"
        path.write_text(json.dumps(head))
        self.assertEqual(self.client.post(f"/api/runs/{run_id}/recover").status_code, 409)

    def test_two_recovery_requests_do_not_start_two_actors(self):
        run_id, _ = self.make_failure()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.client.post(f"/api/runs/{run_id}/recover").status_code, range(2)))
        self.assertEqual(sorted(responses), [200, 409])
        row = self.wait_status(run_id, {"RECOVERED"})
        self.assertEqual(sum(event["payload"].get("event") == "tool_call" for event in row["events"]), 1)

    def test_sse_reconnect_deduplicates_without_execution(self):
        run_id = self.start()
        row = self.wait_status(run_id, {"SUCCESS"})
        last = row["events"][-1]["event_id"]
        all_events = decoded_events(self.client.get(f"/api/runs/{run_id}/events"))
        self.assertEqual([event["event_id"] for event in all_events], list(range(1, last + 1)))
        resumed = decoded_events(self.client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "5"}))
        self.assertEqual([event["event_id"] for event in resumed], list(range(6, last + 1)))
        self.assertEqual(decoded_events(self.client.get(f"/api/runs/{run_id}/events?after={last}")), [])
        self.assertEqual(self.client.get(f"/api/runs/{run_id}/events?after={last+1}").status_code, 409)
        self.assertEqual(self.client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "-1"}).status_code, 400)
        self.assertEqual(len(self.client.get(f"/api/runs/{run_id}").json()["events"]), last)

    def test_sse_failed_then_recovered_keeps_old_failure(self):
        run_id, failed = self.make_failure()
        last = failed["events"][-1]["event_id"]
        self.assertIn("tool_timeout", [event["payload"].get("event") for event in decoded_events(self.client.get(f"/api/runs/{run_id}/events"))])
        self.client.post(f"/api/runs/{run_id}/recover")
        self.wait_status(run_id, {"RECOVERED"})
        resumed = decoded_events(self.client.get(f"/api/runs/{run_id}/events?after={last}"))
        self.assertTrue(all(event["event_id"] > last for event in resumed))
        self.assertIn("recovery_completed", [event["payload"].get("event") for event in resumed])

    def test_loopback_origin_and_host_guard(self):
        self.assertEqual(self.client.post("/api/runs", json={"task": "x"}, headers={"Origin": "https://example.invalid"}).status_code, 403)
        self.assertEqual(self.client.get("/api/health", headers={"Host": "example.invalid"}).status_code, 400)
        self.assertEqual(self.client.get("/api/runs/not-a-run").status_code, 404)


class CapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_does_not_evict_running_or_failed_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = MockRuntime(Path(temporary), step_delay=0.1, max_runs=1)
            try:
                await runtime.start("Synthetic fixture")
                with self.assertRaises(DemoError) as error:
                    await runtime.start("Second fixture")
                self.assertEqual(error.exception.status, 429)
                self.assertEqual(len(runtime.runs), 1)
            finally:
                await runtime.close()


    async def test_sse_cancellation_does_not_cancel_or_repeat_actor(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = MockRuntime(Path(temporary), step_delay=0.001)
            try:
                run = await runtime.start("Synthetic fixture")
                stream = run.journal.stream(run)
                self.assertIn("task_started", await anext(stream))
                await stream.aclose()
                await run.actor
                self.assertEqual(run.execution_counts["planner"], 1)
                self.assertEqual(run.execution_counts["tool"], 1)
            finally:
                await runtime.close()


class FilesystemTests(unittest.TestCase):
    def test_long_valid_windows_target_uses_short_sibling_temporary(self):
        with tempfile.TemporaryDirectory(prefix="ui_") as temporary:
            directory = Path(temporary)
            # Valid target < 260, but the old target-name + UUID temp exceeded
            # MAX_PATH. This regression does not change global Windows options.
            while len(str(directory)) < 178:
                directory = directory / "checkpoint_fixture"
            directory.mkdir(parents=True)
            target = directory / ("a" * 64 + ".json")
            self.assertLess(len(str(target)), 260)
            self.assertGreaterEqual(len(str(target)) + len(".tmp-") + 32, 260)
            atomic_write(target, b"{\"engineering_mock\":true}")
            self.assertEqual(target.read_bytes(), b"{\"engineering_mock\":true}")
            self.assertEqual(list(directory.glob(".tmp-*")), [])

    def test_nonfinite_delay_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            for invalid in [float("nan"), float("inf"), -1, 31]:
                with self.assertRaises(ValueError):
                    create_app(Path(temporary), step_delay=invalid)


if __name__ == "__main__":
    unittest.main()
