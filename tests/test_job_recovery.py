from contextlib import contextmanager
from io import BytesIO
import os
import threading
from unittest.mock import patch

from app import create_app
from feedback.storage import atomic_json
from tests.helpers import BaseTest, workspace_client


class JobRecoveryTests(BaseTest):
    options = {"start": "2026-10-19", "end": "2026-10-22", "confirmed": False}

    def setUp(self):
        super().setUp()
        self.app = create_app({"TESTING": True, "STORAGE_ROOT": self.root / "web"})
        self.client = workspace_client(self.app)
        self.client.get("/")
        with self.client.session_transaction() as session:
            self.headers = {"X-CSRF-Token": session["csrf"]}
        key = patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-only-recovery-key"})
        key.start()
        self.addCleanup(key.stop)

    def upload(self):
        response = self.client.post("/api/games/baloot/uploads", headers=self.headers,
            data={"file": (BytesIO(self.raw.to_csv(index=False).encode()), "sample.csv")})
        self.assertEqual(response.status_code, 201, response.get_json())
        metadata = response.get_json()
        return "/api/games/baloot/uploads/" + metadata["run"], metadata

    @contextmanager
    def blocked_report(self, failure=None):
        started, release = threading.Event(), threading.Event()
        threads = []
        thread_class = threading.Thread

        def thread(*args, **kwargs):
            worker = thread_class(*args, **kwargs)
            threads.append(worker)
            return worker

        def run(folder, config, start, end, confirmed, job_id, progress, should_stop):
            progress(stage="processing", message="测试处理进度", done=1, total=4)
            started.set()
            if not release.wait(5):
                raise AssertionError("Test did not release its report worker")
            if should_stop():
                raise InterruptedError("测试任务已停止")
            if failure is not None:
                raise failure
            return {"total": 2, "previous_total": 2}

        with patch("feedback.jobs.run_report", side_effect=run), patch(
                "feedback.jobs.threading.Thread", side_effect=thread):
            try:
                yield started, release, threads
            finally:
                release.set()
                for worker in threads:
                    worker.join(timeout=5)
                    self.assertFalse(worker.is_alive())

    def start(self, base):
        response = self.client.post(base + "/reports", json=self.options, headers=self.headers)
        self.assertEqual(response.status_code, 202, response.get_json())
        return response.get_json()

    def close(self):
        return self.client.post("/api/workspaces/close", headers=self.headers)

    def test_close_removes_only_its_page_and_revokes_access(self):
        base, meta = self.upload()
        folder = self.app.extensions["storage"].folder("baloot", meta["run"])
        other = workspace_client(self.app)
        with other.session_transaction() as session:
            headers = {"X-CSRF-Token": session["csrf"]}
        response = other.post("/api/games/baloot/uploads", headers=headers,
            data={"file": (BytesIO(self.raw.to_csv(index=False).encode()), "other.csv")})
        other_folder = self.app.extensions["storage"].folder("baloot", response.get_json()["run"])
        self.assertEqual(other.get(base).status_code, 404)
        self.assertEqual(self.close().status_code, 200)
        self.assertFalse(folder.exists())
        self.assertTrue(other_folder.exists())
        self.assertEqual(self.client.get(base).status_code, 410)

    def test_tabs_and_other_sessions_cannot_access_each_other(self):
        import re
        base, _ = self.upload()
        page = self.client.get("/games/baloot").get_data(as_text=True)
        token = re.search(r'data-workspace="([^"]+)"', page).group(1)
        self.assertEqual(self.client.get(base, headers={"X-Workspace": token}).status_code, 404)
        outsider = workspace_client(self.app)
        stolen = self.client.environ_base["HTTP_X_WORKSPACE"]
        with outsider.session_transaction() as session:
            headers = {"X-CSRF-Token": session["csrf"], "X-Workspace": stolen}
        self.assertEqual(outsider.post("/api/workspaces/close", headers=headers).status_code, 410)
        for suffix in ("", "/preview", "/jobs/" + "a" * 32, "/jobs/" + "a" * 32 + "/download/excel?workspace=" + stolen):
            self.assertEqual(outsider.get(base + suffix, headers={"X-Workspace": stolen}).status_code, 410)
        self.assertEqual(self.client.get(base).status_code, 200)

    def test_close_cancels_worker_but_keeps_files_and_slot_until_finished(self):
        base, meta = self.upload()
        folder = self.app.extensions["storage"].folder("baloot", meta["run"])
        with self.blocked_report() as (started, release, threads):
            self.start(base)
            self.assertTrue(started.wait(3))
            self.assertEqual(self.close().status_code, 200)
            self.assertTrue(self.app.extensions["jobs"].cancel.is_set())
            self.assertTrue(folder.exists())
            other = workspace_client(self.app)
            self.assertEqual(other.get("/api/jobs/active").get_json(), {"busy": True})
            release.set()
            threads[0].join(timeout=3)
            self.app.extensions["workspaces"].sweep()
            self.assertFalse(folder.exists())
            self.assertEqual(other.get("/api/jobs/active").get_json(), {"busy": False})

    def test_busy_report_never_discloses_another_page_reference(self):
        base, _ = self.upload()
        with self.blocked_report() as (started, release, threads):
            self.start(base)
            self.assertTrue(started.wait(3))
            other_base, _ = self.upload()
            response = self.client.post(other_base + "/reports", json=self.options, headers=self.headers)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(set(response.get_json()), {"error", "busy"})
            self.assertEqual(self.client.get("/api/jobs/active").get_json(), {"busy": True})

    def test_stop_preserves_work_until_page_closes(self):
        base, meta = self.upload()
        with self.blocked_report() as (started, release, threads):
            job = self.start(base)
            self.assertTrue(started.wait(3))
            stopped = self.client.post(base + "/jobs/" + job["id"] + "/stop", headers=self.headers)
            self.assertEqual(stopped.get_json()["status"], "stopping")
            release.set()
            threads[0].join(timeout=3)
            self.assertEqual(self.client.get(base + "/jobs/" + job["id"]).get_json()["status"], "stopped")
        self.close()
        self.assertFalse(self.app.extensions["storage"].folder("baloot", meta["run"]).exists())

    def test_expiry_removes_abandoned_files_but_heartbeat_keeps_them(self):
        import time
        base, meta = self.upload()
        manager = self.app.extensions["workspaces"]
        token = self.client.environ_base["HTTP_X_WORKSPACE"]
        manager.pages[token]["touched"] = time.monotonic() - manager.idle_seconds - 1
        self.client.post("/api/workspaces/ping", headers=self.headers)
        manager.sweep()
        self.assertEqual(self.client.get(base).status_code, 200)
        manager.pages[token]["touched"] = time.monotonic() - manager.idle_seconds - 1
        manager.sweep()
        self.assertFalse(self.app.extensions["storage"].folder("baloot", meta["run"]).exists())
        self.assertEqual(self.client.get(base).status_code, 410)

    def test_cleanup_waits_for_inflight_request_and_rejects_late_report_start(self):
        from werkzeug.exceptions import Gone
        _, meta = self.upload()
        manager = self.app.extensions["workspaces"]
        token = self.client.environ_base["HTTP_X_WORKSPACE"]
        with self.client.session_transaction() as session:
            owner = session["csrf"]
        manager.acquire(token, owner)
        self.close()
        folder = self.app.extensions["storage"].folder("baloot", meta["run"])
        self.assertTrue(folder.exists())
        with self.assertRaises(Gone):
            manager.start_report(token, "baloot", meta["run"], self.config, self.options)
        manager.release(token)
        self.assertFalse(folder.exists())

    def test_restart_removes_temporary_data_and_keeps_legacy_records(self):
        _, meta = self.upload()
        temporary = self.app.extensions["storage"].folder("baloot", meta["run"])
        legacy = self.app.extensions["storage"].folder("baloot", "f" * 32)
        atomic_json(legacy / "upload.json", {"legacy": True})
        create_app({"TESTING": True, "STORAGE_ROOT": self.root / "web"})
        self.assertFalse(temporary.exists())
        self.assertTrue(legacy.exists())
