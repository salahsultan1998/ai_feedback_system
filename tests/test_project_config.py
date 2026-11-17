from copy import deepcopy
from io import BytesIO
import json
import os
from unittest.mock import patch

from app import create_app
from tests.helpers import BaseTest, workspace_client


class ProjectConfigTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.options = {"TESTING": True, "STORAGE_ROOT": self.root / "web"}
        self.app = create_app(self.options)
        self.client = workspace_client(self.app)
        with self.client.session_transaction() as state:
            self.headers = {"X-CSRF-Token": state["csrf"]}
        self.base = "/api/games/baloot/config"
        self.password = "test-project-password"
        env = patch.dict(os.environ, {"FEEDBACK_PROMPT_PASSWORD_BALOOT": self.password})
        env.start()
        self.addCleanup(env.stop)

    def post(self, path, body):
        return self.client.post(self.base + path, headers=self.headers, data=json.dumps(body), content_type="application/json")

    def unlock(self):
        result = self.post("/unlock", {"password": self.password})
        self.assertEqual(result.status_code, 200, result.get_json())
        return result.get_json()

    def test_project_configuration_is_gated_and_unlock_expires(self):
        self.assertEqual(self.client.get(self.base).get_json(), {"unlocked": False, "available": True})
        self.assertEqual(self.client.get(self.base + "/versions/0").status_code, 403)
        self.assertEqual(self.post("", {}).status_code, 403)
        self.assertEqual(self.post("/restore", {"revision": "stale", "version": 0}).status_code, 403)
        with patch("feedback.prompt_store.time", return_value=1_000_000):
            self.assertEqual(self.post("/unlock", {"password": "wrong"}).status_code, 403)
            opened = self.unlock()
            self.assertEqual(opened["expires_at"], 1_001_800)
            self.assertNotIn("feishu", opened["value"]["settings"])
            other = workspace_client(self.app)
            self.assertFalse(other.get(self.base).get_json()["unlocked"])
            self.assertEqual(self.post("/lock", {}).status_code, 200)
            self.assertFalse(self.client.get(self.base).get_json()["unlocked"])
            self.unlock()
        with patch("feedback.prompt_store.time", return_value=1_001_800):
            self.assertFalse(self.client.get(self.base).get_json()["unlocked"])
            self.assertEqual(self.client.get("/api/games/baloot/prompts").status_code, 403)

    def test_unlock_password_attempts_are_bounded(self):
        with patch("feedback.prompt_store.time", return_value=1_000_000):
            for _ in range(5):
                self.assertEqual(self.post("/unlock", {"password": "wrong"}).status_code, 403)
            limited = self.post("/unlock", {"password": self.password})
            self.assertEqual(limited.status_code, 429)
            self.assertEqual(limited.headers["Retry-After"], "300")
        with patch("feedback.prompt_store.time", return_value=1_000_300):
            self.assertTrue(self.unlock()["unlocked"])

    def test_validated_versioned_settings_persist_and_reach_new_jobs(self):
        opened = self.unlock()
        value = deepcopy(opened["value"])
        value["settings"]["classification_batch_size"] = 17
        value["report"]["module_growth_thresholds"]["账号问题"] = 0.4
        value["schema"]["terminology"]["队友"] = {"definition": "同一队伍的玩家"}
        from feedback.exports.excel import COLUMNS
        self.assertEqual(list(value["settings"]["detail_columns"]), list(COLUMNS))
        self.assertEqual(list(value["schema"]["labels"]), list(self.config["schema"]["labels"]))

        invalid = deepcopy(value)
        del invalid["report"]["module_growth_thresholds"]["账号问题"]
        result = self.post("", {"revision": opened["revision"], "value": invalid})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.client.get(self.base).get_json()["version"], 0)
        saved = self.post("", {"revision": opened["revision"], "value": value})
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual([row["version"] for row in saved.get_json()["history"]], [1, 0])
        self.assertEqual(self.post("", {"revision": opened["revision"], "value": value}).status_code, 409)

        restarted = create_app(self.options)
        self.client = workspace_client(restarted)
        with self.client.session_transaction() as state:
            self.headers = {"X-CSRF-Token": state["csrf"]}
        self.assertEqual(self.unlock()["value"], value)
        self.assertEqual(self.client.get(self.base + "/versions/0").get_json()["value"], opened["value"])
        uploaded = self.client.post("/api/games/baloot/uploads", headers=self.headers,
            data={"file": (BytesIO(self.raw.to_csv(index=False).encode()), "sample.csv")})
        self.assertEqual(uploaded.status_code, 201, uploaded.get_json())
        url = "/api/games/baloot/uploads/" + uploaded.get_json()["run"] + "/reports"
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-only"}), patch.object(
                restarted.extensions["jobs"], "start", return_value={"id": "test-job"}) as start:
            report = self.client.post(url, headers=self.headers, json={"start": "2026-10-19", "end": "2026-10-22"})
        self.assertEqual(report.status_code, 202, report.get_json())
        captured = start.call_args.args[2]
        self.assertEqual(captured["classification_batch_size"], 17)
        self.assertEqual(captured["report"], value["report"])
        self.assertIn("同一队伍的玩家", captured["classification_prompt"])
        self.assertEqual(list(captured["detail_columns"]), list(COLUMNS))

        restored = self.post("/restore", {"revision": saved.get_json()["revision"], "version": 0})
        self.assertEqual(restored.status_code, 200, restored.get_json())
        self.assertEqual(restored.get_json()["version"], 2)
        self.assertEqual(restored.get_json()["value"], opened["value"])
        self.assertEqual(captured["classification_batch_size"], 17)
