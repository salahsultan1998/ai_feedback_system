"""Evidence-linked suggestions and issue descriptions from current-period rows."""
import json

from .ai_client import cached_request, check_stop
from .report_language import validate_report_language


def evidence(item, allowed, config, max_chars):
    if not isinstance(item, dict) or not isinstance(item.get("text"), str):
        raise ValueError("AI 分析缺少文字。")
    text, ids = item["text"].strip(), item.get("feedback_ids")
    if not text or len(text) > max_chars or not isinstance(ids, list) or not 1 <= len(ids) <= config["report"]["max_evidence_ids"]:
        raise ValueError("AI 分析文字长度或引用数量无效。")
    validate_report_language(text)
    if any(not isinstance(fid, str) or fid not in allowed for fid in ids) or len(set(ids)) != len(ids):
        raise ValueError("AI 分析引用了未知或重复的反馈 ID。")
    return {"text": text, "feedback_ids": ids}


def batches(items, metadata, limit):
    batch = []
    for item in items:
        if len(json.dumps({**metadata, "items": [item]}, ensure_ascii=False, sort_keys=True)) > limit:
            raise ValueError("单条分析数据超过长度限制。")
        if batch and len(json.dumps({**metadata, "items": batch + [item]}, ensure_ascii=False, sort_keys=True)) > limit:
            yield batch
            batch = []
        batch.append(item)
    if batch:
        yield batch


def fragments(rows, config):
    size = (config["report"]["ai_max_input_chars"] - 1500) // 6
    for row in rows.sort_values("反馈ID").to_dict("records"):
        for offset in range(0, len(row["中文翻译"]), size):
            yield {"feedback_id": str(row["反馈ID"]), "label": row["最终标签"],
                   "theme": row["具体主题"] or "未细分", "is_suggestion": row["是否建议"] == "是",
                   "text": row["中文翻译"][offset:offset + size]}


def analyze_report(snapshot, config, cache_dir, progress, should_stop=None):
    rules = config["report"]
    limit = rules["ai_max_input_chars"]

    def ask(name, batch, metadata, suggestions=False):
        check_stop(should_stop)
        allowed = set()
        for item in batch:
            allowed.update(item.get("feedback_ids", [item.get("feedback_id")]))

        def validate(value):
            if not suggestions:
                return evidence(value, allowed, config, 160)
            if not isinstance(value, dict) or not isinstance(value.get("suggestions"), list):
                raise ValueError("AI 建议必须为列表。")
            if len(value["suggestions"]) > metadata["max_items"]:
                raise ValueError("AI 建议超过数量限制。")
            return {"suggestions": [evidence(item, allowed, config, 140) for item in value["suggestions"]]}
        return cached_request(name, {**metadata, "items": batch}, config, cache_dir, validate, should_stop)

    progress(stage="analysis", message="正在提炼玩家建议", done=0, total=0)
    meta = {"stage": "提取", "max_items": rules["max_suggestions"], "max_evidence_ids": rules["max_evidence_ids"]}
    suggestions = []
    suggestion_rows = snapshot["current"].loc[snapshot["current"]["是否建议"].eq("是")]
    initial_batches = list(batches(fragments(suggestion_rows, config), meta, limit))
    for batch in initial_batches:
        suggestions.extend(ask("suggestions", batch, meta, True)["suggestions"])
    if len(initial_batches) > 1 and suggestions:
        meta = {**meta, "stage": "合并"}
        while True:
            grouped = list(batches(suggestions, meta, limit))
            if len(grouped) == 1:
                suggestions = ask("suggestions", grouped[0], meta, True)["suggestions"]
                break
            reduced = []
            for batch in grouped:
                if len(batch) == 1:
                    reduced.extend(batch)
                else:
                    reduced.extend(ask("suggestions", batch, {**meta, "max_items": min(rules["max_suggestions"], max(1, len(batch) // 2))}, True)["suggestions"])
            if len(reduced) >= len(suggestions):
                raise ValueError("建议无法在长度限制内合并，请调整报告配置。")
            suggestions = reduced
            if not suggestions:
                break

    issues = {}
    for module in snapshot["modules"]:
        if not module["flagged"]:
            continue
        progress(stage="analysis", message=f"正在分析：{module['label']}", done=0, total=0)
        rows = snapshot["current"].loc[snapshot["current"]["最终标签"].eq(module["label"])].copy()
        rows["具体主题"] = rows["具体主题"].replace("", "未细分")
        issues[module["label"]] = []
        for theme in rows["具体主题"].value_counts().head(rules["max_issues_per_module"]).index:
            subset = rows.loc[rows["具体主题"].eq(theme)]
            items = list(fragments(subset, config))
            meta = {"stage": "反馈", "label": module["label"], "theme": theme, "max_evidence_ids": rules["max_evidence_ids"]}
            while True:
                parts = [ask("issues", batch, meta) for batch in batches(items, meta, limit)]
                if len(parts) == 1:
                    issues[module["label"]].append({"theme": theme, "count": len(subset), **parts[0]})
                    break
                if meta["stage"] == "合并" and len(parts) >= len(items):
                    raise ValueError("问题分析无法在长度限制内合并。")
                items, meta = parts, {**meta, "stage": "合并"}
    return {"suggestions": suggestions, "issues": issues}
