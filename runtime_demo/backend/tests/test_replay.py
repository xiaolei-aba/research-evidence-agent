"""CPU-only release checks. Never import a model, scorer or experiment actor."""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app import create_app
from backend.mock_agent import DemoError, MockRuntime
from backend.replay import ReplayCatalog, validate_event

REPLAY = Path(__file__).resolve().parents[2] / "data" / "replay"


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="release_replay_")
        self.root = Path(self.temporary.name)
        self.replay = self.root / "replay"
        shutil.copytree(REPLAY, self.replay)
        self.catalog = ReplayCatalog(self.replay)

    def tearDown(self):
        self.temporary.cleanup()

    def mutate_manifest(self, mutate):
        path = self.replay / "trace_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        mutate(manifest)
        content = json.dumps(manifest).encode("utf-8")
        path.write_bytes(content)
        (self.replay / "trace_manifest.sha256").write_text(hashlib.sha256(content).hexdigest(), encoding="ascii")

    def mutate_trace(self, public_id, mutate):
        manifest = json.loads((self.replay / "trace_manifest.json").read_text(encoding="utf-8"))
        entry = next(e for e in manifest["traces"] if e["public_id"] == public_id)
        path = self.replay / entry["file"]
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        mutate(events)
        content = ("\n".join(json.dumps(e) for e in events) + "\n").encode("utf-8")
        path.write_bytes(content)
        self.mutate_manifest(lambda m: next(e for e in m["traces"] if e["public_id"] == public_id).update(sha256=hashlib.sha256(content).hexdigest(), event_count=len(events)))

    def assert_unavailable(self):
        with self.assertRaises(DemoError) as caught:
            self.catalog.list()
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(caught.exception.code, "replay_integrity_unavailable")

    def test_released_histories_keep_their_actual_source_boundary(self):
        entries = self.catalog.list()["replays"]
        self.assertEqual({e["source_kind"] for e in entries}, {"real_model_dev_history", "engineering_mock_history"})
        for entry in entries:
            result = self.catalog.get(entry["public_id"])
            self.assertTrue(result["readonly"])
            self.assertFalse(result["model_calls"])
            self.assertEqual(len(result["events"]), entry["event_count"])
            self.assertTrue(all(e["timestamp"] is None for e in result["events"]))
            self.assertNotIn("file", result["manifest"])
        formal = self.catalog.get("dev_generation_failure")["events"]
        self.assertFalse(any(e["event"] in {"checkpoint_restored", "recovery_verified"} for e in formal))
        terminal = next(e for e in formal if e["metadata"].get("terminal_status"))
        self.assertEqual(terminal["metadata"]["error_category"], "GenerationLengthFailure")
        self.assertEqual(terminal["metadata"]["terminal_status"], "failed")

    def test_mock_history_does_not_duplicate_preserved_trace_prefix(self):
        events = self.catalog.get("engineering_process_restart")["events"]
        indices = [e["metadata"]["source_event_index"] for e in events if e["metadata"].get("source_record") == "agent_trace"]
        self.assertEqual(indices, list(range(22)))
        report = events[-1]
        self.assertEqual(report["event"], "recovery_verified")
        self.assertFalse(report["metadata"]["real_llm_used"])
        self.assertTrue(report["metadata"]["mechanism_checks_passed"])
        for key in ["replayed_steps", "lost_steps", "duplicate_tool_calls"]:
            self.assertEqual(report["metadata"][key], 0)

    def test_manifest_tamper_fails_closed(self):
        path = self.replay / "trace_manifest.json"
        path.write_bytes(path.read_bytes() + b" ")
        self.assert_unavailable()

    def test_trace_tamper_fails_closed(self):
        path = self.replay / "example_trace.jsonl"
        path.write_bytes(path.read_bytes() + b" ")
        self.assert_unavailable()

    def test_missing_trace_has_no_mock_fallback(self):
        (self.replay / "engineering_recovery.jsonl").unlink()
        self.assert_unavailable()
        self.assertFalse((self.root / "runs").exists())

    def test_unknown_trace_id_is_not_a_path_lookup(self):
        for unknown in ["missing", "../trace_manifest.json", "..%2f", "/private", "..\\trace_manifest.json"]:
            with self.assertRaises(DemoError) as caught:
                self.catalog.get(unknown)
            self.assertEqual(caught.exception.status, 404)

    def test_manifest_path_escape_blocked_even_after_rehash(self):
        self.mutate_manifest(lambda m: m["traces"][0].update(file="../private.jsonl"))
        self.assert_unavailable()

    def test_symlink_escape_blocked_where_supported(self):
        original = self.replay / "example_trace.jsonl"
        escaped = self.root / "escaped.jsonl"
        escaped.write_bytes(original.read_bytes())
        original.unlink()
        try:
            original.symlink_to(escaped)
        except OSError:
            self.skipTest("Windows symlink privilege unavailable; lexical path escape is tested separately.")
        self.assert_unavailable()

    def test_raw_fields_blocked_even_after_matching_sha(self):
        self.mutate_trace("dev_generation_failure", lambda rows: rows[0]["metadata"].update(raw_response="not permitted"))
        self.assert_unavailable()

    def test_extra_event_fields_blocked_even_after_matching_sha(self):
        self.mutate_trace("dev_generation_failure", lambda rows: rows[0].update(task_id="not permitted"))
        self.assert_unavailable()

    def test_fabricated_formal_recovery_blocked_even_after_matching_sha(self):
        self.mutate_trace("dev_generation_failure", lambda rows: rows[0].update(component="Checkpoint", event="checkpoint_restored", status="recovered"))
        self.assert_unavailable()

    def test_nonfinite_string_or_bool_metadata_numbers_are_rejected(self):
        for invalid in [float("nan"), float("inf"), -1, "500", True]:
            with self.assertRaises(ValueError):
                validate_event({"timestamp": None, "component": "Runtime", "event": "task_started", "status": "running", "metadata": {"stage": "start", "duration_ms": invalid}})

    def test_no_private_text_fields_in_release(self):
        raw = "\n".join(p.read_text(encoding="utf-8") for p in self.replay.glob("*.json*"))
        for forbidden in ['"task_id"', '"query"', '"content"', '"raw_response"', '"gold"', '"pid"', '"source_path"', "Users" + chr(92), "D" + chr(58) + chr(92), "C" + chr(58) + chr(92)]:
            self.assertNotIn(forbidden, raw)

    def test_replay_endpoints_never_start_or_recover_actor(self):
        data = self.root / "new_session"
        with patch.object(MockRuntime, "start", side_effect=AssertionError("actor must not start")), patch.object(MockRuntime, "recover", side_effect=AssertionError("actor must not recover")), patch.object(MockRuntime, "inject", side_effect=AssertionError("actor must not inject")):
            with TestClient(create_app(data, replay_root=self.replay, step_delay=0)) as client:
                before = list(data.iterdir())
                self.assertEqual(client.get("/api/replays").status_code, 200)
                for public_id in ["dev_generation_failure", "engineering_process_restart"]:
                    response = client.get(f"/api/replays/{public_id}")
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.json()["readonly"])
                self.assertEqual(client.get("/api/replays/missing").status_code, 404)
                self.assertEqual(client.post("/api/replays/engineering_process_restart/recover").status_code, 405)
                self.assertEqual(list(data.iterdir()), before)

    def test_api_reports_corruption_instead_of_fallback(self):
        (self.replay / "trace_manifest.sha256").write_text("0" * 64, encoding="ascii")
        with TestClient(create_app(self.root / "new_session", replay_root=self.replay, step_delay=0)) as client:
            response = client.get("/api/replays")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"]["code"], "replay_integrity_unavailable")
            self.assertEqual(client.app.state.runtime.total_runs, 0)


class HostedPresentationTests(unittest.TestCase):
    def test_host_and_same_origin_guard_for_opt_in_space(self):
        with tempfile.TemporaryDirectory() as temporary:
            with TestClient(create_app(Path(temporary), mode="space", step_delay=0.1)) as client:
                headers = {"Host": "owned-example.hf.space", "Origin": "https://owned-example.hf.space"}
                self.assertEqual(client.get("/api/health", headers={"Host": "owned-example.hf.space"}).status_code, 200)
                self.assertEqual(client.post("/api/runs", json={"task": "Synthetic illustration"}, headers=headers).status_code, 201)
                self.assertEqual(client.post("/api/runs", json={"task": "x"}, headers={"Host": "owned-example.hf.space", "Origin": "https://different-space.hf.space"}).status_code, 403)
                self.assertEqual(client.get("/api/health", headers={"Host": "example.invalid"}).status_code, 400)
                self.assertEqual(client.app.state.runtime.total_runs, 1)

    def test_default_local_mode_does_not_allow_space_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            with TestClient(create_app(Path(temporary), step_delay=0)) as client:
                self.assertEqual(client.get("/api/health", headers={"Host": "example.hf.space"}).status_code, 400)
                self.assertEqual(client.get("/api/health").json()["deployment_mode"], "local")

    def test_body_and_task_caps_reject_before_run_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            with TestClient(create_app(Path(temporary), step_delay=0)) as client:
                self.assertEqual(client.post("/api/runs", json={"task": "a" * 501}).status_code, 422)
                self.assertEqual(client.post("/api/runs", content=b"x" * 4097, headers={"Content-Type": "application/json"}).status_code, 413)
                self.assertEqual(client.app.state.runtime.total_runs, 0)
                self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_local_custom_data_root_env(self):
        with tempfile.TemporaryDirectory() as temporary:
            chosen = Path(temporary) / "local_only"
            with patch.dict("os.environ", {"DEMO_DATA_ROOT": str(chosen)}):
                with TestClient(create_app(step_delay=0)) as client:
                    self.assertEqual(client.app.state.runtime.data_root, chosen.resolve())


class PersistentCapsTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_run_retained_and_cap_survives_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = MockRuntime(root, step_delay=0, max_runs=1)
            run = await runtime.start("Synthetic fixture")
            await run.actor
            self.assertEqual(run.state.status, "SUCCESS")
            history = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
            with self.assertRaises(DemoError) as caught:
                await runtime.start("Another fixture")
            self.assertEqual(caught.exception.status, 429)
            self.assertEqual(sorted(p.relative_to(root).as_posix() for p in root.rglob("*")), history)
            await runtime.close()
            restarted = MockRuntime(root, step_delay=0, max_runs=1)
            with self.assertRaises(DemoError):
                await restarted.start("No reset workaround")
            self.assertEqual(restarted.total_runs, 1)
            await restarted.close()

    async def test_total_event_cap_blocks_further_disk_growth(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = MockRuntime(Path(temporary), step_delay=0)
            run = await runtime.start("Synthetic fixture")
            await run.actor
            async with run.lock:
                run.journal.max_total_events = run.journal.sequence
                before = run.journal.path.read_bytes()
                with self.assertRaises(RuntimeError):
                    run.emit("Runtime", "complete", "success", event="extra")
                self.assertEqual(before, run.journal.path.read_bytes())
            await runtime.close()


if __name__ == "__main__":
    unittest.main()
