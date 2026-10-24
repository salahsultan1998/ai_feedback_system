"""Select the union of the two periods before any AI calls."""
import pandas as pd

from .cleaning import parse_dates
from .periods import report_periods


def period_mask(dates, period):
    return (dates >= pd.Timestamp(period["start"])) & (dates < pd.Timestamp(period["end"]) + pd.Timedelta(days=1))


def select_feedback(raw, cleaned, start, end, config, confirmed=False):
    periods = report_periods(start, end, config["max_report_days"])
    dates = cleaned["反馈时间"]
    current = period_mask(dates, periods["current"])
    previous = period_mask(dates, periods["previous"])
    if not current.any():
        raise ValueError("所选报告期间没有有效反馈，请调整日期或上传其他文件。")
    source_dates = parse_dates(raw["反馈时间"]).dropna()
    minimum, maximum = source_dates.min().normalize(), source_dates.max().normalize()
    required_first = pd.Timestamp(periods["previous"]["start"])
    required_last = pd.Timestamp(periods["current"]["end"])
    inferred_covered = minimum <= required_first and maximum >= required_last
    missing_days = {}
    for key, period in periods.items():
        present = set(source_dates.loc[period_mask(source_dates, period)].dt.strftime("%Y-%m-%d"))
        missing_days[key] = [d.strftime("%Y-%m-%d") for d in pd.date_range(period["start"], period["end"]) if d.strftime("%Y-%m-%d") not in present]
    selected = cleaned.loc[current | previous].copy()
    info = {"periods": periods, "current_count": int(current.sum()), "previous_count": int(previous.sum()),
            "selected_count": len(selected), "outside_count": len(cleaned) - len(selected),
            "inferred_covered": bool(inferred_covered), "confirmed": bool(confirmed),
            "ready": bool(inferred_covered or confirmed), "days_without_records": missing_days,
            "source_start": str(minimum.date()), "source_end": str(maximum.date())}
    return selected, info
