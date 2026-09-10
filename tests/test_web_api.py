"""Private API integration tests; the subprocess is an explicit test fixture."""
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from av_eval.web_jobs import CHECK_NAMES, JobManager, Settings, build_report, redact, save_json

HAS_WEB = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx", "PIL"))
ROOT = Path(__file__).resolve().parents[1]
TOKEN = "local-test-token-not-a-real-secret" * 2
ORIGIN = "https://xuanhaochang.github.io"


class ReportTests(unittest.TestCase):
    def test_unknown_checks_are_not_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "predictions.csv").write_text('GPT预测结果\n[]\n', encoding="utf-8")
            checks = [{"check_name": CHECK_NAMES[0], "decision": "not_detected", "execution_status": "failed"},
                      {"check_name": CHECK_NAMES[1], "decision": "detected", "execution_status": "ok"}]
            (root / "run.jsonl").write_text(json.dumps({"success": True, "classic_checks": checks}), encoding="utf-8")
            report = build_report(root)
            self.assertEqual(report["metrics"]["evaluable_checks"], 1)
            self.assertEqual(report["checks"][0]["decision"], "not_evaluable")
            self.assertEqual(report["checks"][-1]["decision"], "not_evaluable")
            self.assertEqual(report["metrics"]["coverage"], 0.1)

    def test_empty_prediction_is_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "predictions.csv").write_text('GPT预测结果\n""\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                build_report(root)

    def test_redacts_server_secrets_recursively(self):
        config = Settings(Path("/server/repo"), Path("/server/jobs"), Path(sys.executable), TOKEN)
        with patch.object(Settings, "runtime_env", return_value={"AVAGENT_API_KEY": "test-private-key"}):
            result = redact({"evidence": ["test-private-key /server/repo/input " + TOKEN]}, config)
        self.assertNotIn("test-private-key", json.dumps(result))
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn("/server/repo", json.dumps(result))


@unittest.skipUnless(HAS_WEB and shutil.which("ffmpeg"), "Install requirements-web.txt and ffmpeg for API tests")
class WebAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory()
        video = Path(cls.media.name) / "synthetic.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=size=64x64:duration=1:rate=10",
                        "-an", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", str(video)], check=True)
        cls.video = video.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.media.cleanup()

    def setUp(self):
        from fastapi.testclient import TestClient
        from av_eval.web_api import create_app
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        shutil.copyfile(ROOT / "tests/fixtures/web_runner.py", root / "run_avagent.py")
        self.settings = Settings(root, root / "jobs", Path(sys.executable), TOKEN)
        self.ready = patch.object(Settings, "readiness", return_value=[])
        self.ready.start()
        self.addCleanup(self.ready.stop)
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {"Authorization": "Bearer " + TOKEN, "Origin": ORIGIN}

    def submit(self, prompt="test fixture", **kwargs):
        return self.client.post("/api/jobs", headers=self.headers, data={"prompt": prompt},
                                files={"video": ("video.mp4", self.video, "video/mp4")}, **kwargs)

    def wait_for(self, job_id, expected):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            result = self.client.get(f"/api/jobs/{job_id}", headers=self.headers).json()
            if result["status"] in expected:
                return result
            time.sleep(.03)
        self.fail(f"Task did not reach {expected}: {result['status']}")

    def test_authentication_required_before_upload(self):
        for path in ("/api/health", "/api/jobs", "/api/jobs/invalid/report.jsonl"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post("/api/jobs", content=b"invalid multipart").status_code, 401)

    def test_cors_errors_and_preflight(self):
        response = self.client.get("/api/jobs", headers={"Origin": ORIGIN})
        self.assertEqual(response.headers["access-control-allow-origin"], ORIGIN)
        denied = self.client.get("/api/jobs", headers={**self.headers, "Origin": "https://untrusted.example"})
        self.assertEqual(denied.status_code, 403)
        preflight = self.client.options("/api/jobs", headers={"Origin": ORIGIN,
            "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"})
        self.assertEqual(preflight.status_code, 200)

    def test_ngrok_browser_header_preserves_authentication_and_origin_checks(self):
        header = {"ngrok-skip-browser-warning": "1"}
        preflight = self.client.options("/api/health", headers={"Origin": ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,ngrok-skip-browser-warning"})
        self.assertEqual(preflight.status_code, 200)
        self.assertIn("ngrok-skip-browser-warning", preflight.headers["access-control-allow-headers"])
        self.assertEqual(self.client.get("/api/health", headers=header).status_code, 401)
        allowed = self.client.get("/api/health", headers={**self.headers, **header})
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["service"], "avagent-eval")
        denied = self.client.get("/api/health", headers={**self.headers, **header,
            "Origin": "https://untrusted.example"})
        self.assertEqual(denied.status_code, 403)

    def test_successful_upload_cli_job_and_export(self):
        response = self.submit()
        self.assertEqual(response.status_code, 202, response.text)
        job = self.wait_for(response.json()["id"], {"completed", "failed"})
        self.assertEqual(job["status"], "completed", job)
        self.assertEqual(job["report"]["metrics"]["evaluable_checks"], 1)
        self.assertEqual(job["report"]["metrics"]["issue_count"], 0)
        self.assertEqual(len(job["report"]["checks"]), 10)
        export = self.client.get(f"/api/jobs/{job['id']}/report.jsonl", headers=self.headers)
        self.assertEqual(export.status_code, 200)
        self.assertEqual(json.loads(export.text)["id"], job["id"])
        self.assertNotIn(str(self.settings.data), export.text)
        self.assertEqual(len(self.client.get("/api/jobs", headers=self.headers).json()), 1)

    def test_runner_failure_has_no_report(self):
        response = self.submit("[FAIL]")
        job = self.wait_for(response.json()["id"], {"failed"})
        self.assertIsNone(job["report"])
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/report.jsonl", headers=self.headers).status_code, 409)

    def test_cancel_running_process(self):
        response = self.submit("[WAIT]")
        job = self.wait_for(response.json()["id"], {"running"})
        result = self.client.post(f"/api/jobs/{job['id']}/cancel", headers=self.headers)
        self.assertEqual(result.json()["status"], "cancelled")
        self.assertIsNone(result.json()["report"])

    def test_missing_configuration_blocks_submission(self):
        with patch.object(Settings, "readiness", return_value=["AVAGENT_VISUAL_MODEL"]):
            self.assertEqual(self.submit().status_code, 503)

    def test_oversized_body_rejected_before_parsing(self):
        result = self.client.post("/api/jobs", headers={**self.headers, "Content-Length": str(129 * 1024 * 1024)}, content=b"tiny")
        self.assertEqual(result.status_code, 413)

    def test_invalid_video_rejected(self):
        result = self.client.post("/api/jobs", headers=self.headers, data={"prompt": "example"},
                                  files={"video": ("video.mp4", b"not-a-video", "video/mp4")})
        self.assertEqual(result.status_code, 422)
        self.assertFalse(list(self.settings.data.glob("upload-*")))

    def test_extra_fields_cannot_select_runner_or_path(self):
        result = self.client.post("/api/jobs", headers=self.headers,
                                  data={"prompt": "example", "runner": "/bin/false"},
                                  files={"video": ("video.mp4", self.video, "video/mp4")})
        self.assertIn(result.status_code, (400, 422))

    def test_unknown_job_and_no_raw_log_route(self):
        self.assertEqual(self.client.get("/api/jobs/not-a-job", headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/" + "a" * 32 + "/runner.log", headers=self.headers).status_code, 404)

    def test_job_quota(self):
        manager = self.client.app.state.jobs
        manager.settings = replace(self.settings, max_saved_jobs=0)
        self.assertEqual(self.submit().status_code, 429)

    def test_restart_marks_unfinished_jobs_failed(self):
        job_id = "a" * 32
        directory = self.settings.data / job_id
        directory.mkdir()
        save_json(directory / "job.json", {"id": job_id, "status": "running"})
        manager = JobManager(self.settings)
        try:
            self.assertEqual(manager.get(job_id)["status"], "failed")
        finally:
            manager.close()

    def connect_events(self, job_id):
        return self.client.websocket_connect(f"/api/jobs/{job_id}/events", headers={"Origin": ORIGIN})

    def test_websocket_rejects_origin_and_query_credentials(self):
        from starlette.websockets import WebSocketDisconnect
        for origin in (None, "https://untrusted.example", "null"):
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect("/api/jobs/unknown/events", headers={"Origin": origin} if origin else {}):
                    self.fail("Untrusted origin accepted")
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/api/jobs/unknown/events?token=never-in-url", headers={"Origin": ORIGIN}):
                self.fail("Query-string credential accepted")

    def test_websocket_checks_auth_before_job_lookup(self):
        from starlette.websockets import WebSocketDisconnect
        with self.connect_events("unknown") as connection:
            connection.send_json({"type": "authenticate", "token": "incorrect"})
            with self.assertRaises(WebSocketDisconnect) as error:
                connection.receive_json()
            self.assertEqual(error.exception.code, 4401)
        with self.connect_events("unknown") as connection:
            connection.send_json({"type": "authenticate", "token": TOKEN})
            with self.assertRaises(WebSocketDisconnect) as error:
                connection.receive_json()
            self.assertEqual(error.exception.code, 4404)

    def test_websocket_rejects_malformed_and_binary_auth_frames(self):
        from starlette.websockets import WebSocketDisconnect
        for payload in ('not json', '[]', '{"type":"authenticate","token":123}', b'binary'):
            with self.connect_events("unknown") as connection:
                if isinstance(payload, bytes):
                    connection.send_bytes(payload)
                else:
                    connection.send_text(payload)
                with self.assertRaises(WebSocketDisconnect) as error:
                    connection.receive_json()
                self.assertIn(error.exception.code, (4400, 4401))

    def test_websocket_authentication_timeout_and_capacity(self):
        from fastapi.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect
        from av_eval.web_api import create_app
        settings = replace(self.settings, event_auth_timeout_sec=.05, max_event_connections=1)
        with TestClient(create_app(settings)) as client:
            with client.websocket_connect("/api/jobs/unknown/events", headers={"Origin": ORIGIN}) as connection:
                with self.assertRaises(WebSocketDisconnect):
                    with client.websocket_connect("/api/jobs/unknown/events", headers={"Origin": ORIGIN}):
                        self.fail("Exceeded connection limit")
                with self.assertRaises(WebSocketDisconnect) as error:
                    connection.receive_json()
                self.assertEqual(error.exception.code, 4408)
        self.assertEqual(client.app.state.event_connections, 0)

    def test_websocket_pushes_completion_without_status_queries(self):
        job_id = self.submit("[DELAY]").json()["id"]
        with self.connect_events(job_id) as connection:
            connection.send_json({"type": "authenticate", "token": TOKEN})
            while True:
                message = connection.receive_json()
                self.assertEqual(message["type"], "job")
                self.assertEqual(set(message["job"]), {"id", "status", "started_at", "finished_at"})
                if message["job"]["status"] == "completed":
                    break
            self.assertNotIn(TOKEN, json.dumps(message))
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}", headers=self.headers).json()["status"], "completed")

    def test_websocket_reconnect_snapshots_missed_failure(self):
        job_id = self.submit("[FAIL]").json()["id"]
        self.wait_for(job_id, {"failed"})
        with self.connect_events(job_id) as connection:
            connection.send_json({"type": "authenticate", "token": TOKEN})
            self.assertEqual(connection.receive_json()["job"]["status"], "failed")

    def test_websocket_cancel_and_disconnect_do_not_cancel_other_jobs(self):
        job_id = self.submit("[WAIT]").json()["id"]
        self.wait_for(job_id, {"running"})
        manager = self.client.app.state.jobs
        with self.connect_events(job_id) as connection:
            connection.send_json({"type": "authenticate", "token": TOKEN})
            self.assertEqual(connection.receive_json()["job"]["status"], "running")
        self.assertEqual(manager.get(job_id)["status"], "running")
        self.assertFalse(manager.listeners)
        with self.connect_events(job_id) as connection:
            connection.send_json({"type": "authenticate", "token": TOKEN})
            connection.receive_json()
            self.client.post(f"/api/jobs/{job_id}/cancel", headers=self.headers)
            self.assertEqual(connection.receive_json()["job"]["status"], "cancelled")

    def test_websocket_heartbeat_on_same_connection_and_invalid_frames(self):
        from fastapi.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect
        from av_eval.web_api import create_app
        with TestClient(create_app(replace(self.settings, event_heartbeat_sec=.05))) as client:
            job_id = client.post("/api/jobs", headers=self.headers, data={"prompt": "[WAIT]"},
                                 files={"video": ("video.mp4", self.video, "video/mp4")}).json()["id"]
            with client.websocket_connect(f"/api/jobs/{job_id}/events", headers={"Origin": ORIGIN}) as connection:
                connection.send_json({"type": "authenticate", "token": TOKEN})
                while connection.receive_json()["type"] != "heartbeat":
                    pass
                connection.send_json({"type": "pong"})
                self.assertEqual(connection.receive_json()["type"], "heartbeat")
                connection.send_json({"type": "arbitrary-command"})
                with self.assertRaises(WebSocketDisconnect) as error:
                    connection.receive_json()
                self.assertEqual(error.exception.code, 4400)


if __name__ == "__main__":
    unittest.main()
