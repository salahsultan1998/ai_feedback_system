from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from feedback.config import load_config
from feedback.storage import atomic_csv


def rows(config):
    dates = ["2026-10-01", "2026-10-12 00:00", "2026-10-15 23:59", "2026-10-16",
             "2026-10-19", "2026-10-22 23:59", "2026-10-23", "2026-10-31"]
    return pd.DataFrame([{**dict.fromkeys(config["column_mapping"], ""), "反馈ID": f"00{i}",
        "用户ID": "0009", "用户自己填写的内容": "=1+1" if i == 4 else f"synthetic feedback {i}",
        "反馈时间": day} for i, day in enumerate(dates)])


def fake_request(prompt, payload, config, validate, should_stop=None):
    if "feedbacks" in payload:
        return validate({"results": [{"feedback_id": item["feedback_id"],
            "translation_zh": "玩家建议改善游戏连接。", "label": "游戏卡顿", "theme": "", "subtheme": "",
            "is_valid": True, "exclusion_reason": ""}
            for item in payload["feedbacks"]]})
    if "feedback" in payload:
        return validate({"translation_zh": "玩家建议改善游戏连接。", "label": "游戏卡顿", "theme": "", "subtheme": "",
                         "is_valid": True, "exclusion_reason": ""})
    if "candidate_id" in payload["items"][0]:
        groups = {}
        for item in payload["items"]:
            groups.setdefault(item["label"], []).append(item["candidate_id"])
        return validate({"suggestions": [{"text": "改善游戏连接", "candidate_ids": ids}
            for ids in list(groups.values())[:payload["max_items"]]]})
    ids = [payload["items"][0].get("feedback_id") or payload["items"][0]["feedback_ids"][0]]
    if "label" in payload:
        return validate({"text": "玩家反映连接中断，希望改善连接。", "feedback_ids": ids})
    return validate({"suggestions": [{"text": "改善游戏连接", "feedback_ids": ids}]})


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = load_config("baloot")
        self.raw = rows(self.config)
        atomic_csv(self.root / "rows.csv", self.raw)
