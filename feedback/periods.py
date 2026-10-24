"""User-facing dates are inclusive; comparisons always shift back seven days."""
from datetime import date, timedelta


def report_periods(start, end, max_days=31):
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (ValueError, TypeError):
        raise ValueError("请选择有效的开始和结束日期。") from None
    if first > last or (last - first).days + 1 > max_days:
        raise ValueError(f"结束日期不可早于开始日期，且期间不可超过 {max_days} 天。")
    return {
        "current": {"start": first.isoformat(), "end": last.isoformat()},
        "previous": {"start": (first - timedelta(days=7)).isoformat(),
                     "end": (last - timedelta(days=7)).isoformat()},
    }
