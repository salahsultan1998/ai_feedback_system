import json
import os
import time
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import pandas as pd
from openpyxl import Workbook

from app import create_app
from feedback import report_review
from feedback.review_state import mark_push_started
from feedback.exports.markdown import render_markdown
from tests.helpers import BaseTest, fake_request


class ReportReviewTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        current = pd.DataFrame([
            {"反馈ID": "00012", "用户ID": "009", "用户自己填写的内容": "أريد تحسين الاتصال", "中文翻译": "希望改善连接。"},
            {"反馈ID": "00013", "用户ID": "010", "用户自己填写的内容": "Lütfen bağlantıyı iyileştirin.", "中文翻译": "请改善连接。"}])
        snapshot = {"current": current, "selection": {"periods": {
            "current": {"start": "2026-10-19", "end": "2026-10-22"},
            "previous": {"start": "2026-10-12", "end": "2026-10-15"}}},
            "total": 2, "previous_total": 1,
            "modules": [{"label": "游戏卡顿", "count": 2, "share": 1.0, "previous": 1, "flagged": False}]}
        analysis = {"suggestions": [{"text": "改善连接", "feedback_ids": ["00012", "00013"]}], "issues": {}}
        self.original = render_markdown(snapshot, analysis, {"name": "测试游戏"})
        (self.folder / "report.md").write_text(self.original, encoding="utf-8")
        report_review.initialize(self.folder, snapshot, analysis, self.original)

    def save(self, text, version=None):
        before = report_review.details(self.folder)
        return report_review.save(self.folder, before["version"] if version is None else version,
                                  [{"id": "s0", "text": text}])

    def test_edit_changes_only_suggestion_and_preserves_all_source_snapshots(self):
        before = report_review.details(self.folder)
        self.assertTrue(before["editable"])
        self.assertFalse(before["modified"])
        item = before["suggestions"][0]
        self.assertEqual(item["feedback_ids"], ["00012", "00013"])
        self.assertEqual([source["feedback_id"] for source in item["sources"]], ["00012", "00013"])
        self.assertEqual(item["sources"][0]["original_text"], "أريد تحسين الاتصال")
        self.assertEqual(item["sources"][1]["translation_zh"], "请改善连接。")
        immutable = (self.folder / "review.json").read_bytes()
        (self.folder.parent / "processed.csv").write_text("a different report's processing results", encoding="utf-8")
        saved = self.save("优化游戏连接稳定性")
        self.assertTrue(saved["modified"])
        self.assertNotEqual(saved["version"], before["version"])
        self.assertEqual(saved["suggestions"][0]["original_text"], "改善连接")
        self.assertEqual(saved["suggestions"][0]["sources"], item["sources"])
        after = (self.folder / "report.md").read_text(encoding="utf-8")
        self.assertEqual(after, self.original.replace("1. 改善连接（反馈 ID：", "1. 优化游戏连接稳定性（反馈 ID："))
        self.assertEqual((self.folder / "report.original.md").read_text(encoding="utf-8"), self.original)
        self.assertEqual((self.folder / "review.json").read_bytes(), immutable)

    def test_restore_is_explicit_and_original_backup_survives_multiple_saves(self):
        self.save("改善网络连接")
        current = self.save("提高连接稳定性")
        restored = report_review.save(self.folder, current["version"],
            [{"id": item["id"], "text": item["original_text"]} for item in current["suggestions"]])
        self.assertFalse(restored["modified"])
        self.assertEqual((self.folder / "report.md").read_text(encoding="utf-8"), self.original)
        self.assertEqual((self.folder / "report.original.md").read_text(encoding="utf-8"), self.original)

    def test_optimistic_conflict_does_not_overwrite_newer_work(self):
        version = report_review.details(self.folder)["version"]
        self.save("先保存的修订")
        latest = (self.folder / "report.md").read_bytes()
        with self.assertRaises(report_review.ReviewConflict):
            self.save("过期页面的修订", version)
        self.assertEqual((self.folder / "report.md").read_bytes(), latest)

    def test_invalid_edits_cannot_change_ids_or_replace_statistics(self):
        before = report_review.details(self.folder)
        for suggestions in ([], [{"id": "invented", "text": "新的文案"}],
                [{"id": "s0", "text": ""}], [{"id": "s0", "text": "   "}],
                [{"id": "s0", "text": 12}], [{"id": "s0", "text": "文" * 2001}],
                [{"id": "s0", "text": "原文 العربية"}],
                [{"id": "s0", "text": "新的文案"}, {"id": "s0", "text": "重复"}]):
            with self.subTest(suggestions=suggestions), self.assertRaises(ValueError):
                report_review.save(self.folder, before["version"], suggestions)
            self.assertEqual((self.folder / "report.md").read_text(encoding="utf-8"), self.original)

    def test_html_markdown_and_newlines_cannot_inject_report_structure(self):
        self.save('<img src=x onerror=alert(1)> **改进** | 链接\n## 反馈统计')
        text = (self.folder / "report.md").read_text(encoding="utf-8")
        self.assertNotIn("<img", text)
        self.assertEqual(text.count("\n## 反馈统计\n"), 1)
        self.assertEqual(text.count("| 反馈模块 |"), 1)
        self.assertIn("（反馈 ID：00012、00013）", text)
        self.assertTrue(report_review.details(self.folder)["modified"])

    def test_every_push_checkpoint_locks_editing_including_transformed_legacy_states(self):
        for state in ("sending", "sent", "partial", "failed", "unknown"):
            with self.subTest(state=state):
                (self.folder / "feishu_push.json").write_text(json.dumps({"status": state, "format": "old"}))
                details = report_review.details(self.folder)
                self.assertTrue(details["available"])
                self.assertFalse(details["editable"])
                with self.assertRaises(ValueError):
                    self.save("不应保存")
        (self.folder / "feishu_push.json").unlink()
        mark_push_started(self.folder)
        self.assertFalse(report_review.details(self.folder)["editable"])
        with self.assertRaises(ValueError):
            self.save("仍然不能保存")

    def test_old_reports_fail_closed_without_using_mutable_processing_cache(self):
        (self.folder / "review.json").unlink()
        details = report_review.details(self.folder)
        self.assertFalse(details["available"])
        self.assertFalse(details["editable"])
        with self.assertRaises(ValueError):
            self.save("不能推断原文")

    def test_old_report_recovers_only_from_its_completed_excel(self):
        (self.folder / "review.json").unlink()
        (self.folder / "report.original.md").unlink()
        book = Workbook()
        sheet = book.active
        sheet.title = "本期反馈明细"
        sheet.append(["报告标题"])
        sheet.append(["报告期间"])
        sheet.append([])
        sheet.append(["反馈ID", "原文", "中文翻译"])
        sheet.append(["00012", "原始保存的玩家文本", "当时生成的中文翻译"])
        sheet.append(["00013", "Другой отзыв", "另一条中文翻译"])
        book.save(self.folder / "feedback_details.xlsx")
        book.close()
        self.assertTrue(report_review.prepare_existing(self.folder))
        details = report_review.details(self.folder)
        self.assertTrue(details["editable"])
        self.assertEqual(details["suggestions"][0]["sources"][0]["feedback_id"], "00012")
        self.assertEqual(details["suggestions"][0]["sources"][0]["translation_zh"], "当时生成的中文翻译")
        self.assertFalse(report_review.prepare_existing(self.folder), "Do not replace an existing snapshot")

    def test_old_user_id_reports_or_missing_evidence_do_not_get_inferred_snapshots(self):
        (self.folder / "review.json").unlink()
        (self.folder / "report.original.md").unlink()
        (self.folder / "report.md").write_text(self.original.replace("反馈 ID：00012、00013", "用户 ID：009、010"), encoding="utf-8")
        self.assertFalse(report_review.prepare_existing(self.folder))
        (self.folder / "report.md").write_text(self.original, encoding="utf-8")
        self.assertFalse(report_review.prepare_existing(self.folder))
        self.assertFalse((self.folder / "review.json").exists())

    def test_large_merged_suggestion_keeps_every_source(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            original = self.original.replace("00012、00013", "、".join(f"{i:05d}" for i in range(250)))
            (folder / "report.md").write_text(original, encoding="utf-8")
            rows = pd.DataFrame([{"反馈ID": f"{i:05d}", "用户自己填写的内容": f"source {i}", "中文翻译": f"原文翻译 {i}"} for i in range(250)])
            analysis = {"suggestions": [{"text": "改善连接", "feedback_ids": list(rows["反馈ID"])}]}
            report_review.initialize(folder, {"current": rows}, analysis, original)
            detail = report_review.details(folder)
            self.assertEqual(len(detail["suggestions"][0]["sources"]), 250)
            self.assertEqual(detail["suggestions"][0]["sources"][-1]["original_text"], "source 249")


class ReportReviewWebTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.raw.loc[self.raw["反馈ID"].isin(["004", "005"]), "反馈标签"] = "建议"
        self.app = create_app({"TESTING": True, "STORAGE_ROOT": self.root / "web"})
        self.client = self.app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as session:
            self.headers = {"X-CSRF-Token": session["csrf"]}
        upload = self.client.post("/api/games/baloot/uploads", headers=self.headers,
            data={"file": (BytesIO(self.raw.to_csv(index=False).encode()), "review.csv")})
        self.base = "/api/games/baloot/uploads/" + upload.get_json()["run"]
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-only"}), patch("feedback.ai_client.request_json", side_effect=fake_request):
            response = self.client.post(self.base + "/reports", headers=self.headers,
                json={"start": "2026-10-19", "end": "2026-10-22"})
            self.job = self.base + "/jobs/" + response.get_json()["id"]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = self.client.get(self.job).get_json()
                if status["status"] not in {"running", "stopping"}:
                    break
                time.sleep(.01)
            self.assertEqual(status["status"], "complete", status)

    def edit_payload(self, text="改善连接稳定性"):
        review = self.client.get(self.job + "/review").get_json()
        return {"version": review["version"],
                "suggestions": [{"id": item["id"], "text": text} for item in review["suggestions"]]}

    def test_save_updates_preview_download_and_real_feishu_payload_without_ai(self):
        payload = self.edit_payload()
        self.assertTrue(payload["suggestions"])
        excel = self.client.get(self.job + "/download/excel")
        original_excel = excel.data
        excel.close()
        with patch("feedback.ai_client.request_json") as model:
            saved = self.client.post(self.job + "/review", json=payload, headers=self.headers)
            self.assertEqual(saved.status_code, 200, saved.get_json())
            self.assertIn("改善连接稳定性", saved.get_json()["html"])
            self.assertTrue(saved.get_json()["review"]["modified"])
            self.assertNotEqual(saved.get_json()["review"]["version"], payload["version"])
            preview = self.client.get(self.job + "/report").get_json()
            self.assertEqual(preview["review"]["version"], saved.get_json()["review"]["version"])
            md = self.client.get(self.job + "/download/markdown")
            self.assertIn("改善连接稳定性", md.data.decode("utf-8"))
            md.close()
            excel = self.client.get(self.job + "/download/excel")
            self.assertEqual(excel.data, original_excel)
            excel.close()
            with patch("feedback.feishu.credentials", return_value=("https://example", "")), patch(
                    "feedback.feishu_sheets.sync", return_value=None), patch(
                    "feedback.feishu.send_payload", return_value={"code": 0}) as sender:
                response = self.client.post(self.job + "/push/feishu", headers=self.headers,
                    json={"report_version": preview["review"]["version"]})
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertIn("改善连接稳定性", json.dumps(sender.call_args.args[1], ensure_ascii=False))
            model.assert_not_called()
        self.assertFalse(self.client.get(self.job + "/review").get_json()["editable"])

    def test_delete_all_restore_and_push_preserve_sources_statistics_and_excel_without_ai(self):
        original = self.client.get(self.job + "/review").get_json()
        self.assertTrue(original["suggestions"])
        response = self.client.get(self.job + "/download/markdown")
        original_markdown = response.data.decode("utf-8")
        response.close()
        response = self.client.get(self.job + "/download/excel")
        original_excel = response.data
        response.close()

        def save(included):
            review = self.client.get(self.job + "/review").get_json()
            payload = {"version": review["version"], "suggestions": [
                {"id": item["id"], "text": item["original_text"], "included": included}
                for item in review["suggestions"]]}
            saved = self.client.post(self.job + "/review", json=payload, headers=self.headers)
            self.assertEqual(saved.status_code, 200, saved.get_json())
            return saved.get_json()

        with patch("feedback.ai_client.request_json") as model:
            saved = save(False)
            self.assertTrue(saved["review"]["editable"])
            self.assertTrue(saved["review"]["modified"])
            self.assertIn("本期建议已全部移除。", saved["html"])
            reopened = self.client.get(self.job + "/review").get_json()
            self.assertTrue(all(not item["included"] for item in reopened["suggestions"]))
            self.assertEqual([item["sources"] for item in reopened["suggestions"]],
                             [item["sources"] for item in original["suggestions"]])
            response = self.client.get(self.job + "/download/markdown")
            removed_markdown = response.data.decode("utf-8")
            response.close()
            self.assertIn("<!-- suggestion-ids:  -->", removed_markdown)
            self.assertIn("本期建议已全部移除。", removed_markdown)
            self.assertEqual(report_review._parts(removed_markdown)[::2], report_review._parts(original_markdown)[::2])
            response = self.client.get(self.job + "/download/excel")
            self.assertEqual(response.data, original_excel)
            response.close()
            restored = save(True)
            self.assertFalse(restored["review"]["modified"])
            response = self.client.get(self.job + "/download/markdown")
            self.assertEqual(response.data.decode("utf-8"), original_markdown)
            response.close()
            removed = save(False)
            with patch("feedback.feishu.credentials", return_value=("https://example", "")), patch(
                    "feedback.feishu_sheets.sync", return_value=None), patch(
                    "feedback.feishu.send_payload", return_value={"code": 0}) as sender:
                pushed = self.client.post(self.job + "/push/feishu", headers=self.headers,
                    json={"report_version": removed["review"]["version"]})
                self.assertEqual(pushed.status_code, 200, pushed.get_json())
                sent = json.dumps([call.args[1] for call in sender.call_args_list], ensure_ascii=False)
                self.assertIn("本期建议已全部移除。", sent)
                self.assertNotIn("suggestion-ids", sent)
                for item in original["suggestions"]:
                    self.assertNotIn(item["text"], sent)
            self.assertFalse(self.client.get(self.job + "/review").get_json()["editable"])
            model.assert_not_called()

    def test_csrf_optimistic_version_and_push_lock(self):
        payload = self.edit_payload()
        self.assertEqual(self.client.post(self.job + "/review", json=payload).status_code, 403)
        with self.app.extensions["feishu_lock"]:
            self.assertEqual(self.client.post(self.job + "/review", json=payload, headers=self.headers).status_code, 409)
        response = self.client.post(self.job + "/review", json=payload, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.client.post(self.job + "/review", json=payload, headers=self.headers).status_code, 409)
        with patch("web.api.feishu.push") as sender:
            response = self.client.post(self.job + "/push/feishu", json={"report_version": payload["version"]}, headers=self.headers)
            self.assertEqual(response.status_code, 409, response.get_json())
            sender.assert_not_called()
        # Rejecting a stale preview must not freeze the otherwise editable report.
        self.assertTrue(self.client.get(self.job + "/review").get_json()["editable"])
        fresh = self.edit_payload()
        with patch("feedback.feishu.credentials", return_value=("https://example", "")), patch(
                "feedback.feishu_sheets.sync", side_effect=ValueError("模拟明细推送失败")):
            response = self.client.post(self.job + "/push/feishu", json={"report_version": fresh["version"]}, headers=self.headers)
            self.assertEqual(response.status_code, 400)
        self.assertFalse(self.client.get(self.job + "/review").get_json()["editable"])
        self.assertEqual(self.client.post(self.job + "/review", json=fresh, headers=self.headers).status_code, 400)

    def test_review_rejects_unfinished_and_unknown_jobs(self):
        for method in (self.client.get, self.client.post):
            response = method(self.base + "/jobs/" + "a" * 32 + "/review", headers=self.headers)
            self.assertEqual(response.status_code, 400)
