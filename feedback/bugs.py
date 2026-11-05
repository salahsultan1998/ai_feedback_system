"""Independent, evidence-linked suspected malfunction extraction for review."""
from .ai_client import cached_request, check_stop
from .analysis import batches, evidence, fragments
from .policies import abuse_mask, bug_mask
from .taxonomy import display_theme


def analyze_bugs(snapshot, config, cache_dir, progress, should_stop=None):
    """Scan valid current feedback whose original source label marks a BUG candidate."""
    limit = config["report"]["ai_max_input_chars"]
    current = snapshot["current"].fillna("").copy()
    # Statistics normally filters these rows already; retain the guard for callers
    # supplying an older or manually constructed snapshot.
    if "排除原因" not in current:
        current["排除原因"] = ""
    current = current.loc[~abuse_mask(current)]
    if "是否有效" in current:
        current = current.loc[current["是否有效"].eq("是")]
    if "反馈标签" not in current:
        current["反馈标签"] = ""
    current = current.loc[bug_mask(current, config)]
    if current.empty:
        progress(stage="analysis", message="本期没有符合原标签的 BUG 候选，已跳过提炼", done=0, total=0)
        return []
    contexts = {str(row["反馈ID"]): (row["最终标签"], row["具体主题"], row["三级主题"])
                for row in current.to_dict("records")}
    meta = {"task": "bugs", "stage": "提取"}

    def ask(batch, merging=False):
        check_stop(should_stop)
        candidates = {f"b{index}": item for index, item in enumerate(batch)} if merging else {}
        inputs = ([{"candidate_id": cid, **{key: item[key] for key in ("label", "categories", "text")}}
                   for cid, item in candidates.items()] if merging else batch)
        allowed = set(candidates) if merging else {item["feedback_id"] for item in batch}
        field = "candidate_ids" if merging else "feedback_ids"

        def sources(item):
            ids = (list(dict.fromkeys(fid for cid in item[field] for fid in candidates[cid]["feedback_ids"]))
                   if merging else item[field])
            paths = {contexts[fid] for fid in ids}
            if len(paths) != 1:
                raise ValueError("不同模块或分类路径的疑似 BUG 不可合并。")
            label, theme, subtheme = next(iter(paths))
            return {"text": item["text"], "label": label,
                    "categories": [display_theme(theme, subtheme) or "未细分"], "feedback_ids": ids}

        def validate(value):
            if not isinstance(value, dict) or not isinstance(value.get("bugs"), list):
                raise ValueError("AI 疑似 BUG 必须为列表。")
            checked = []
            for item in value["bugs"]:
                checked_item = evidence(item, allowed, config, 160, max_ids=len(allowed), id_field=field)
                sources(checked_item)
                checked.append(checked_item)
            if merging:
                used = [cid for item in checked for cid in item[field]]
                if len(used) != len(set(used)) or set(used) != allowed:
                    raise ValueError("疑似 BUG 合并必须完整保留每个候选，且不可重复引用。")
            return {"bugs": checked}

        payload = {**meta, "stage": "合并" if merging else "提取", "items": inputs}
        result = cached_request("bugs", payload, config, cache_dir, validate, should_stop)
        return [(sources(item), item[field]) for item in result["bugs"]]

    progress(stage="analysis", message="正在提取待核查的疑似 BUG", done=0, total=0)
    # Source-label candidacy is decided above; the model judges only the candidates.
    inputs = ({key: value for key, value in item.items() if key != "is_suggestion"}
              for item in fragments(current, config))
    candidates = []
    for batch in batches(inputs, meta, limit):
        candidates.extend(item for item, _ in ask(batch))

    def unique(items):
        result = {}
        for item in items:
            key = (contexts[item["feedback_ids"][0]], item["text"])
            if key not in result:
                result[key] = {**item, "feedback_ids": list(item["feedback_ids"])}
            else:
                result[key]["feedback_ids"] = list(dict.fromkeys(result[key]["feedback_ids"] + item["feedback_ids"]))
        return list(result.values())

    groups = {}
    for item in unique(candidates):
        path = contexts[item["feedback_ids"][0]]
        groups.setdefault(path, []).append(item)

    def compact(items):
        return [{"candidate_id": f"b{index}", **{key: item[key] for key in ("label", "categories", "text")}}
                for index, item in enumerate(items)]

    merge_meta = {**meta, "stage": "合并"}
    result = []
    for group in groups.values():
        if len(group) < 2:
            result.extend(group)
            continue
        compact_batches = list(batches(compact(group), merge_meta, limit))
        if len(compact_batches) == 1:
            result.extend(item for item, _ in ask(group, True))
            continue
        # First consolidate bounded blocks. Then compare each remaining candidate
        # against earlier ones in bounded batches. Distinct problems may stay
        # distinct forever; there is no forced reduction or top-N truncation.
        reduced, offset = [], 0
        for block in compact_batches:
            original = group[offset:offset + len(block)]
            if len(original) > 1:
                reduced.extend(item for item, _ in ask(original, True))
            else:
                reduced.extend(original)
            offset += len(block)
        consolidated = []
        for anchor in unique(reduced):
            pending, kept = consolidated, []
            while pending:
                check_stop(should_stop)
                packed = next(batches(compact([anchor, *pending]), merge_meta, limit))
                count = len(packed) - 1
                if count < 1:
                    raise ValueError("两条疑似 BUG 的分析数据超过长度限制。")
                merged = ask([anchor, *pending[:count]], True)
                anchor = next(item for item, refs in merged if "b0" in refs)
                kept.extend(item for item, refs in merged if "b0" not in refs)
                pending = pending[count:]
            consolidated = [*kept, anchor]
        result.extend(consolidated)
    return unique(result)
