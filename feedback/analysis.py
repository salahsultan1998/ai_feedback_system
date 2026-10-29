"""Evidence-linked suggestions and issue descriptions from current-period rows."""
import json

from .ai_client import cached_request, check_stop
from .report_language import validate_report_language
from .policies import abuse_mask, is_suggestion_label, suggestion_mask
from .taxonomy import display_theme


def evidence(item, allowed, config, max_chars, max_ids=None, id_field="feedback_ids"):
    if not isinstance(item, dict) or not isinstance(item.get("text"), str):
        raise ValueError("AI 分析缺少文字。")
    text, ids = item["text"].strip(), item.get(id_field)
    maximum = config["report"]["max_evidence_ids"] if max_ids is None else max_ids
    if not text or len(text) > max_chars or not isinstance(ids, list) or not 1 <= len(ids) <= maximum:
        raise ValueError("AI 分析文字长度或引用数量无效。")
    validate_report_language(text)
    if any(not isinstance(fid, str) or fid not in allowed for fid in ids) or len(set(ids)) != len(ids):
        raise ValueError("AI 分析引用了未知或重复的反馈 ID。")
    return {"text": text, id_field: ids}


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
                   "theme": row["具体主题"] or "未细分", "subtheme": row["三级主题"],
                   "is_suggestion": is_suggestion_label(row["反馈标签"], config),
                   "text": row["中文翻译"][offset:offset + size]}


def analyze_report(snapshot, config, cache_dir, progress, should_stop=None):
    rules = config["report"]
    limit = rules["ai_max_input_chars"]
    current = snapshot["current"].loc[~abuse_mask(snapshot["current"])]
    suggestion_rows = current.loc[suggestion_mask(current, config)]
    source_labels = suggestion_rows.set_index("反馈ID")["最终标签"].to_dict()
    source_categories = {row["反馈ID"]: display_theme(row["具体主题"], row["三级主题"]) or "未细分"
                         for row in suggestion_rows.to_dict("records")}

    def ask(name, batch, metadata, suggestions=False):
        check_stop(should_stop)
        merging = suggestions and metadata["stage"] == "合并"
        candidates = {item["candidate_id"]: item for item in batch} if merging else {}
        allowed = set()
        for item in batch:
            allowed.update([item["candidate_id"]] if merging else item.get("feedback_ids", [item.get("feedback_id")]))

        def suggestion_sources(item):
            if merging:
                selected = [candidates[cid] for cid in item["candidate_ids"]]
                labels = {candidate["label"] for candidate in selected}
                ids = list(dict.fromkeys(fid for candidate in selected for fid in candidate["feedback_ids"]))
            else:
                ids = item["feedback_ids"]
                labels = {source_labels[fid] for fid in ids}
            if len(labels) != 1:
                raise ValueError("不同模块的建议不可合并为同一条。")
            return {"text": item["text"], "label": next(iter(labels)), "feedback_ids": ids,
                    "categories": sorted({source_categories[fid] for fid in ids})}

        def validate(value):
            if not suggestions:
                return evidence(value, allowed, config, 160)
            if not isinstance(value, dict) or not isinstance(value.get("suggestions"), list):
                raise ValueError("AI 建议必须为列表。")
            if len(value["suggestions"]) > metadata["max_items"]:
                raise ValueError("AI 建议超过数量限制。")
            checked = []
            for item in value["suggestions"]:
                item = evidence(item, allowed, config, 140, max_ids=len(allowed),
                                id_field="candidate_ids" if merging else "feedback_ids")
                suggestion_sources(item)
                checked.append(item)
            # Keep wire references in cache, so retries can validate the same shape.
            return {"suggestions": checked}

        inputs = batch
        if merging:
            # Source lists can grow large. Compact references let the backend
            # preserve every contributor without sending them through each merge.
            inputs = [{key: item[key] for key in ("candidate_id", "label", "categories", "text")} for item in batch]
        result = cached_request(name, {**metadata, "items": inputs}, config, cache_dir, validate, should_stop)
        if suggestions:
            return {"suggestions": [suggestion_sources(item) for item in result["suggestions"]]}
        return result

    progress(stage="analysis", message="正在提炼玩家建议", done=0, total=0)
    meta = {"stage": "提取", "max_items": rules["max_suggestions"]}
    suggestions = []
    initial_batches = list(batches(fragments(suggestion_rows, config), meta, limit))
    for batch in initial_batches:
        suggestions.extend(ask("suggestions", batch, meta, True)["suggestions"])
    if len(suggestions) > 1:
        meta = {**meta, "stage": "合并"}
        while True:
            candidates = {f"s{index}": item for index, item in enumerate(suggestions)}
            compact = [{"candidate_id": cid, "label": item["label"], "categories": item["categories"], "text": item["text"]}
                       for cid, item in candidates.items()]
            grouped = list(batches(compact, meta, limit))

            def merge(batch, options):
                expanded = [{**item, "feedback_ids": candidates[item["candidate_id"]]["feedback_ids"]} for item in batch]
                return ask("suggestions", expanded, options, True)["suggestions"]

            if len(grouped) == 1:
                suggestions = merge(grouped[0], meta)
                break
            reduced = []
            for batch in grouped:
                if len(batch) == 1:
                    reduced.append(candidates[batch[0]["candidate_id"]])
                else:
                    reduced.extend(merge(batch, {**meta, "max_items": min(rules["max_suggestions"], max(1, len(batch) // 2))}))
            if len(reduced) >= len(suggestions):
                raise ValueError("建议无法在长度限制内合并，请调整报告配置。")
            suggestions = reduced
            if not suggestions:
                break

    # Do not rely on the model to remove literally identical final entries.
    unique = {}
    for item in suggestions:
        key = (item["label"], tuple(item["categories"]), item["text"])
        if key not in unique:
            unique[key] = {**item, "feedback_ids": list(item["feedback_ids"])}
        else:
            unique[key]["feedback_ids"] = list(dict.fromkeys(unique[key]["feedback_ids"] + item["feedback_ids"]))
    suggestions = list(unique.values())

    issues = {}
    for module in snapshot["modules"]:
        if not module["flagged"]:
            continue
        progress(stage="analysis", message=f"正在分析：{module['label']}", done=0, total=0)
        rows = current.loc[current["最终标签"].eq(module["label"])].copy()
        rows["分析主题"] = [display_theme(theme, subtheme) or "未细分"
                            for theme, subtheme in zip(rows["具体主题"], rows["三级主题"])]
        issues[module["label"]] = []
        for theme in rows["分析主题"].value_counts().head(rules["max_issues_per_module"]).index:
            subset = rows.loc[rows["分析主题"].eq(theme)]
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
