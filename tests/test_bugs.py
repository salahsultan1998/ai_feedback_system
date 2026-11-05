import copy
import json
from unittest.mock import patch

import pandas as pd

from feedback.bugs import analyze_bugs
from tests.helpers import BaseTest


class BugAnalysisTests(BaseTest):
    def snapshot(self, entries):
        rows = pd.DataFrame([{"反馈ID": f"00{index:04d}", "用户ID": "private-user", "最终标签": "其他",
            "具体主题": "个人信息页面", "三级主题": "", "反馈标签": "BUG", "排除原因": "",
            "中文翻译": f"玩家反映点击战绩第{index}页按钮后页面闪退。", "是否有效": "是", **entry}
            for index, entry in enumerate(entries)])
        return {"current": rows, "previous": pd.DataFrame([{"反馈ID": "old", "中文翻译": "上周问题"}]),
                "modules": [{"label": "其他", "flagged": False}]}

    @staticmethod
    def distinct(prompt, payload, config, validate, should_stop=None):
        source = "candidate_id" if payload["stage"] == "合并" else "feedback_id"
        field = "candidate_ids" if source == "candidate_id" else "feedback_ids"
        return validate({"bugs": [{"text": item["text"], field: [item[source]]} for item in payload["items"]]})

    def test_only_exact_trimmed_bug_source_labels_enter_valid_current_pool(self):
        snapshot = self.snapshot([{}, {"反馈标签": " BUG "}, {"反馈标签": "建议"},
            {"反馈标签": "bug"}, {"反馈标签": "BUG反馈"},
            {"反馈标签": "", "用户主动选择的标签": "BUG"},
            {"反馈标签": "", "中文翻译": "BUG：玩家反映按钮失效。"},
            {"是否有效": "否"}, {"排除原因": "含辱骂内容"}, {"最终标签": "辱骂"},
            {"反馈标签": "BUG", "用户主动选择的标签": "建议"}])
        calls = []

        def request(prompt, payload, config, validate, should_stop=None):
            calls.append(payload)
            self.assertEqual(payload["task"], "bugs")
            return self.distinct(prompt, payload, config, validate, should_stop)

        with patch("feedback.ai_client.request_json", side_effect=request):
            result = analyze_bugs(snapshot, self.config, self.root / "cache", lambda **v: None)
        seen = [item["feedback_id"] for payload in calls if payload["stage"] == "提取" for item in payload["items"]]
        self.assertEqual(seen, ["000000", "000001", "000010"])
        self.assertEqual(len(result), 3)
        self.assertNotIn("private-user", json.dumps(calls))
        self.assertNotIn("is_suggestion", json.dumps(calls))
        self.assertEqual(result[0]["categories"], ["个人信息页面"])

    def test_no_matching_source_labels_make_no_bug_requests(self):
        snapshot = self.snapshot([{"反馈标签": label, "用户主动选择的标签": "BUG",
                                   "中文翻译": "BUG：点击按钮后闪退。"}
                                  for label in ("", "建议", "bug", "BUG反馈", None)])
        with patch("feedback.ai_client.request_json") as model:
            self.assertEqual(analyze_bugs(snapshot, self.config, self.root / "cache", lambda **v: None), [])
            model.assert_not_called()
        snapshot["current"] = snapshot["current"].drop(columns="反馈标签")
        with patch("feedback.ai_client.request_json") as model:
            self.assertEqual(analyze_bugs(snapshot, self.config, self.root / "missing-tag-cache", lambda **v: None), [])
            model.assert_not_called()

    def test_default_bug_label_and_explicit_custom_labels_select_their_own_sources(self):
        snapshot = self.snapshot([{}, {"反馈标签": "bug"}, {"反馈标签": "故障"}, {"反馈标签": " 故障 "}])
        default = copy.deepcopy(self.config)
        default.pop("bug_labels", None)
        with patch("feedback.ai_client.request_json", side_effect=self.distinct) as model:
            result = analyze_bugs(snapshot, default, self.root / "cache", lambda **v: None)
            self.assertEqual([fid for item in result for fid in item["feedback_ids"]], ["000000"])
            changed = copy.deepcopy(default)
            changed["bug_labels"] = ["故障"]
            result = analyze_bugs(snapshot, changed, self.root / "cache", lambda **v: None)
            self.assertEqual({fid for item in result for fid in item["feedback_ids"]}, {"000002", "000003"})
            calls = model.call_count
            self.assertEqual(analyze_bugs(snapshot, changed, self.root / "cache", lambda **v: None), result)
            self.assertEqual(model.call_count, calls)

    def test_merge_preserves_all_contributors_and_uses_compact_references(self):
        snapshot = self.snapshot([{} for _ in range(4)])

        def request(prompt, payload, config, validate, should_stop=None):
            if payload["stage"] == "提取":
                return self.distinct(prompt, payload, config, validate, should_stop)
            self.assertTrue(all(set(item) == {"candidate_id", "label", "categories", "text"}
                                for item in payload["items"]))
            return validate({"bugs": [{"text": "玩家反映打开战绩页面闪退，待核查。",
                                      "candidate_ids": [item["candidate_id"] for item in payload["items"]]}]})

        with patch("feedback.ai_client.request_json", side_effect=request):
            result = analyze_bugs(snapshot, self.config, self.root / "cache", lambda **v: None)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["feedback_ids"], list(snapshot["current"]["反馈ID"]))

    def test_retries_reuse_cache_and_threshold_or_suggestion_settings_do_not_change_it(self):
        snapshot = self.snapshot([{}, {}])
        with patch("feedback.ai_client.request_json", side_effect=self.distinct) as model:
            expected = analyze_bugs(snapshot, self.config, self.root / "cache", lambda **v: None)
            count = model.call_count
            changed = copy.deepcopy(self.config)
            changed["report"]["module_growth_thresholds"] = {"其他": 100}
            changed["report"]["max_suggestions"] = 1
            changed["report"]["max_evidence_ids"] = 1
            changed["suggestion_labels"] = ["别的标签"]
            self.assertEqual(analyze_bugs(snapshot, changed, self.root / "cache", lambda **v: None), expected)
            self.assertEqual(model.call_count, count)

    def test_unknown_and_duplicate_evidence_rejected(self):
        snapshot = self.snapshot([{}])
        for index, refs in enumerate((["unknown"], ["000000", "000000"])):
            def request(prompt, payload, config, validate, should_stop=None):
                return validate({"bugs": [{"text": "玩家反映页面闪退。", "feedback_ids": refs}]})
            with self.subTest(refs=refs), patch("feedback.ai_client.request_json", side_effect=request):
                with self.assertRaises(ValueError):
                    analyze_bugs(snapshot, self.config, self.root / f"bad-{index}", lambda **v: None)

    def test_cross_module_and_full_category_path_extraction_rejected(self):
        for entry in ({"最终标签": "玩法相关"}, {"具体主题": "相册功能"}, {"三级主题": "不同场景"}):
            snapshot = self.snapshot([{}, entry])
            def request(prompt, payload, config, validate, should_stop=None):
                return validate({"bugs": [{"text": "玩家反映页面闪退。",
                    "feedback_ids": [item["feedback_id"] for item in payload["items"]]}]})
            with self.subTest(entry=entry), patch("feedback.ai_client.request_json", side_effect=request):
                with self.assertRaisesRegex(ValueError, "分类路径"):
                    analyze_bugs(snapshot, self.config, self.root / "bad-path", lambda **v: None)

    def test_merge_cannot_drop_or_duplicate_candidates(self):
        snapshot = self.snapshot([{}, {}])
        for index, refs in enumerate((["b0"], ["b0", "b1", "b0"])):
            def request(prompt, payload, config, validate, should_stop=None):
                if payload["stage"] == "提取":
                    return self.distinct(prompt, payload, config, validate, should_stop)
                return validate({"bugs": [{"text": "玩家反映页面闪退。", "candidate_ids": refs}]})
            with self.subTest(refs=refs), patch("feedback.ai_client.request_json", side_effect=request):
                with self.assertRaises(ValueError):
                    analyze_bugs(snapshot, self.config, self.root / f"bad-merge-{index}", lambda **v: None)

    def test_distinct_large_list_is_bounded_and_never_truncated(self):
        snapshot = self.snapshot([{} for _ in range(35)])
        self.config["report"]["ai_max_input_chars"] = 3000
        calls = []
        def request(prompt, payload, config, validate, should_stop=None):
            calls.append(payload)
            self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False, sort_keys=True)), 3000)
            return self.distinct(prompt, payload, config, validate, should_stop)
        with patch("feedback.ai_client.request_json", side_effect=request):
            result = analyze_bugs(snapshot, self.config, self.root / "cache", lambda **v: None)
        self.assertGreater(sum(payload["stage"] == "合并" for payload in calls), 1)
        self.assertEqual(len(result), 35)
        self.assertEqual({fid for item in result for fid in item["feedback_ids"]}, set(snapshot["current"]["反馈ID"]))

    def test_large_merged_issue_keeps_all_evidence_without_sending_growing_source_lists(self):
        snapshot = self.snapshot([{} for _ in range(100)])
        self.config["report"]["ai_max_input_chars"] = 3000
        def request(prompt, payload, config, validate, should_stop=None):
            self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False, sort_keys=True)), 3000)
            if payload["stage"] == "提取":
                return self.distinct(prompt, payload, config, validate, should_stop)
            self.assertNotIn("feedback_ids", json.dumps(payload))
            return validate({"bugs": [{"text": "玩家反映战绩页面闪退，待核查。",
                "candidate_ids": [item["candidate_id"] for item in payload["items"]]}]})
        with patch("feedback.ai_client.request_json", side_effect=request):
            result = analyze_bugs(snapshot, self.config, self.root / "cache", lambda **v: None)
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0]["feedback_ids"]), set(snapshot["current"]["反馈ID"]))

    def test_different_category_paths_remain_separate_even_with_identical_text(self):
        snapshot = self.snapshot([{"中文翻译": "玩家反映打开页面闪退。"},
                                  {"具体主题": "相册功能", "中文翻译": "玩家反映打开页面闪退。"}])
        with patch("feedback.ai_client.request_json", side_effect=self.distinct) as model:
            result = analyze_bugs(snapshot, self.config, self.root / "cache", lambda **v: None)
        self.assertEqual(len(result), 2)
        self.assertEqual(model.call_count, 1)

    def test_long_translations_are_fragmented_without_new_translation_calls(self):
        snapshot = self.snapshot([{"中文翻译": "玩家反映战绩页面闪退。" * 800}])
        self.config["report"]["ai_max_input_chars"] = 3000
        lengths = []
        def request(prompt, payload, config, validate, should_stop=None):
            if payload["stage"] == "提取":
                lengths.extend(len(item["text"]) for item in payload["items"])
            return validate({"bugs": []})
        with patch("feedback.ai_client.request_json", side_effect=request):
            self.assertEqual(analyze_bugs(snapshot, self.config, self.root / "cache", lambda **v: None), [])
        self.assertGreater(len(lengths), 1)
        self.assertEqual(sum(lengths), len(snapshot["current"].iloc[0]["中文翻译"]))

    def test_cancellation_is_checked_before_model_request(self):
        with patch("feedback.ai_client.request_json") as model:
            with self.assertRaises(InterruptedError):
                analyze_bugs(self.snapshot([{}]), self.config, self.root / "cache", lambda **v: None, lambda: True)
        model.assert_not_called()
