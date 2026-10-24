import unittest
from feedback.periods import report_periods


class PeriodTests(unittest.TestCase):
    def test_partial_week_compares_same_weekdays(self):
        periods = report_periods("2026-10-19", "2026-10-22")
        self.assertEqual(periods["previous"], {"start": "2026-10-12", "end": "2026-10-15"})

    def test_single_day_and_month_boundary(self):
        p = report_periods("2026-11-02", "2026-11-02")
        self.assertEqual(p["previous"]["end"], "2026-10-26")

    def test_invalid_ranges(self):
        for start, end in [(None,None), ("bad","2026-10-22"), ("2026-10-22","2026-10-19"), ("2026-01-01","2026-12-31")]:
            with self.assertRaises(ValueError):
                report_periods(start,end)
