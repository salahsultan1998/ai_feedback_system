import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import pandas as pd

from feedback import bug_review, report_review
from feedback.exports.markdown import render_markdown
from feedback.review_state import mark_push_started


class SuggestionDeletionTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        rows = pd.DataFrame([{"反馈ID": f"000{index}", "用户自己填写的内容": f"original {index}",
                              "中文翻译": f"翻译 {index}"} for index in range(1, 4)])
        snapshot = {"current": rows, "selection": {"periods": {
            "current": {"start": "2026-10-19", "end": "2026-10-22"},
            "previous": {"start": "2026-10-12", "end": "2026-10-15"}}},
            "total": 3, "previous_total": 2,
            "modules": [{"label": "游戏卡顿", "count": 3, "share": 1.0, "previous": 2, "flagged": False}]}
        analysis = {"suggestions": [
            {"text": "改善网络连接", "feedback_ids": ["0001", "0002"]},
            {"text": "增加重新连接入口", "feedback_ids": ["0001", "0002"]},
            {"text": "保留对局记录", "feedback_ids": ["0003"]}], "issues": {},
            "bugs": [{"label": "游戏卡顿", "categories": [], "text": "玩家反映对局黑屏。", "feedback_ids": ["0001"]}]}
        self.original = render_markdown(snapshot, analysis, {"name": "测试游戏"})
        (self.folder / "report.md").write_text(self.original, encoding="utf-8")
        report_review.initialize(self.folder, snapshot, analysis, self.original)
        self.snapshot = (self.folder / "review.json").read_bytes()

    def drafts(self):
        return [{"id": item["id"], "text": item["text"], "included": item.get("included", True)}
                for item in report_review.details(self.folder)["suggestions"]]

    def save(self, values, version=None):
        version = report_review.summary(self.folder)["version"] if version is None else version
        return report_review.save(self.folder, version, values)

    def assert_immutable(self):
        self.assertEqual((self.folder / "review.json").read_bytes(), self.snapshot)
        self.assertEqual((self.folder / "report.original.md").read_text(encoding="utf-8"), self.original)

    def test_remove_middle_suggestion_renumbers_kept_items_and_preserves_other_sections(self):
        before = report_review.details(self.folder)
        values = self.drafts()
        values[0]["text"] = "提高网络连接稳定性"
        values[1]["included"] = False
        saved = self.save(values)
        current = (self.folder / "report.md").read_text(encoding="utf-8")
        prefix, body, suffix = report_review._parts(current)
        original_prefix, _, original_suffix = report_review._parts(self.original)
        self.assertEqual((prefix, suffix), (original_prefix, original_suffix))
        self.assertIn("<!-- suggestion-ids: s0,s2 -->\n\n1. 提高网络连接稳定性", body)
        self.assertIn("2. 保留对局记录（反馈 ID：0003）", body)
        self.assertNotIn("增加重新连接入口", body)
        self.assertEqual([item["included"] for item in saved["suggestions"]], [True, False, True])
        self.assertTrue(saved["modified"])
        self.assertEqual([item["sources"] for item in saved["suggestions"]],
                         [item["sources"] for item in before["suggestions"]])
        self.assert_immutable()

    def test_identical_feedback_id_lists_do_not_confuse_identity_after_repeated_edits(self):
        values = self.drafts()
        values[0]["included"] = False
        values[1]["text"] = "在断线提示中增加重新连接入口"
        saved = self.save(values)
        self.assertEqual([item["included"] for item in saved["suggestions"]], [False, True, True])
        self.assertEqual(saved["suggestions"][0]["text"], "改善网络连接")
        self.assertEqual(saved["suggestions"][1]["text"], values[1]["text"])
        values = self.drafts()
        values[1]["text"] = "增加重新连接按钮并显示连接状态"
        saved = self.save(values)
        self.assertEqual(saved["suggestions"][1]["text"], values[1]["text"])
        self.assertFalse(saved["suggestions"][0]["included"])
        self.assert_immutable()

    def test_remove_all_remains_editable_and_restore_recreates_original_markdown(self):
        values = self.drafts()
        for item in values:
            item["included"] = False
        removed = self.save(values)
        body = report_review._parts((self.folder / "report.md").read_text(encoding="utf-8"))[1]
        self.assertEqual(body.strip(), "<!-- suggestion-ids:  -->\n\n本期建议已全部移除。")
        self.assertTrue(removed["editable"])
        self.assertTrue(removed["modified"])
        self.assertTrue(all(not item["included"] for item in removed["suggestions"]))
        restored = self.save([{"id": item["id"], "text": item["original_text"], "included": True}
                              for item in removed["suggestions"]])
        self.assertFalse(restored["modified"])
        self.assertEqual((self.folder / "report.md").read_text(encoding="utf-8"), self.original)
        self.assert_immutable()

    def test_invalid_manifest_or_visible_references_cannot_be_saved(self):
        values = self.drafts()
        values[1]["included"] = False
        self.save(values)
        current = (self.folder / "report.md").read_text(encoding="utf-8")
        invalid = [current.replace("s0,s2 -->", ids + " -->")
                   for ids in ("s0,s0", "s2,s0", "s0,unknown", "s0", " s0,s2", "s0, s2")]
        invalid += [current.replace("<!-- suggestion-ids: s0,s2 -->", "<!-- suggestion-ids: s0,s2 --"),
                    current.replace("2. 保留对局记录", "3. 保留对局记录"),
                    current.replace("2. 保留对局记录（反馈 ID：0003）", "2. 保留对局记录（反馈 ID：0001）")]
        for text in invalid:
            with self.subTest(text=text):
                (self.folder / "report.md").write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    report_review.save(self.folder, "stale-version", values)
                self.assertEqual((self.folder / "report.md").read_text(encoding="utf-8"), text)
        self.assert_immutable()

    def test_invalid_inclusion_fields_or_forged_evidence_are_rejected(self):
        for extra in ({"included": "false"}, {"included": 0}, {"included": 1}, {"included": None},
                      {"feedback_ids": ["0003"]}, {"other": True}, {"id": "s99"}):
            values = self.drafts()
            values[0].update(extra)
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.save(values)
            self.assertEqual((self.folder / "report.md").read_text(encoding="utf-8"), self.original)
        self.assert_immutable()

    def test_stale_version_and_push_lock_prevent_deletion(self):
        version = report_review.summary(self.folder)["version"]
        values = self.drafts()
        values[0]["included"] = False
        self.save(values)
        current = (self.folder / "report.md").read_bytes()
        values[1]["included"] = False
        with self.assertRaises(report_review.ReviewConflict):
            self.save(values, version)
        mark_push_started(self.folder)
        with self.assertRaisesRegex(ValueError, "锁定"):
            self.save(values)
        self.assertEqual((self.folder / "report.md").read_bytes(), current)
        self.assert_immutable()

    def test_missing_excluded_source_is_rejected_before_writing(self):
        values = self.drafts()
        values[2]["included"] = False
        self.save(values)
        version = report_review.summary(self.folder)["version"]
        current = (self.folder / "report.md").read_bytes()
        data = json.loads(self.snapshot)
        del data["sources"]["0003"]
        (self.folder / "review.json").write_text(json.dumps(data), encoding="utf-8")
        values[0]["text"] = "不应保存的文字"
        with self.assertRaises((KeyError, ValueError)):
            report_review.save(self.folder, version, values)
        self.assertEqual((self.folder / "report.md").read_bytes(), current)

    def test_bug_review_preserves_the_saved_suggestion_subset(self):
        bugs_before = bug_review.details(self.folder)["bugs"]
        values = self.drafts()
        values[0]["included"] = False
        self.save(values)
        self.assertEqual(bug_review.details(self.folder)["bugs"], bugs_before)
        before = report_review._parts((self.folder / "report.md").read_text(encoding="utf-8"))[1]
        bugs = [{"id": item["id"], "text": "玩家反映进入对局后出现黑屏。", "included": item["included"],
                 "feedback_ids": item["feedback_ids"]} for item in bugs_before]
        bug_review.save(self.folder, bug_review.summary(self.folder)["version"], bugs)
        after = report_review._parts((self.folder / "report.md").read_text(encoding="utf-8"))[1]
        self.assertEqual(before, after)
        self.assertEqual([item["included"] for item in report_review.details(self.folder)["suggestions"]],
                         [False, True, True])
        self.assert_immutable()
