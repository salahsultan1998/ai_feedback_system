"""Platform access control without contacting the shared account service."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

from app import create_app
from web.auth import LOGIN_LIMIT, SESSION_SECONDS, supabase_sign_in


EMAIL = "member@example.test"
ENV = {"FEEDBACK_AUTH_ENABLED": "true", "SUPABASE_URL": "https://auth.example.test",
       "SUPABASE_PUBLISHABLE_KEY": "publishable-test-key", "FEEDBACK_ALLOWED_EMAILS": EMAIL}


class SupabaseAuthTests(TestCase):
    def test_password_exchange_only_returns_confirmed_identity(self):
        upstream = MagicMock()
        response = upstream.open.return_value.__enter__.return_value
        payload = {"access_token": "upstream-only", "refresh_token": "upstream-only", "user": {
            "id": "account-id", "email": EMAIL.upper(), "email_confirmed_at": "2026-11-17T00:00:00Z"}}
        response.read.return_value = json.dumps(payload).encode()
        with patch("web.auth.build_opener", return_value=upstream):
            result = supabase_sign_in(ENV["SUPABASE_URL"], ENV["SUPABASE_PUBLISHABLE_KEY"], EMAIL, "sample-password")
            self.assertEqual(result, ("account-id", EMAIL))
            request = upstream.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://auth.example.test/auth/v1/token?grant_type=password")
            self.assertEqual(request.get_header("Apikey"), ENV["SUPABASE_PUBLISHABLE_KEY"])
            self.assertEqual(json.loads(request.data), {"email": EMAIL, "password": "sample-password"})
            self.assertEqual(request.get_method(), "POST")
            payload["user"]["email_confirmed_at"] = None
            response.read.return_value = json.dumps(payload).encode()
            self.assertIsNone(supabase_sign_in(ENV["SUPABASE_URL"], ENV["SUPABASE_PUBLISHABLE_KEY"], EMAIL, "sample-password"))


class AuthTests(TestCase):
    def setUp(self):
        storage = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch("app.load_dotenv"))
        self.enterContext(patch.dict(os.environ, ENV, clear=True))
        self.client = create_app({"TESTING": True, "STORAGE_ROOT": storage}).test_client()
        self.sign_in = self.enterContext(patch("web.auth.supabase_sign_in", return_value=("user-test", EMAIL)))

    def csrf(self):
        with self.client.session_transaction() as state:
            return state["csrf"]

    def login(self, email=EMAIL):
        self.client.get("/login")
        return self.client.post("/login", data={"email": email, "password": "password-test", "csrf": self.csrf()})

    def test_login_is_required_for_pages_apis_and_downloads(self):
        self.assertEqual(self.client.get("/").location, "/login")
        self.assertEqual(self.client.get("/games/okey").location, "/login")
        for route in ("/api/games/okey/prompts", "/api/games/okey/uploads/r/jobs/j/download/markdown"):
            response = self.client.get(route)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.get_json()["login_url"], "/login")
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        with self.client.get("/static/css/style.css") as response:
            self.assertEqual(response.status_code, 200)
        self.sign_in.assert_not_called()

    def test_login_checks_csrf_allowlist_and_verified_identity(self):
        self.assertEqual(self.client.post("/login", data={"email": EMAIL, "password": "password-test"}).status_code, 403)
        self.assertEqual(self.login("other@example.test").status_code, 401)
        self.sign_in.assert_not_called()
        self.sign_in.return_value = ("other-user", "other@example.test")
        self.assertEqual(self.login().status_code, 401)
        self.sign_in.return_value = ("user-test", EMAIL)
        old_csrf = self.csrf()
        self.assertEqual(self.login(EMAIL.upper()).status_code, 303)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertNotEqual(self.csrf(), old_csrf)
        self.sign_in.assert_called_with(ENV["SUPABASE_URL"], ENV["SUPABASE_PUBLISHABLE_KEY"], EMAIL, "password-test")

    def test_logout_revokes_the_server_session_even_if_cookie_is_replayed(self):
        self.assertEqual(self.login().status_code, 303)
        self.client.get("/")
        self.assertEqual(self.client.post("/logout").status_code, 403)
        cookie = self.client.get_cookie("feedback_session")
        response = self.client.post("/logout", data={"csrf": self.csrf()})
        self.assertEqual(response.location, "/login")
        self.client.set_cookie("feedback_session", cookie.value)
        self.assertEqual(self.client.get("/").location, "/login")

    def test_sessions_expire_and_uncertain_authentication_fails_closed(self):
        self.sign_in.side_effect = OSError("mocked upstream connection failure")
        self.assertEqual(self.login().status_code, 503)
        self.assertEqual(self.client.get("/").location, "/login")
        self.sign_in.side_effect = None
        with patch("web.auth.time.time", return_value=1_800_000_000):
            self.assertEqual(self.login().status_code, 303)
        with patch("web.auth.time.time", return_value=1_800_000_000 + SESSION_SECONDS):
            self.assertEqual(self.client.get("/").location, "/login")

    def test_repeated_invalid_logins_are_throttled_before_supabase(self):
        self.sign_in.return_value = None
        for _ in range(LOGIN_LIMIT):
            self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.login().status_code, 429)
        self.assertEqual(self.sign_in.call_count, LOGIN_LIMIT)
