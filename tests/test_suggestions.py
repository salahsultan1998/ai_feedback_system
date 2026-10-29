import json
from collections import Counter
from unittest.mock import patch

import pandas as pd

from feedback.analysis import analyze_report
from feedback.exports.markdown import render_markdown
from feedback.statistics import build_statistics
from tests.helpers import BaseTest


class SuggestionGroupingTests(BaseTest):
    def snapshot(self, entries):
        rows = pd.DataFrame([{**self.raw.iloc[0].to_dict(), "反馈时间": "2026-10-20",
            "反馈ID": f"feedback-{index:05d}", "用户ID": f"user-{index:05d}", "反馈标签": "建议",
            "最终标签": "其他", "具体主题": "相册功能", "三级主题": "", "中文翻译": "建议增加相册功能。",
            "是否有效": "是", "是否建议": "是", "排除原因": "", **entry}
            for index, entry in enumerate(entries)])
        snapshot = build_statistics(rows, {"periods": {
            "current": {"start": "2026-10-19", "end": "2026-10-25"},
            "previous": {"start": "2026-10-12", "end": "2026-10-18"}}}, self.config)
        # These tests isolate suggestion consolidation from issue analysis.
        for module in snapshot["modules"]:
            module["flagged"] = False
        return snapshot

    def test_equivalent_suggestions_merge_with_deduplicated_user_ids_and_reuse_cache(self):
        snapshot = self.snapshot([{"用户ID": "001234"}, {"用户ID": "5434"}, {"用户ID": "001234"}])
        payloads = []

        def request(prompt, payload, config, validate, should_stop=None):
            payloads.append(payload)
            if payload["stage"] == "提取":
                texts = ["增加相册功能", "添加相册入口", "新增相册"]
                return validate({"suggestions": [{"text": text, "feedback_ids": [item["feedback_id"]]}
                    for text, item in zip(texts, payload["items"])]})
            self.assertTrue(all(set(item) == {"candidate_id", "label", "categories", "text"} for item in payload["items"]))
            return validate({"suggestions": [{"text": "增加相册功能",
                "candidate_ids": [item["candidate_id"] for item in payload["items"]], "user_ids": ["invented-user"]}]})

        with patch("feedback.ai_client.request_json", side_effect=request) as model:
            result = analyze_report(snapshot, self.config, self.root / "cache", lambda **v: None)
            self.assertEqual(model.call_count, 2, "A consolidation pass also runs for one extraction batch")
            again = analyze_report(snapshot, self.config, self.root / "cache", lambda **v: None)
            self.assertEqual(model.call_count, 2)
        self.assertEqual(again, result)
        self.assertEqual(len(result["suggestions"]), 1)
        self.assertEqual(len(result["suggestions"][0]["feedback_ids"]), 3)
        text = render_markdown(snapshot, result, self.config)
        self.assertIn("1. 增加相册功能（用户 ID：001234、5434）", text)
        self.assertNotIn("反馈 ID：", text)
        self.assertNotIn("invented-user", text)
        # Display IDs are mapped locally; they are not added to model inputs.
        self.assertNotIn("001234", json.dumps(payloads))
        self.assertNotIn("5434", json.dumps(payloads))

    def test_distinct_requests_in_one_module_and_requests_in_other_modules_stay_separate(self):
        snapshot = self.snapshot([{}, {"具体主题": "个人信息页面", "中文翻译": "建议增加战绩筛选。"},
                                  {"最终标签": "玩法相关", "具体主题": "俱乐部", "中文翻译": "建议增加俱乐部排行榜。"}])

        def request(prompt, payload, config, validate, should_stop=None):
            if payload["stage"] == "提取":
                return validate({"suggestions": [{"text": item["text"], "feedback_ids": [item["feedback_id"]]}
                    for item in payload["items"]]})
            return validate({"suggestions": [{"text": item["text"], "candidate_ids": [item["candidate_id"]]}
                for item in payload["items"]]})

        with patch("feedback.ai_client.request_json", side_effect=request):
            result = analyze_report(snapshot, self.config, self.root / "cache", lambda **v: None)
        self.assertEqual(len(result["suggestions"]), 3)
        self.assertEqual(Counter(item["label"] for item in result["suggestions"]), {"其他": 2, "玩法相关": 1})

    def test_cross_module_merges_and_unknown_or_duplicate_references_are_rejected(self):
        snapshot = self.snapshot([{}, {"最终标签": "玩法相关", "具体主题": "俱乐部"}])
        cases = [(["s0", "s1"], "不同模块"), (["unknown"], "未知"), (["s0", "s0"], "重复")]
        for index, (refs, expected) in enumerate(cases):
            def request(prompt, payload, config, validate, should_stop=None):
                if payload["stage"] == "提取":
                    return validate({"suggestions": [{"text": "增加功能", "feedback_ids": [item["feedback_id"]]}
                        for item in payload["items"]]})
                return validate({"suggestions": [{"text": "增加功能", "candidate_ids": refs}]})
            with self.subTest(refs=refs), patch("feedback.ai_client.request_json", side_effect=request):
                with self.assertRaisesRegex(ValueError, expected):
                    analyze_report(snapshot, self.config, self.root / f"cache-{index}", lambda **v: None)

        def cross_module_extraction(prompt, payload, config, validate, should_stop=None):
            return validate({"suggestions": [{"text": "增加功能",
                "feedback_ids": [item["feedback_id"] for item in payload["items"]]}]})
        with patch("feedback.ai_client.request_json", side_effect=cross_module_extraction):
            with self.assertRaisesRegex(ValueError, "不同模块"):
                analyze_report(snapshot, self.config, self.root / "invalid-extraction", lambda **v: None)

    def test_identical_final_entries_are_deduplicated_within_their_module(self):
        snapshot = self.snapshot([{}, {}, {"最终标签": "玩法相关", "具体主题": "俱乐部"}])

        def request(prompt, payload, config, validate, should_stop=None):
            field = "feedback_ids" if payload["stage"] == "提取" else "candidate_ids"
            source = "feedback_id" if payload["stage"] == "提取" else "candidate_id"
            return validate({"suggestions": [{"text": "增加相册功能", field: [item[source]]} for item in payload["items"]]})

        with patch("feedback.ai_client.request_json", side_effect=request):
            result = analyze_report(snapshot, self.config, self.root / "cache", lambda **v: None)
        self.assertEqual(len(result["suggestions"]), 2)
        self.assertEqual(len(result["suggestions"][0]["feedback_ids"]), 2)
        self.assertNotEqual(result["suggestions"][0]["label"], result["suggestions"][1]["label"])

    def test_large_multistage_merge_preserves_all_contributors_with_bounded_inputs(self):
        snapshot = self.snapshot([{} for _ in range(250)])
        self.config["report"]["ai_max_input_chars"] = 3000
        calls = []

        def request(prompt, payload, config, validate, should_stop=None):
            calls.append(payload["stage"])
            self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False, sort_keys=True)), 3000)
            if payload["stage"] == "提取":
                ids = list(dict.fromkeys(item["feedback_id"] for item in payload["items"]))
                groups = [ids[offset::min(10, len(ids))] for offset in range(min(10, len(ids)))]
                return validate({"suggestions": [{"text": "增加相册功能", "feedback_ids": group} for group in groups]})
            return validate({"suggestions": [{"text": "增加相册功能",
                "candidate_ids": [item["candidate_id"] for item in payload["items"]]}]})

        with patch("feedback.ai_client.request_json", side_effect=request) as model:
            result = analyze_report(snapshot, self.config, self.root / "cache", lambda **v: None)
            count = model.call_count
            self.assertEqual(analyze_report(snapshot, self.config, self.root / "cache", lambda **v: None), result)
            self.assertEqual(model.call_count, count)
        self.assertGreater(calls.count("提取"), 1)
        self.assertGreater(calls.count("合并"), 1)
        self.assertEqual(len(result["suggestions"]), 1)
        self.assertEqual(set(result["suggestions"][0]["feedback_ids"]), set(snapshot["current"]["反馈ID"]))
        self.assertGreater(len(json.dumps(result["suggestions"][0])), 3000)
        text = render_markdown(snapshot, result, self.config)
        for user in snapshot["current"]["用户ID"]:
            self.assertIn(user, text)

    def test_missing_user_id_is_explicit_and_never_replaced_by_feedback_id(self):
        for users, expected in [(["", None], "用户 ID：未提供"),
                                (["001234", ""], "用户 ID：001234；另有用户 ID 未提供")]:
            snapshot = self.snapshot([{"用户ID": uid} for uid in users])
            result = {"suggestions": [{"text": "增加相册功能", "feedback_ids": list(snapshot["current"]["反馈ID"])}], "issues": {}}
            with self.subTest(users=users):
                text = render_markdown(snapshot, result, self.config)
                self.assertIn(expected, text)
                self.assertNotIn("feedback-", text)
