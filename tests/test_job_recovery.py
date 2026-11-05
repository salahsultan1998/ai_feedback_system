from contextlib import contextmanager
from io import BytesIO
import json
import os
import threading
from unittest.mock import patch

from app import create_app
from feedback.storage import atomic_json
from tests.helpers import BaseTest


class JobRecoveryTests(BaseTest):
    options = {"start": "2026-10-19", "end": "2026-10-22", "confirmed": False}

    def setUp(self):
        super().setUp()
        self.app = create_app({"TESTING": True, "STORAGE_ROOT": self.root / "web"})
        self.client = self.app.test_client()
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

    def test_navigation_and_reupload_conflict_identify_the_same_active_worker(self):
        base, metadata = self.upload()
        with self.blocked_report() as (started, release, threads):
            job = self.start(base)
            self.assertTrue(started.wait(3))
            self.client.get("/")
            self.client.get("/games/baloot")
            other_base, _ = self.upload()
            conflict = self.client.post(other_base + "/reports", json=self.options, headers=self.headers)
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.get_json()["active_job"]["id"], job["id"])
            active = self.client.get("/api/jobs/active")
            self.assertEqual(active.status_code, 200)
            current = active.get_json()["job"]
            self.assertEqual(current, conflict.get_json()["active_job"])
            self.assertEqual((current["game"], current["run"]), ("baloot", metadata["run"]))
            self.assertEqual(current["options"], self.options)
            self.assertEqual((current["done"], current["total"], current["status"]), (1, 4, "running"))
            self.assertEqual(len(threads), 1)
            self.assertNotIn("test-only-recovery-key", json.dumps(current))
            self.assertNotIn("classification_prompt", current)
            snapshot = self.app.extensions["jobs"].current()
            snapshot["message"] = "client-local change"
            self.assertEqual(self.app.extensions["jobs"].current()["message"], "测试处理进度")

    def test_upload_metadata_restores_original_upload_without_a_latest_job_pointer(self):
        base, metadata = self.upload()
        response = self.client.get(base)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), metadata)
        self.assertNotIn("latest_job", response.get_json())
        self.assertNotIn("OPENROUTER_API_KEY", response.get_json())
        self.assertEqual(self.client.get("/api/games/baloot/uploads/not-an-id").status_code, 400)
        self.assertEqual(self.client.get("/api/games/baloot/uploads/" + "f" * 32).status_code, 400)

    def test_completed_away_job_remains_readable_after_the_worker_releases(self):
        self.assertEqual(self.client.get("/api/jobs/active").get_json(), {"job": None})
        base, _ = self.upload()
        with self.blocked_report() as (started, release, threads):
            job = self.start(base)
            self.assertTrue(started.wait(3))
            release.set()
            threads[0].join(timeout=3)
            self.assertFalse(threads[0].is_alive())
            self.assertIsNone(self.app.extensions["jobs"].active)
            self.assertEqual(self.client.get("/api/jobs/active").get_json(), {"job": None})
            saved = self.client.get(base + "/jobs/" + job["id"]).get_json()
            self.assertEqual(saved["status"], "complete")
            self.assertEqual(saved["result"], {"total": 2, "previous_total": 2})
            self.assertEqual(saved["run"], job["run"])

    def test_stop_keeps_the_worker_reserved_until_inflight_work_finishes(self):
        base, _ = self.upload()
        with self.blocked_report() as (started, release, threads):
            job = self.start(base)
            self.assertTrue(started.wait(3))
            stopped = self.client.post(base + "/jobs/" + job["id"] + "/stop", headers=self.headers)
            self.assertEqual(stopped.get_json()["status"], "stopping")
            active = self.client.get("/api/jobs/active").get_json()["job"]
            self.assertEqual((active["id"], active["status"]), (job["id"], "stopping"))
            blocked = self.client.post(base + "/reports", json=self.options, headers=self.headers)
            self.assertEqual(blocked.status_code, 409)
            self.assertEqual(blocked.get_json()["active_job"]["status"], "stopping")
            release.set()
            threads[0].join(timeout=3)
            self.assertFalse(threads[0].is_alive())
            self.assertEqual(self.client.get("/api/jobs/active").get_json(), {"job": None})
            saved = self.client.get(base + "/jobs/" + job["id"]).get_json()
            self.assertEqual(saved["status"], "stopped")
            self.assertEqual(saved["done"], 1)

    def test_failed_worker_releases_its_reservation_and_keeps_progress(self):
        base, _ = self.upload()
        with self.blocked_report(ValueError("synthetic failure")) as (started, release, threads):
            job = self.start(base)
            self.assertTrue(started.wait(3))
            with self.assertLogs(self.app.logger.name, level="WARNING"):
                release.set()
                threads[0].join(timeout=3)
            self.assertFalse(threads[0].is_alive())
            self.assertEqual(self.client.get("/api/jobs/active").get_json(), {"job": None})
            saved = self.client.get(base + "/jobs/" + job["id"]).get_json()
            self.assertEqual((saved["status"], saved["done"]), ("failed", 1))

    def test_restart_interrupts_saved_jobs_without_claiming_an_active_worker(self):
        base, metadata = self.upload()
        folder = self.app.extensions["storage"].folder("baloot", metadata["run"])
        for index, status in enumerate(("running", "stopping")):
            atomic_json(folder / "jobs" / (str(index) * 32 + ".json"), {"status": status, "done": 1})
        restarted = create_app({"TESTING": True, "STORAGE_ROOT": self.root / "web"}).test_client()
        self.assertEqual(restarted.get("/api/jobs/active").get_json(), {"job": None})
        for index in range(2):
            saved = restarted.get(base + "/jobs/" + str(index) * 32).get_json()
            self.assertEqual((saved["status"], saved["done"]), ("interrupted", 1))

    def test_other_busy_locks_do_not_return_an_active_report(self):
        base, metadata = self.upload()
        folder = self.app.extensions["storage"].folder("baloot", metadata["run"])
        job = "a" * 32
        atomic_json(folder / "jobs" / (job + ".json"), {"status": "complete"})
        with self.app.extensions["feishu_lock"]:
            response = self.client.post(base + "/jobs/" + job + "/push/feishu", headers=self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(set(response.get_json()), {"error"})
