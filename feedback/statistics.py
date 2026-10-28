"""All report numbers and attention flags are computed here, not by AI."""
from .cleaning import parse_dates
from .selection import period_mask


def change_rate(current, previous):
    return (current - previous) / previous if previous else (0.0 if current == 0 else None)


def build_statistics(processed, selection, config):
    for column in ("是否有效", "是否建议"):
        if column not in processed or not processed[column].isin(["是", "否"]).all():
            raise ValueError("处理结果缺少有效性或建议判断，请按新规则重新生成。")
    dates = parse_dates(processed["反馈时间"])
    valid = processed["是否有效"].eq("是")
    current_mask = period_mask(dates, selection["periods"]["current"])
    previous_mask = period_mask(dates, selection["periods"]["previous"])
    current = processed.loc[valid & current_mask].copy()
    previous = processed.loc[valid & previous_mask].copy()
    excluded = processed.loc[~valid].copy()
    selection = {**selection, "candidate_current_count": int(current_mask.sum()),
        "candidate_previous_count": int(previous_mask.sum()), "processed_count": len(processed),
        "current_count": len(current), "previous_count": len(previous), "selected_count": int(valid.sum()),
        "semantic_excluded_in_period": len(excluded),
        "semantic_excluded_current": int((~valid & current_mask).sum()),
        "semantic_excluded_previous": int((~valid & previous_mask).sum()),
        "source_excluded_in_period": selection.get("excluded_in_period", 0),
        "excluded_in_period": selection.get("excluded_in_period", 0) + len(excluded)}
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
        suggestion_count = int((current["最终标签"].eq(label) & current["是否建议"].eq("是")).sum())
        modules.append(dict(label=label, count=count, previous=old, share=count / len(current) if len(current) else 0,
                            suggestion_count=suggestion_count,
                            delta=count-old, rate=rate, flagged=bool(flagged)))
    modules.sort(key=lambda item: -item["count"])
    return {"current": current, "previous": previous, "excluded": excluded, "modules": modules, "selection": selection,
            "suggestion_feedback_count": int(current["是否建议"].eq("是").sum()),
            "previous_suggestion_feedback_count": int(previous["是否建议"].eq("是").sum()),
            "total": len(current), "previous_total": len(previous), "rate": change_rate(len(current), len(previous))}
