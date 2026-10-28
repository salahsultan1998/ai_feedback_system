from feedback.statistics import build_statistics, change_rate
from feedback.cleaning import clean_feedback
from feedback.selection import select_feedback
from tests.helpers import BaseTest


class StatisticsTests(BaseTest):
    def test_zero_baseline_and_thresholds(self):
        self.assertIsNone(change_rate(3,0))
        self.assertEqual(change_rate(0,0),0)
        self.assertEqual(change_rate(15,10),.5)
        cleaned, _ = clean_feedback(self.raw,self.config)
        selected, info = select_feedback(self.raw,cleaned,"2026-10-19","2026-10-22",self.config)
        selected["最终标签"] = ["账号问题","账号问题","游戏卡顿","游戏卡顿"]
        selected["具体主题"] = ""
        selected["是否有效"], selected["是否建议"], selected["排除原因"] = "是", "否", ""
        self.config["report"]["anomaly"]["new_module_min_count"] = 2
        snapshot = build_statistics(selected,info,self.config)
        modules = {item["label"]:item for item in snapshot["modules"]}
        self.assertTrue(modules["游戏卡顿"]["flagged"])
        self.assertEqual(modules["账号问题"]["count"],0)
        self.assertEqual(modules["账号问题"]["rate"],-1)
        self.assertEqual(sum(item["share"] for item in snapshot["modules"]),1)
