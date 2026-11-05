import json
import unittest

from feedback.analysis import batches


class AnalysisBatchTests(unittest.TestCase):
    def test_exact_serialized_budget_keeps_unicode_records_and_metadata(self):
        metadata = {"stage": "提取", "rules": {"limit": 10, "description": "报告"}}
        items = [{"feedback_id": f"00{i}", "text": "玩家说：العربية\n中文"} for i in range(5)]
        limit = len(json.dumps({**metadata, "items": items[:2]}, ensure_ascii=False, sort_keys=True))

        grouped = list(batches(iter(items), metadata, limit))

        self.assertEqual(grouped, [items[:2], items[2:4], items[4:]])
        for batch in grouped:
            self.assertLessEqual(len(json.dumps({**metadata, "items": batch}, ensure_ascii=False,
                                                sort_keys=True)), limit)
        self.assertEqual(metadata, {"stage": "提取", "rules": {"limit": 10, "description": "报告"}})

    def test_single_record_fills_the_budget_and_one_less_character_rejects_it(self):
        metadata = {"stage": "合并"}
        item = {"text": "含引号\"和换行\n的反馈", "feedback_ids": ["0001", "0002"]}
        limit = len(json.dumps({**metadata, "items": [item]}, ensure_ascii=False, sort_keys=True))

        self.assertEqual(list(batches([item], metadata, limit)), [[item]])
        with self.assertRaisesRegex(ValueError, "单条分析数据超过长度限制"):
            list(batches([item], metadata, limit - 1))

    def test_empty_input_has_no_batches(self):
        self.assertEqual(list(batches([], {"stage": "提取"}, 1)), [])
