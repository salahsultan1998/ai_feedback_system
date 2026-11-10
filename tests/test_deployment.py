from http.cookies import SimpleCookie
from io import BytesIO
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from flask import request

from app import create_app
from feedback.config import ROOT
from feedback.storage import atomic_json


class DeploymentTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.storage = Path(temporary.name).resolve() / "private"
        self.environment = {
            "FEEDBACK_ENV": "production",
            "FEEDBACK_STORAGE": str(self.storage),
            "FLASK_SECRET_KEY": "deployment-test-secret-" + "x" * 32,
            "FEEDBACK_ALLOWED_HOSTS": "feedback.example.test",
        }
        self.enterContext(patch("app.load_dotenv"))
        self.enterContext(patch.dict(os.environ, self.environment, clear=True))

    def app(self, **config):
        app = create_app({"TESTING": True, **config})
        if "storage_lock" in app.extensions:
            self.addCleanup(app.extensions["storage_lock"].close)
        return app

    def test_missing_or_unsafe_production_configuration_fails_before_storage_creation(self):
        for key, value in (
            ("FEEDBACK_ENV", "prodution"),
            ("FLASK_SECRET_KEY", ""),
            ("FLASK_SECRET_KEY", "short"),
            ("FEEDBACK_STORAGE", ""),
            ("FEEDBACK_STORAGE", "relative-storage"),
            ("FEEDBACK_ALLOWED_HOSTS", ""),
            ("FEEDBACK_ALLOWED_HOSTS", "*"),
            ("FEEDBACK_ALLOWED_HOSTS", "https://feedback.example.test/path"),
            ("FEEDBACK_SECURE_COOKIES", "false"),
            ("FEEDBACK_BEHIND_PROXY", "invalid"),
        ):
            with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                with self.assertRaises(RuntimeError):
                    self.app()
                self.assertFalse(self.storage.exists())

    def test_storage_cannot_be_project_root_or_public_static_directory(self):
        for target in (ROOT, ROOT / "static", ROOT / "static" / "private-feedback"):
            with self.subTest(target=target), patch.dict(os.environ, {"FEEDBACK_STORAGE": str(target)}):
                with self.assertRaises(RuntimeError):
                    self.app()

    def test_shared_storage_permissions_are_rejected_without_changing_them(self):
        self.storage.mkdir(mode=0o755)
        self.storage.chmod(0o755)
        with self.assertRaisesRegex(RuntimeError, "0700"):
            self.app()
        self.assertEqual(self.storage.stat().st_mode & 0o777, 0o755)

    def test_health_is_private_data_free_and_does_not_create_a_session(self):
        app = self.app()
        with patch("feedback.ai_client.request_json") as model:
            response = app.test_client().get("/healthz", base_url="https://feedback.example.test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("Set-Cookie", response.headers)
        model.assert_not_called()
        self.assertEqual(self.storage.stat().st_mode & 0o777, 0o700)

    def test_fresh_api_clients_cannot_bypass_csrf_with_placeholder_tokens(self):
        app = self.app()
        source = (ROOT / "examples/feedback_sample.csv").read_bytes()
        for headers in ({}, {"X-CSRF-Token": "missing"}, {"X-CSRF-Token": "arbitrary"}):
            with self.subTest(headers=headers):
                response = app.test_client().post("/api/games/baloot/uploads",
                    base_url="https://feedback.example.test", headers=headers,
                    data={"file": (BytesIO(source), "sample.csv")})
                self.assertEqual(response.status_code, 403)
                self.assertFalse((self.storage / "baloot").exists())

    def test_domain_and_proxy_headers_are_checked_separately(self):
        with patch.dict(os.environ, {"FEEDBACK_BEHIND_PROXY": "true"}):
            app = self.app()

        @app.get("/test-request")
        def inspect_request():
            return {"host": request.host, "secure": request.is_secure, "remote": request.remote_addr}

        client = app.test_client()
        headers = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "192.0.2.10",
                   "X-Forwarded-Host": "attacker.example.test"}
        response = client.get("/test-request", base_url="http://feedback.example.test", headers=headers)
        self.assertEqual(response.get_json(), {
            "host": "feedback.example.test", "secure": True, "remote": "192.0.2.10"})
        self.assertEqual(client.get("/healthz", base_url="https://attacker.example.test").status_code, 400)

    def test_proxy_headers_are_ignored_without_explicit_proxy_configuration(self):
        app = self.app()

        @app.get("/test-request")
        def inspect_request():
            return {"secure": request.is_secure, "remote": request.remote_addr}

        response = app.test_client().get("/test-request", base_url="http://feedback.example.test",
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "192.0.2.10"})
        self.assertEqual(response.get_json(), {"secure": False, "remote": "127.0.0.1"})

    def test_session_survives_restart_but_temporary_upload_is_deleted(self):
        first = self.app()
        client = first.test_client()
        response = client.get("/games/baloot", base_url="https://feedback.example.test")
        token = re.search(r'data-workspace="([^"]+)"', response.get_data(as_text=True)).group(1)
        cookie = SimpleCookie(response.headers["Set-Cookie"])["feedback_session"]
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        session = first.session_interface.get_signing_serializer(first).loads(cookie.value)
        headers = {"X-CSRF-Token": session["csrf"], "X-Workspace": token}
        response = client.post("/api/games/baloot/uploads", base_url="https://feedback.example.test",
            headers=headers, data={"file": (BytesIO((ROOT / "examples/feedback_sample.csv").read_bytes()), "sample.csv")})
        self.assertEqual(response.status_code, 201, response.get_json())
        run = response.get_json()["run"]
        original = self.storage / "baloot" / run / "source.csv"
        self.assertTrue(original.is_file())
        first.extensions["storage_lock"].close()

        second = self.app()
        fresh = second.test_client()
        fresh.set_cookie("feedback_session", cookie.value, domain="feedback.example.test")
        response = fresh.post("/api/games/baloot/uploads", base_url="https://feedback.example.test", headers=headers)
        self.assertEqual(response.status_code, 410)  # CSRF accepted, but the old page has expired.
        response = fresh.get(f"/api/games/baloot/uploads/{run}", base_url="https://feedback.example.test")
        self.assertEqual(response.status_code, 410)
        self.assertFalse(original.exists())

    def test_second_app_cannot_recover_jobs_owned_by_running_app(self):
        app = self.app()
        path = self.storage / "baloot" / ("a" * 32) / "jobs" / (("b" * 32) + ".json")
        atomic_json(path, {"status": "running", "message": "working"})
        with self.assertRaises(RuntimeError):
            self.app()
        self.assertEqual(json.loads(path.read_text())["status"], "running")
        app.extensions["storage_lock"].close()
        self.app()
        self.assertEqual(json.loads(path.read_text())["status"], "interrupted")

    def test_storage_lock_is_released_when_owner_process_exits(self):
        code = "from unittest.mock import patch\nfrom app import create_app\nwith patch('app.load_dotenv'):\n    app = create_app()\n"
        app = self.app()
        blocked = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT,
                                 env=self.environment, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("already in use", blocked.stderr)
        app.extensions["storage_lock"].close()
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT,
                                env=self.environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.app()

    def test_local_development_keeps_test_storage_override_and_local_hosts(self):
        with patch.dict(os.environ, {}, clear=True):
            app = self.app(STORAGE_ROOT=self.storage)
        self.assertEqual(app.extensions["storage"].root, self.storage)
        self.assertFalse(app.config["SESSION_COOKIE_SECURE"])
        self.assertEqual(app.test_client().get("/").status_code, 200)
        self.assertEqual(app.test_client().get("/", base_url="http://outside.example.test").status_code, 400)
