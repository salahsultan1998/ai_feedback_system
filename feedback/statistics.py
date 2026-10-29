"""All report numbers and attention flags are computed here, not by AI."""
from .cleaning import parse_dates
from .selection import period_mask
from .policies import abuse_mask, suggestion_mask
from .taxonomy import validate_path


def change_rate(current, previous):
    return (current - previous) / previous if previous else (0.0 if current == 0 else None)


def build_statistics(processed, selection, config):
    if "是否有效" not in processed or not processed["是否有效"].isin(["是", "否"]).all():
        raise ValueError("处理结果缺少有效性判断，请按新规则重新生成。")
    processed = processed.copy()
    dates = parse_dates(processed["反馈时间"])
    abusive = abuse_mask(processed)
    valid = processed["是否有效"].eq("是") & ~abusive
    for row in processed.loc[valid].to_dict("records"):
        if not isinstance(row.get("三级主题"), str):
            raise ValueError("处理结果缺少三级分类，请按新规则重新生成。")
        validate_path(row["最终标签"], row["具体主题"], row["三级主题"], config["schema"])
    semantic_excluded = ~valid & ~abusive
    processed["是否建议"] = (valid & suggestion_mask(processed, config)).map({True: "是", False: "否"})
    current_mask = period_mask(dates, selection["periods"]["current"])
    previous_mask = period_mask(dates, selection["periods"]["previous"])
    current = processed.loc[valid & current_mask].copy()
    previous = processed.loc[valid & previous_mask].copy()
    excluded = processed.loc[semantic_excluded].copy()
    selection = {**selection, "candidate_current_count": int(current_mask.sum()),
        "candidate_previous_count": int(previous_mask.sum()), "processed_count": len(processed),
        "current_count": len(current), "previous_count": len(previous), "selected_count": int(valid.sum()),
        "semantic_excluded_in_period": len(excluded),
        "semantic_excluded_current": int((semantic_excluded & current_mask).sum()),
        "semantic_excluded_previous": int((semantic_excluded & previous_mask).sum()),
        "abusive_excluded_in_period": int(abusive.sum()),
        "abusive_excluded_current": int((abusive & current_mask).sum()),
        "abusive_excluded_previous": int((abusive & previous_mask).sum()),
        "source_excluded_in_period": selection.get("excluded_in_period", 0),
        "excluded_in_period": selection.get("excluded_in_period", 0) + int((~valid).sum())}
    counts, old_counts = current["最终标签"].value_counts(), previous["最终标签"].value_counts()
    thresholds = config["report"]["module_growth_thresholds"]
    modules = []
    for label in config["schema"]["labels"]:
        count, old = int(counts.get(label, 0)), int(old_counts.get(label, 0))
        if not count and not old:
            continue
        rate = change_rate(count, old)
        threshold = thresholds[label]
        # A zero baseline has no percentage: any current feedback is new.
        flagged = count > 0 and (old == 0 or rate >= threshold)
        suggestion_count = int((current["最终标签"].eq(label) & current["是否建议"].eq("是")).sum())
        modules.append(dict(label=label, count=count, previous=old, share=count / len(current) if len(current) else 0,
                            suggestion_count=suggestion_count,
                            delta=count-old, rate=rate, growth_threshold=threshold, flagged=bool(flagged)))
    modules.sort(key=lambda item: -item["count"])
    return {"current": current, "previous": previous, "excluded": excluded, "modules": modules, "selection": selection,
            "suggestion_feedback_count": int(current["是否建议"].eq("是").sum()),
            "previous_suggestion_feedback_count": int(previous["是否建议"].eq("是").sum()),
            "total": len(current), "previous_total": len(previous), "rate": change_rate(len(current), len(previous))}
