"""All report numbers and attention flags are computed here, not by AI."""
from .cleaning import parse_dates
from .selection import period_mask


def change_rate(current, previous):
    return (current - previous) / previous if previous else (0.0 if current == 0 else None)


def build_statistics(processed, selection, config):
    dates = parse_dates(processed["反馈时间"])
    current = processed.loc[period_mask(dates, selection["periods"]["current"])].copy()
    previous = processed.loc[period_mask(dates, selection["periods"]["previous"])].copy()
    counts, old_counts = current["最终标签"].value_counts(), previous["最终标签"].value_counts()
    rules = config["report"]["anomaly"]
    modules = []
    for label in config["schema"]["labels"]:
        count, old = int(counts.get(label, 0)), int(old_counts.get(label, 0))
        if not count and not old:
            continue
        rate = change_rate(count, old)
        flagged = ((not old and count >= rules["new_module_min_count"]) or
                   (old > 0 and count >= rules["min_count"] and count - old >= rules["min_increase"]
                    and rate >= rules["min_growth_rate"]))
        modules.append(dict(label=label, count=count, previous=old, share=count / len(current),
                            delta=count-old, rate=rate, flagged=bool(flagged)))
    modules.sort(key=lambda item: -item["count"])
    return {"current": current, "previous": previous, "modules": modules, "selection": selection,
            "total": len(current), "previous_total": len(previous), "rate": change_rate(len(current), len(previous))}
