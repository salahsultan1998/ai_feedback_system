import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openpyxl import load_workbook

from feedback.ai_client import cached_request
from feedback.analysis import evidence
from feedback.config import ROOT
from feedback.report_language import REPORT_LANGUAGE_INSTRUCTIONS, validate_report_language
from feedback.storage import atomic_csv
from feedback.workflow import run_report
from tests.helpers import BaseTest, fake_request


class ReportLanguageTests(BaseTest):
    def test_report_prompts_enforce_chinese_without_changing_classification_rules(self):
        for name in ("suggestions", "issues"):
            self.assertIn(REPORT_LANGUAGE_INSTRUCTIONS, self.config[name + "_prompt"])
        original = (ROOT / "config/baloot/prompts/classification.md").read_text(encoding="utf-8")
        expected = original.replace("{{GAME_NAME}}", self.config["name"]).replace(
            "{{LABEL_SCHEMA}}", json.dumps(self.config["schema"], ensure_ascii=False))
        self.assertEqual(self.config["classification_prompt"], expected)

    def test_mixed_arabic_presentation_forms_and_encoded_arabic_are_rejected(self):
        for text in ("建议优化 بلوت 玩法", "玩家提到 ﺑﺎﻟﻮﺕ", "建议优化 &#1576;&#1604;&#1608;&#1578;", "增加金币 ١٠٠", "阿语标记：َ"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "阿拉伯语"):
                validate_report_language(text)
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "阿拉伯语"):
                evidence({"text": text, "feedback_ids": ["001"]}, {"001"}, self.config, 160)

    def test_chinese_with_abbreviations_numbers_and_punctuation_is_allowed(self):
        text = "玩家建议优化 VIP 购买和 AI 匹配，金币数量为 100。"
        self.assertEqual(validate_report_language(text), text)
        self.assertEqual(evidence({"text": text, "feedback_ids": ["001"]}, {"001"}, self.config, 160)["text"], text)

    def test_arabic_analysis_is_retried_and_only_chinese_is_cached(self):
        client = MagicMock()
        def response(text):
            item = {"text": text, "feedback_ids": ["001"]}
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(item)))])
        client.chat.completions.create.side_effect = [response("建议优化 بلوت"), response("建议优化某玩法的匹配速度。")]
        config = {**self.config, "max_retries": 1, "retry_delay": 0}
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-only"}), patch("feedback.ai_client.OpenAI") as factory:
            factory.return_value.__enter__.return_value = client
            result = cached_request("issues", {"items": [{"feedback_id": "001", "text": "已有翻译"}]},
                config, self.root / "analysis_cache", lambda value: evidence(value, {"001"}, config, 160))
        self.assertEqual(client.chat.completions.create.call_count, 2)
        self.assertEqual(result["text"], "建议优化某玩法的匹配速度。")
        caches = list((self.root / "analysis_cache").glob("*.json"))
        self.assertEqual(len(caches), 1)
        validate_report_language(json.loads(caches[0].read_text())["text"])

    def test_final_markdown_guard_blocks_unvalidated_arabic(self):
        with patch("feedback.ai_client.request_json", side_effect=fake_request), patch(
                "feedback.workflow.analyze_report", return_value={"suggestions": [
                    {"text": "建议优化 بلوت", "feedback_ids": ["004"]}], "issues": {}}):
            with self.assertRaisesRegex(ValueError, "阿拉伯语"):
                run_report(self.root, self.config, "2026-10-19", "2026-10-22", False, "blocked", lambda **v: None)
        self.assertFalse((self.root / "reports/blocked/report.md").exists())

    def test_original_arabic_is_preserved_in_detail_export(self):
        self.raw.loc[4, "用户自己填写的内容"] = "اقتراح تحسين اللعبة"
        atomic_csv(self.root / "rows.csv", self.raw)
        with patch("feedback.ai_client.request_json", side_effect=fake_request):
            run_report(self.root, self.config, "2026-10-19", "2026-10-22", False, "chinese", lambda **v: None)
        folder = self.root / "reports/chinese"
        validate_report_language((folder / "report.md").read_text(encoding="utf-8"))
        book = load_workbook(folder / "feedback_details.xlsx", read_only=True)
        try:
            self.assertEqual(book["本期反馈明细"]["H5"].value, "اقتراح تحسين اللعبة")
        finally:
            book.close()
