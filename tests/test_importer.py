import pandas as pd
from feedback.importer import read_feedback
from feedback.cleaning import clean_feedback
from tests.helpers import BaseTest


class ImportTests(BaseTest):
    def test_csv_and_excel_keep_leading_zero_ids(self):
        self.raw.to_excel(self.root / "source.xlsx", index=False)
        for name in ("rows.csv", "source.xlsx"):
            data = read_feedback(self.root / name, self.config)
            self.assertEqual(data.iloc[0]["反馈ID"], "000")
            self.assertEqual(data.iloc[0]["用户ID"], "0009")

    def test_cleaning_explains_exclusions(self):
        data = pd.concat([self.raw, self.raw.iloc[[0]]], ignore_index=True)
        data.loc[1, "用户自己填写的内容"] = ""
        data.loc[2, "反馈时间"] = "not a date"
        data.loc[3, "反馈标签"] = "ignore"
        valid, excluded = clean_feedback(data, self.config)
        self.assertEqual(len(valid), 5)
        self.assertEqual(set(excluded["排除原因"]), {"反馈内容为空","反馈时间无效","源标签排除","反馈ID重复"})

    def test_missing_columns_and_row_limit(self):
        pd.DataFrame({"unrelated": ["value"]}).to_csv(self.root / "bad.csv", index=False)
        with self.assertRaises(ValueError):
            read_feedback(self.root / "bad.csv", self.config)
        with self.assertRaises(ValueError):
            read_feedback(self.root / "rows.csv", {**self.config, "max_rows": 2})
