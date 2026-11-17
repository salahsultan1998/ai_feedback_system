"""Okey reports preserve standard backend labels and match missing labels to the taxonomy."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import load_workbook
import pandas as pd

from feedback.config import load_config
from feedback.importer import read_feedback
from feedback.processing import validate_result
from feedback.storage import atomic_csv, read_csv
from feedback.workflow import run_report


class OkeyTests(unittest.TestCase):
    def test_model_cannot_exclude_unclear_feedback(self):
        config = load_config("okey")
        answer = {"translation_zh": "原文无法理解。", "subtheme": "", "is_valid": False,
                  "exclusion_reason": "内容无法理解"}
        with self.assertRaises(ValueError):
            validate_result(answer, config, "购买与支付")

    def test_unmatched_source_requires_model_to_return_a_standard_theme(self):
        config = load_config("okey")
        answer = {"translation_zh": "玩家无法登录账号。", "subtheme": "", "is_valid": True,
                  "exclusion_reason": ""}
        for original_label in ("", "停用的旧标签"):
            for fields in ({}, {"theme": ""}, {"theme": "停用的旧标签"}):
                with self.subTest(original_label=original_label, fields=fields):
                    with self.assertRaises(ValueError):
                        validate_result({**answer, **fields}, config, original_label)

    def test_standard_and_matched_labels_control_statistics_and_report_candidates(self):
        config = load_config("okey")
        self.assertEqual(config["classification_source"], "source_label")
        payment_topic = "Oyuncu çip satın alamıyor"
        bug_topic = "İnternet bağlantım kesiliyor"
        label_rows = [
            ("001", "购买与支付", "Yardım edin"),
            ("002", "购买与支付", "Çip satın alamıyorum"),
            ("003", "停用的旧标签", "İnternet bağlantım kesiliyor"),
            ("004", "", "Yeniden deneme düğmesi ekleyin"),
            ("005", "信息不完整", "Oyunla ilgili bir sorun var"),
            ("006", "建议与一般支持", "Yeniden deneme düğmesi ekleyin"),
            ("007", "BUG", "İnternet bağlantım kesiliyor"),
            ("008", "建议与一般支持", "Synthetic abusive feedback"),
            ("009", "重复反馈", "Synthetic repeated feedback"),
            ("010", "无效反馈", "Synthetic invalid feedback"),
            ("011", "账号访问与安全", "Hesabıma giriş yapamıyorum"),
        ]
        source = pd.DataFrame([
            {"反馈ID": feedback_id, "用户自己填写的内容": body, "反馈标签": label,
             "用户主动选择的标签": "提建议" if feedback_id == "003" else "",
             "反馈时间": "2026-11-09" if feedback_id == "011" else "2026-11-16"}
            for feedback_id, label, body in label_rows
        ])
        classifications = {
            "001": ("请帮帮我。", ""),
            "002": ("玩家反映无法购买金币。", payment_topic),
            "003": ("玩家反映网络连接中断。", bug_topic),
            "004": ("玩家建议增加重试按钮。", "Oyuncu öneri yaptığı zaman"),
            "005": ("玩家反映游戏出现问题。", payment_topic),
            "006": ("玩家建议增加重试按钮。", "Oyuncu öneri yaptığı zaman"),
            "007": ("玩家反映网络连接中断。", bug_topic),
            "008": ("含辱骂内容。", ""),
            "011": ("玩家反映无法登录账号。", "Hesabıma giriş yapamıyorum"),
        }

        def model(prompt, payload, config, validate, should_stop=None):
            if "feedbacks" in payload:
                answers = []
                for item in payload["feedbacks"]:
                    feedback_id = item["feedback_id"]
                    translation, subtheme = classifications[feedback_id]
                    abused = feedback_id == "008"
                    answer = {"feedback_id": feedback_id, "translation_zh": translation,
                              "subtheme": subtheme, "is_valid": not abused,
                              "exclusion_reason": "含辱骂内容" if abused else ""}
                    if feedback_id == "002":
                        answer.update(label="玩法相关", theme="Okey及桌游")
                    elif feedback_id == "003":
                        answer.update(label="玩法相关", theme="BUG")
                    elif feedback_id == "004":
                        answer["theme"] = "建议与一般支持"
                    answers.append(answer)
                return validate({"results": answers})
            ids = [item["feedback_id"] for item in payload["items"]]
            if payload.get("task") == "bugs":
                self.assertEqual(ids, ["003", "007"])
                return validate({"bugs": [{"text": "玩家反映，网络连接中断，具体情况仍待核查。", "feedback_ids": ids}]})
            if "label" in payload:
                self.assertNotEqual(payload["label"], "BUG")
                return validate({"text": "玩家反馈了该模块的问题。", "feedback_ids": ids[:1]})
            self.assertEqual(ids, ["004", "006"])
            return validate({"suggestions": [{"text": "增加重试按钮。", "feedback_ids": ids}]})

        with TemporaryDirectory() as temp:
            folder = Path(temp)
            csv_path = folder / "source.csv"
            source.to_csv(csv_path, index=False)
            atomic_csv(folder / "rows.csv", read_feedback(csv_path, config))
            with patch("feedback.ai_client.request_json", side_effect=model) as request:
                result = run_report(folder, config, "2026-11-16", "2026-11-16", False, "first", lambda **kw: None)
                calls = request.call_count
                run_report(folder, config, "2026-11-16", "2026-11-16", False, "retry", lambda **kw: None)
                self.assertEqual(request.call_count, calls)
            self.assertEqual((result["total"], result["previous_total"]), (7, 1))
            self.assertEqual(result["selection"]["excluded_in_period"], 3)
            self.assertEqual(result["selection"]["abusive_excluded_in_period"], 1)
            self.assertEqual((result["suggestions"], result["suggestion_feedback_count"]), (1, 2))

            saved = read_csv(folder / "processed.csv").set_index("反馈ID")
            categories = ["最终标签", "具体主题", "三级主题"]
            expected = {
                "001": ("金币相关", "购买与支付", ""),
                "002": ("金币相关", "购买与支付", payment_topic),
                "003": ("BUG", "BUG", bug_topic),
                "004": ("其他", "建议与一般支持", "Oyuncu öneri yaptığı zaman"),
                "005": ("其他", "信息不完整", ""),
                "007": ("BUG", "BUG", bug_topic),
            }
            for feedback_id, path in expected.items():
                with self.subTest(feedback_id=feedback_id):
                    self.assertEqual(tuple(saved.loc[feedback_id, categories]), path)
            self.assertEqual(saved.loc["003", "反馈标签"], "停用的旧标签")
            self.assertEqual(saved.loc["004", "反馈标签"], "")

            report = folder / "reports" / "first"
            review = json.loads((report / "review.json").read_text(encoding="utf-8"))
            self.assertEqual(review["suggestions"][0]["feedback_ids"], ["004", "006"])
            self.assertEqual(review["bugs"][0]["feedback_ids"], ["003", "007"])
            report_body = (report / "report.md").read_text(encoding="utf-8")
            self.assertIn("1. 网络连接中断，具体情况仍待核查。（相关反馈：2 条；反馈 ID：003、007）", report_body)
            self.assertNotIn("| BUG |", report_body)
            self.assertIn("| 金币相关 | 2（28.6%） | 0 |", report_body)
            self.assertIn("| 其他 | 3（42.9%） | 0 |", report_body)

            workbook = load_workbook(report / "feedback_details.xlsx", read_only=True)
            try:
                sheet = workbook["本期反馈明细"]
                headers = [cell.value for cell in sheet[4]]
                details = {row[headers.index("反馈ID")]: row for row in sheet.iter_rows(min_row=5, values_only=True)}
                self.assertEqual(set(details), {f"00{i}" for i in range(1, 8)})
                self.assertEqual(details["002"][headers.index("II 类别")], f"购买与支付（{payment_topic}）")
                self.assertEqual(details["001"][headers.index("中文翻译")], "请帮帮我。")
                self.assertEqual(details["001"][headers.index("原文")], "Yardım edin")
            finally:
                workbook.close()
