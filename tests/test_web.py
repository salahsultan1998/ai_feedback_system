from io import BytesIO
import json
import os
import time
from unittest.mock import patch

from app import create_app
from tests.helpers import BaseTest, fake_request


class WebTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.app = create_app({"TESTING":True,"STORAGE_ROOT":self.root/"web"})
        self.client = self.app.test_client()
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

    def test_restart_marks_unfinished_job_interrupted(self):
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
        self.assertEqual(json.loads((folder/"jobs"/(job+".json")).read_text())["status"],"interrupted")
