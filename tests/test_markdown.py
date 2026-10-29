from unittest import TestCase

from feedback.exports.markdown import change, render_markdown


class MarkdownChangeTests(TestCase):
    def test_changes_use_arrows_and_preserve_zero_baseline_and_flat_cases(self):
        for current, previous, expected in [(60, 50, "↑ 10 条（+20.0%）"),
                (45, 50, "↓ 5 条（-10.0%）"), (0, 10, "↓ 10 条（-100.0%）"),
                (10, 10, "持平（0.0%）"), (0, 0, "持平"), (10, 0, "新增（上周同期为 0）")]:
            with self.subTest(current=current, previous=previous):
                self.assertEqual(change(current, previous), expected)

    def test_report_overview_and_module_table_share_arrow_format(self):
        snapshot = {"selection": {"periods": {"current": {"start": "2026-10-19", "end": "2026-10-25"},
                    "previous": {"start": "2026-10-12", "end": "2026-10-18"}}},
            "total": 15, "previous_total": 15, "suggestion_feedback_count": 0,
            "modules": [{"label": "俱乐部", "count": 10, "previous": 5, "share": 2/3, "flagged": False},
                        {"label": "账号问题", "count": 5, "previous": 10, "share": 1/3, "flagged": False}]}
        text = render_markdown(snapshot, {"suggestions": [], "issues": {}}, {"name": "测试游戏"})
        self.assertIn("变化：持平（0.0%）", text)
        self.assertIn("↑ 5 条（+100.0%）", text)
        self.assertIn("↓ 5 条（-50.0%）", text)
        self.assertNotIn("增加", text)
        self.assertNotIn("减少", text)
