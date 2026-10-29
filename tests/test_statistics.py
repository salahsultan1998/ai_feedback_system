import pandas as pd

from feedback.statistics import build_statistics, change_rate
from feedback.cleaning import clean_feedback
from feedback.selection import select_feedback
from tests.helpers import BaseTest


class StatisticsTests(BaseTest):
    def counted_modules(self, counts):
        rows = []
        for label, (current, previous) in counts.items():
            for period, count in (("2026-10-20", current), ("2026-10-13", previous)):
                for _ in range(count):
                    rows.append({**self.raw.iloc[0].to_dict(), "反馈ID": f"sample-{len(rows)}", "反馈时间": period,
                        "最终标签": label, "具体主题": "", "三级主题": "", "是否有效": "是", "排除原因": ""})
        snapshot = build_statistics(pd.DataFrame(rows), {"periods": {
            "current": {"start": "2026-10-19", "end": "2026-10-25"},
            "previous": {"start": "2026-10-12", "end": "2026-10-18"}}}, self.config)
        return {item["label"]: item for item in snapshot["modules"]}

    def test_each_root_uses_its_own_growth_threshold(self):
        self.config["report"]["module_growth_thresholds"].update({"账号问题": .1, "游戏卡顿": .5})
        modules = self.counted_modules({"账号问题": (11, 10), "游戏卡顿": (11, 10)})
        self.assertTrue(modules["账号问题"]["flagged"])
        self.assertFalse(modules["游戏卡顿"]["flagged"])
        self.assertEqual(modules["账号问题"]["growth_threshold"], .1)
        self.assertEqual(modules["账号问题"]["delta"], 1)

    def test_percentage_is_inclusive_without_count_floors(self):
        self.config["report"]["module_growth_thresholds"].update({"账号问题": .1, "游戏卡顿": .1, "充值": .5})
        modules = self.counted_modules({"账号问题": (109, 100), "游戏卡顿": (110, 100), "充值": (2, 1)})
        self.assertFalse(modules["账号问题"]["flagged"])
        self.assertTrue(modules["游戏卡顿"]["flagged"])
        self.assertTrue(modules["充值"]["flagged"], "Fewer than ten current rows and one added row can trigger")

    def test_new_declining_flat_and_over_one_hundred_percent_cases(self):
        self.config["report"]["module_growth_thresholds"].update({"账号问题": 2, "游戏卡顿": 0, "充值": 2, "其他": 0})
        modules = self.counted_modules({"账号问题": (1, 0), "游戏卡顿": (1, 2), "充值": (3, 1), "其他": (1, 1)})
        self.assertTrue(modules["账号问题"]["flagged"])
        self.assertIsNone(modules["账号问题"]["rate"])
        self.assertFalse(modules["游戏卡顿"]["flagged"])
        self.assertTrue(modules["充值"]["flagged"])
        self.assertTrue(modules["其他"]["flagged"])

    def test_zero_baseline_and_thresholds(self):
        self.assertIsNone(change_rate(3,0))
        self.assertEqual(change_rate(0,0),0)
        self.assertEqual(change_rate(15,10),.5)
        cleaned, _ = clean_feedback(self.raw,self.config)
        selected, info = select_feedback(self.raw,cleaned,"2026-10-19","2026-10-22",self.config)
        selected["最终标签"] = ["账号问题","账号问题","游戏卡顿","游戏卡顿"]
        selected["具体主题"] = ""
        selected["三级主题"] = ""
        selected["是否有效"], selected["是否建议"], selected["排除原因"] = "是", "否", ""
        snapshot = build_statistics(selected,info,self.config)
        modules = {item["label"]:item for item in snapshot["modules"]}
        self.assertTrue(modules["游戏卡顿"]["flagged"])
        self.assertEqual(modules["账号问题"]["count"],0)
        self.assertEqual(modules["账号问题"]["rate"],-1)
        self.assertEqual(sum(item["share"] for item in snapshot["modules"]),1)
