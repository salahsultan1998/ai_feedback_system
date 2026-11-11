from io import BytesIO
import os
import time
from unittest.mock import patch

from app import create_app
from tests.helpers import BaseTest, fake_request, workspace_client


class WebTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.app = create_app({"TESTING":True,"STORAGE_ROOT":self.root/"web"})
        self.client = workspace_client(self.app)
        self.client.get("/")
        with self.client.session_transaction() as session:
            self.headers = {"X-CSRF-Token":session["csrf"]}

    def upload(self):
        response = self.client.post("/api/games/baloot/uploads", data={"file":(BytesIO(self.raw.to_csv(index=False).encode()),"sample.csv")}, headers=self.headers)
        self.assertEqual(response.status_code,201,response.get_json())
        return "/api/games/baloot/uploads/"+response.get_json()["run"]

    def test_pages_preview_and_csrf(self):
        for path in ("/","/games/baloot","/games/okey","/static/css/style.css","/static/js/game.js"):
            response = self.client.get(path)
            self.assertEqual(response.status_code,200)
            response.close()
        self.assertEqual(self.client.post("/api/games/baloot/uploads").status_code,403)
        base = self.upload()
        preview = self.client.get(base+"/preview").get_json()
        self.assertEqual(preview["rows"][0][0],"000")
        self.assertEqual(self.client.get("/static/../config/settings.json").status_code,404)

    def test_prompt_overrides_are_protected_persistent_and_used_by_new_reports(self):
        url = "/api/games/baloot/prompts"
        other_url = "/api/games/xinjiang/prompts"
        with patch.dict(os.environ, {"FEEDBACK_PROMPT_PASSWORD_BALOOT": "test-baloot",
                                     "FEEDBACK_PROMPT_PASSWORD_XINJIANG": "test-xinjiang",
                                     "OPENROUTER_API_KEY": "test-only"}):
            default = self.client.get(url).get_json()["prompts"]["classification"]
            update = {"name": "classification", "revision": default["revision"],
                      "text": default["text"] + "\n配置保存验证", "password": "test-baloot"}
            self.assertEqual(self.client.post(url, json=update).status_code, 403)
            other = self.client.get(other_url).get_json()["prompts"]["classification"]
            self.assertEqual(self.client.post(other_url, json={**update, "revision": other["revision"]},
                                              headers=self.headers).status_code, 403)
            self.assertEqual(self.client.post(url, json={**update, "text": "缺少分类占位符"},
                                              headers=self.headers).status_code, 400)
            saved = self.client.post(url, json=update, headers=self.headers)
            self.assertEqual(saved.status_code, 200, saved.get_json())
            version = saved.get_json()
            self.assertEqual(version["previous"], default["text"])
            self.assertEqual(self.client.post(url, json=update, headers=self.headers).status_code, 409)
            base = self.upload()
            with patch.object(self.app.extensions["jobs"], "start", return_value={"id": "test-job"}) as start:
                result = self.client.post(base + "/reports", headers=self.headers,
                                          json={"start": "2026-10-19", "end": "2026-10-22"})
            self.assertEqual(result.status_code, 202, result.get_json())
            self.assertIn("配置保存验证", start.call_args.args[2]["classification_prompt"])

            restarted = create_app({"TESTING": True, "STORAGE_ROOT": self.root / "web"})
            client = workspace_client(restarted)
            self.assertEqual(client.get(url).get_json()["prompts"]["classification"], version)
            self.assertEqual(client.get(other_url).get_json()["prompts"]["classification"], other)
            with client.session_transaction() as session:
                headers = {"X-CSRF-Token": session["csrf"]}
            restored = client.post(url, json={**update, "text": version["previous"],
                                              "revision": version["revision"]}, headers=headers)
            self.assertEqual(restored.status_code, 200, restored.get_json())
            self.assertEqual(restored.get_json()["text"], default["text"])

    def test_api_generates_downloadable_report(self):
        base = self.upload()
        with patch.dict(os.environ, {"OPENROUTER_API_KEY":"test-only"}), patch("feedback.ai_client.request_json",side_effect=fake_request):
            response = self.client.post(base+"/reports",json={"start":"2026-10-19","end":"2026-10-22"},headers=self.headers)
            self.assertEqual(response.status_code,202,response.get_json())
            job = base+"/jobs/"+response.get_json()["id"]
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                status = self.client.get(job).get_json()
                if status["status"] not in {"running","stopping"}:
                    break
                time.sleep(.02)
            self.assertEqual(status["status"],"complete",status)
        for kind in ("markdown","excel"):
            result = self.client.get(job+"/download/"+kind)
            self.assertEqual(result.status_code,200)
            result.close()
        html=self.client.get(job+"/report").get_json()["html"]
        self.assertIn("<table>",html)
        self.assertNotIn('href="feedback_details.xlsx"',html)
        self.assertEqual(self.client.post(job+"/push/feishu").status_code, 403)
        with patch("web.api.feishu.push", return_value={"available": True, "status": "sent", "sent_at": "2026-10-26T00:00:00+00:00"}) as sender:
            pushed = self.client.post(job+"/push/feishu", json={}, headers=self.headers)
            self.assertEqual(pushed.status_code, 200)
            self.assertEqual(pushed.get_json()["status"], "sent")
            sender.assert_called_once()
            with self.app.extensions["feishu_lock"]:
                self.assertEqual(self.client.post(job+"/push/feishu", json={}, headers=self.headers).status_code, 409)

    def test_missing_api_key_is_reported_before_job_starts(self):
        base = self.upload()
        with patch.dict(os.environ, {"OPENROUTER_API_KEY":""}):
            response = self.client.post(base+"/reports",json={"start":"2026-10-19","end":"2026-10-22"},headers=self.headers)
        self.assertEqual(response.status_code,400)
        self.assertIn("OPENROUTER_API_KEY",response.get_json()["error"])
        self.assertIsNone(self.app.extensions["jobs"].active)

    def test_model_rejection_reaches_progress_message_and_server_log(self):
        base = self.upload()
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-only"}), patch(
                "feedback.ai_client.request_json", side_effect=ValueError("模型请求失败（HTTP 403）：Access denied")), patch(
                "feedback.processing.logger.warning"), self.assertLogs(self.app.logger.name, level="WARNING") as logs:
            response = self.client.post(base + "/reports", json={"start": "2026-10-19", "end": "2026-10-22"}, headers=self.headers)
            job = base + "/jobs/" + response.get_json()["id"]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = self.client.get(job).get_json()
                if status["status"] == "failed":
                    break
                time.sleep(.02)
        self.assertEqual(status["status"], "failed", status)
        self.assertEqual((status["done"], status["total"]), (0, 4))
        self.assertIn("HTTP 403", status["message"])
        self.assertIn("Access denied", status["message"])
        self.assertTrue(any("HTTP 403" in line for line in logs.output))

    def test_restart_discards_temporary_uploads(self):
        base=self.upload()
        run=base.split("/")[-1]
        from feedback.storage import atomic_json
        job="a"*32
        folder=self.root/"web/baloot"/run
        atomic_json(folder/"jobs"/(job+".json"),{"status":"running","message":"busy"})
        with patch("web.api.feishu.push") as sender:
            self.assertEqual(self.client.post(base+"/jobs/"+job+"/push/feishu", json={}, headers=self.headers).status_code, 400)
            sender.assert_not_called()
        create_app({"TESTING":True,"STORAGE_ROOT":self.root/"web"})
        self.assertFalse(folder.exists())
