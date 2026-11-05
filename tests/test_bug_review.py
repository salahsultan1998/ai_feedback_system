import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import pandas as pd

from feedback import bug_review, report_review
from feedback.exports.markdown import render_markdown


class BugReviewTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.rows = pd.DataFrame([
            {"反馈ID": "0001", "用户自己填写的内容": "الشاشة سوداء", "中文翻译": "屏幕变黑。"},
            {"反馈ID": "0002", "用户自己填写的内容": "Ekran siyah oldu", "中文翻译": "屏幕黑了。"},
            {"反馈ID": "0003", "用户自己填写的内容": "按钮点了没反应", "中文翻译": "按钮点击无响应。"}])
        self.bugs = [
            {"label": "游戏卡顿", "categories": [], "text": "玩家反映对局中出现黑屏。", "feedback_ids": ["0001", "0002"]},
            {"label": "玩法相关", "categories": ["活动（许愿树）"], "text": "玩家反映活动按钮点击后无响应。", "feedback_ids": ["0002", "0003"]}]
        self.suggestions = [{"text": "改善游戏连接", "feedback_ids": ["0001"]}]
        self.original = self.make_report(self.suggestions, self.bugs)
        (self.folder / "report.md").write_text(self.original, encoding="utf-8")
        report_review.initialize(self.folder, {"current": self.rows},
                                 {"suggestions": self.suggestions, "bugs": self.bugs}, self.original)

    def make_report(self, suggestions, bugs):
        snapshot = {"current": self.rows, "selection": {"periods": {
            "current": {"start": "2026-10-19", "end": "2026-10-22"},
            "previous": {"start": "2026-10-12", "end": "2026-10-15"}}},
            "total": 3, "previous_total": 2,
            "modules": [{"label": "游戏卡顿", "count": 3, "share": 1.0, "previous": 2, "flagged": False}]}
        markdown = render_markdown(snapshot, {"suggestions": suggestions, "issues": {}}, {"name": "测试游戏"})
        return markdown.replace("## 反馈统计\n", bug_review.render_section(bugs) + "## 反馈统计\n")

    def payload(self):
        return [{"id": item["id"], "text": item["text"], "included": item["included"],
                 "feedback_ids": item["feedback_ids"]} for item in bug_review.details(self.folder)["bugs"]]

    def save(self, values):
        return bug_review.save(self.folder, bug_review.summary(self.folder)["version"], values)

    def test_snapshot_contains_all_cited_sources_with_original_ids_and_translations(self):
        details = bug_review.details(self.folder)
        self.assertTrue(details["available"])
        self.assertTrue(details["editable"])
        self.assertTrue(details["has_bugs"])
        self.assertFalse(details["modified"])
        self.assertEqual([item["code"] for item in details["bugs"]], ["BUG-001", "BUG-002"])
        self.assertEqual(details["bugs"][0]["sources"][0]["original_text"], "الشاشة سوداء")
        self.assertEqual(details["bugs"][1]["sources"][0]["translation_zh"], "屏幕黑了。")
        self.assertEqual(details["bugs"][0]["feedback_ids"], ["0001", "0002"])
        self.assertIn("2 个疑似问题，涉及 3 条反馈", self.original)

    def test_suggestion_boundary_uses_next_section_instead_of_bug_heading_wording(self):
        for heading in ("## 疑似 BUG 反馈", "## 疑似 BUG 反馈（待核实）", "## 人工核查的异常反馈"):
            with self.subTest(heading=heading):
                text = self.original.replace(bug_review.HEADING + "\n", heading + "\n")
                (self.folder / "report.md").write_text(text, encoding="utf-8")
                detail = report_review.details(self.folder)
                self.assertEqual([item["text"] for item in detail["suggestions"]], ["改善游戏连接"])
                section = text[text.index(heading):]
                report_review.save(self.folder, detail["version"], [{"id": "s0", "text": "提高连接稳定性"}])
                saved = (self.folder / "report.md").read_text(encoding="utf-8")
                self.assertEqual(saved[saved.index(heading):], section)
        (self.folder / "report.md").write_text(self.original, encoding="utf-8")

    def test_manual_text_and_reference_subset_change_only_bug_section_and_counts(self):
        original_snapshot = (self.folder / "review.json").read_bytes()
        values = self.payload()
        values[0].update(text="玩家反映进入对局后出现黑屏，需核实触发条件。", feedback_ids=["0001"])
        result = self.save(values)
        text = (self.folder / "report.md").read_text(encoding="utf-8")
        old_parts, new_parts = bug_review._parts(self.original), bug_review._parts(text)
        self.assertEqual(new_parts[0], old_parts[0])
        self.assertEqual(new_parts[2], old_parts[2])
        self.assertIn("相关反馈：1 条；反馈 ID：0001", text)
        self.assertEqual(result["bugs"][0]["feedback_ids"], ["0001"])
        self.assertEqual(result["bugs"][0]["original_feedback_ids"], ["0001", "0002"])
        self.assertEqual(len(result["bugs"][0]["sources"]), 2, "Unselected evidence stays available for rechecking")
        self.assertTrue(result["modified"])
        self.assertEqual((self.folder / "review.json").read_bytes(), original_snapshot)
        self.assertEqual((self.folder / "report.original.md").read_text(encoding="utf-8"), self.original)

    def test_excluding_bug_recalculates_totals_and_preserves_stable_remaining_code(self):
        values = self.payload()
        values[0]["included"] = False
        result = self.save(values)
        text = (self.folder / "report.md").read_text(encoding="utf-8")
        self.assertIn("1 个疑似问题，涉及 2 条反馈", text)
        self.assertNotIn("BUG-001", text)
        self.assertIn("1. **【BUG-002", text)
        self.assertFalse(result["bugs"][0]["included"])
        self.assertTrue(result["bugs"][1]["included"])
        values = self.payload()
        values[1]["included"] = False
        result = self.save(values)
        self.assertIn(bug_review.EMPTY_TEXT, (self.folder / "report.md").read_text(encoding="utf-8"))
        self.assertTrue(result["editable"])
        self.assertTrue(result["has_bugs"])
        values = self.payload()
        for item in values:
            item["included"] = True
        self.assertFalse(self.save(values)["modified"])
        self.assertEqual((self.folder / "report.md").read_text(encoding="utf-8"), self.original)

    def test_suggestion_edits_preserve_bug_section_and_invalidate_stale_bug_version(self):
        old_version = bug_review.summary(self.folder)["version"]
        old_bug_section = bug_review._parts(self.original)[1]
        report_review.save(self.folder, old_version, [{"id": "s0", "text": "提高网络连接稳定性"}])
        updated = (self.folder / "report.md").read_text(encoding="utf-8")
        self.assertEqual(bug_review._parts(updated)[1], old_bug_section)
        self.assertIn("提高网络连接稳定性", report_review.details(self.folder)["suggestions"][0]["text"])
        with self.assertRaises(report_review.ReviewConflict):
            bug_review.save(self.folder, old_version, self.payload())

    def test_excluded_empty_draft_is_accepted_and_original_sources_remain_recoverable(self):
        values = self.payload()
        values[0].update(included=False, text="", feedback_ids=[])
        result = self.save(values)
        excluded = result["bugs"][0]
        self.assertFalse(excluded["included"])
        self.assertEqual(excluded["text"], self.bugs[0]["text"])
        self.assertEqual(excluded["feedback_ids"], ["0001", "0002"])
        self.assertEqual(len(excluded["sources"]), 2)
        restored = self.payload()
        restored[0]["included"] = True
        self.assertFalse(self.save(restored)["modified"])

    def test_bug_edits_preserve_suggestion_review_and_reject_stale_suggestion_save(self):
        old_version = report_review.summary(self.folder)["version"]
        values = self.payload()
        values[0]["text"] = "玩家反映黑屏，待核查具体触发条件。"
        self.save(values)
        self.assertEqual(report_review.details(self.folder)["suggestions"][0]["text"], "改善游戏连接")
        self.assertFalse(report_review.summary(self.folder)["modified"])
        with self.assertRaises(report_review.ReviewConflict):
            report_review.save(self.folder, old_version, [{"id": "s0", "text": "过期修订"}])

    def test_invalid_save_cannot_add_or_replace_evidence_or_modify_readonly_fields(self):
        cases = [({"feedback_ids": ["9999"]}), ({"feedback_ids": ["0003"]}),
                 ({"feedback_ids": ["0001", "0001"]}), ({"feedback_ids": []}),
                 ({"feedback_ids": [1]}), ({"included": "false"}), ({"text": ""}),
                 ({"text": "文" * 2001}), ({"text": "العربية"}),
                 ({"label": "其他"}), ({"id": "b1"})]
        before = (self.folder / "report.md").read_bytes()
        for changes in cases:
            with self.subTest(changes=changes):
                values = self.payload()
                values[0].update(changes)
                with self.assertRaises(ValueError):
                    self.save(values)
                self.assertEqual((self.folder / "report.md").read_bytes(), before)
        with self.assertRaises(ValueError):
            self.save(self.payload()[:1])

    def test_user_markup_cannot_create_report_sections_or_change_attribution(self):
        values = self.payload()
        values[0]["text"] = '<img src=x onerror=alert(1)> **描述** | 新内容\n## 反馈统计'
        self.save(values)
        text = (self.folder / "report.md").read_text(encoding="utf-8")
        self.assertNotIn("<img", text)
        self.assertEqual(text.count("\n## 反馈统计\n"), 1)
        self.assertEqual(text.count(bug_review.HEADING), 1)
        self.assertEqual(bug_review.details(self.folder)["bugs"][0]["feedback_ids"], ["0001", "0002"])

    def test_push_checkpoint_or_start_lock_freezes_bug_edits_but_sources_remain_readable(self):
        for status in ("sent", "partial", "sending", "unknown", "failed"):
            with self.subTest(status=status):
                (self.folder / "feishu_push.json").write_text(json.dumps({"status": status}))
                details = bug_review.details(self.folder)
                self.assertTrue(details["available"])
                self.assertFalse(details["editable"])
                self.assertEqual(len(details["bugs"][0]["sources"]), 2)
                with self.assertRaises(ValueError):
                    self.save(self.payload())
        (self.folder / "feishu_push.json").unlink()
        report_review.mark_push_started(self.folder)
        self.assertFalse(bug_review.details(self.folder)["editable"])

    def test_old_reports_without_bug_snapshot_remain_unavailable(self):
        data = json.loads((self.folder / "review.json").read_text(encoding="utf-8"))
        data.pop("bugs")
        (self.folder / "review.json").write_text(json.dumps(data), encoding="utf-8")
        details = bug_review.details(self.folder)
        self.assertFalse(details["available"])
        self.assertEqual(details["locked_reason"], bug_review.UNAVAILABLE_REASON)
        self.assertEqual(details["bugs"], [])
        self.assertTrue(report_review.details(self.folder)["available"])

    def test_reports_with_bugs_but_no_suggestions_keep_bug_source_snapshot(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            text = self.make_report([], self.bugs)
            (folder / "report.md").write_text(text, encoding="utf-8")
            report_review.initialize(folder, {"current": self.rows}, {"suggestions": [], "bugs": self.bugs}, text)
            self.assertFalse(report_review.details(folder)["editable"])
            self.assertTrue(bug_review.details(folder)["editable"])
            self.assertEqual(len(bug_review.details(folder)["bugs"][0]["sources"]), 2)

    def test_empty_bug_list_has_explicit_empty_section_without_changing_suggestions(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            text = self.make_report(self.suggestions, [])
            (folder / "report.md").write_text(text, encoding="utf-8")
            report_review.initialize(folder, {"current": self.rows}, {"suggestions": self.suggestions, "bugs": []}, text)
            details = bug_review.details(folder)
            self.assertTrue(details["available"])
            self.assertFalse(details["editable"])
            self.assertFalse(details["has_bugs"])
            self.assertTrue(report_review.details(folder)["editable"])
