"""Review suspected BUG descriptions and their immutable feedback references."""
import html
from pathlib import Path
import re

from .report_language import validate_report_language
from .review_state import (MAX_TEXT_CHARS, ReviewConflict, load_snapshot, lock_reason,
                           plain_text, report_version)


HEADING = "## BUG 反馈"
EMPTY_TEXT = "本期无 BUG反馈。"
UNAVAILABLE_REASON = "此报告尚未生成BUG 清单，请重新生成报告。"


def _categories(value):
    if isinstance(value, str):
        return [value] if value else []
    return list(value or [])


def snapshot_entries(bugs):
    return [{"id": f"b{index}", "code": f"BUG-{index + 1:03d}", "label": item["label"],
             "categories": _categories(item.get("categories")),
             "text": plain_text(item["text"]),
             "feedback_ids": list(dict.fromkeys(item["feedback_ids"]))}
            for index, item in enumerate(bugs)]


def render_section(bugs):
    """Render stable BUG codes; callers pass only entries included in the report."""
    from .exports.markdown import safe

    lines = [HEADING, ""]
    if not bugs:
        return "\n".join(lines + [EMPTY_TEXT, "", ""])
    references = {fid for item in bugs for fid in item["feedback_ids"]}
    lines += [f"共整理 **{len(bugs)} 个疑似问题，涉及 {len(references)} 条反馈**。以下为玩家描述，尚需进一步核实。", ""]
    for index, item in enumerate(bugs, 1):
        code = item.get("code", f"BUG-{index:03d}")
        categories = "、".join(_categories(item.get("categories"))) or "未细分"
        ids = list(dict.fromkeys(item["feedback_ids"]))
        lines += [f"{index}. **【{safe(code)} · {safe(item['label'])} · {safe(categories)}】** "
                  f"{safe(item['text'])}（相关反馈：{len(ids)} 条；反馈 ID：{safe('、'.join(ids))}）", ""]
    return "\n".join(lines) + "\n"


def _parts(text):
    start = re.search(r"(?m)^" + re.escape(HEADING) + r"\n", text)
    if not start:
        return None
    end = re.search(r"(?m)^## ", text[start.end():])
    offset = start.end() + end.start() if end else len(text)
    return text[:start.start()], text[start.start():offset], text[offset:]


def _active(section, originals):
    """Read current edits from Markdown, anchored by immutable BUG codes and references."""
    from .exports.markdown import safe

    by_code = {item["code"]: item for item in originals}
    lines = [line for line in section.splitlines() if line.strip()]
    if lines == [HEADING, EMPTY_TEXT]:
        return {}
    if len(lines) < 3 or lines[0] != HEADING:
        raise ValueError(" BUG 清单结构已变化，请重新生成报告后编辑。")
    active = {}
    for index, line in enumerate(lines[2:], 1):
        match = re.match(r"\d+\. \*\*【(BUG-\d+) · ", line)
        code = match[1] if match else None
        if code not in by_code or code in active:
            raise ValueError(" BUG 编号已变化，请重新打开核查界面。")
        original = by_code[code]
        categories = "、".join(original["categories"]) or "未细分"
        prefix = f"{index}. **【{safe(code)} · {safe(original['label'])} · {safe(categories)}】** "
        match = re.fullmatch(r"(.*)（相关反馈：([1-9]\d*) 条；反馈 ID：(.+)）", line[len(prefix):]) if line.startswith(prefix) else None
        if not match:
            raise ValueError("BUG 内容或引用结构已变化，请重新生成报告。")
        ids = html.unescape(match[3]).split("、")
        if (len(ids) != int(match[2]) or len(ids) != len(set(ids)) or
                any(fid not in original["feedback_ids"] for fid in ids)):
            raise ValueError("BUG 引用了不属于该问题的反馈。")
        active[code] = {**original, "text": html.unescape(match[1]), "feedback_ids": ids}
    if section.strip() != render_section(list(active.values())).strip():
        raise ValueError("BUG 清单格式或汇总数量已变化，请重新生成报告。")
    return active


def _read(folder, text=None, include_sources=True):
    folder = Path(folder)
    text = (folder / "report.md").read_text(encoding="utf-8") if text is None else text
    data = load_snapshot(folder)
    parts = _parts(text)
    available = data is not None and "bugs" in data and parts is not None
    result = {"available": available, "editable": False, "locked_reason": UNAVAILABLE_REASON,
              "version": report_version(text), "modified": False}
    if not available:
        if include_sources:
            result["bugs"] = []
        return result
    originals = data["bugs"]
    active = _active(parts[1], originals)
    reason = lock_reason(folder) or ("本期没有可核查的 BUG。" if not originals else "")
    result.update(editable=not reason, locked_reason=reason,
                  modified=list(active) != [item["code"] for item in originals] or any(
                      active[item["code"]]["text"] != item["text"] or
                      active[item["code"]]["feedback_ids"] != item["feedback_ids"]
                      for item in originals if item["code"] in active))
    if include_sources:
        result["bugs"] = [{"id": item["id"], "code": item["code"], "label": item["label"],
                           "categories": list(item["categories"]), "text": active.get(item["code"], item)["text"],
                           "original_text": item["text"], "included": item["code"] in active,
                           "feedback_ids": list(active.get(item["code"], item)["feedback_ids"]),
                           "original_feedback_ids": list(item["feedback_ids"]),
                           "sources": [dict(data["sources"][fid]) for fid in item["feedback_ids"]]}
                          for item in originals]
    return result


def details(folder):
    return _read(folder)


def summary(folder, text=None):
    return _read(folder, text=text, include_sources=False)


def save(folder, version, bugs):
    folder = Path(folder)
    current = (folder / "report.md").read_text(encoding="utf-8")
    state = _read(folder, text=current)
    if not state["editable"]:
        raise ValueError(state["locked_reason"])
    if not isinstance(version, str) or version != state["version"]:
        raise ReviewConflict("报告已更新，请重新打开 BUG 核查界面后再保存。")
    originals = load_snapshot(folder)["bugs"]
    if not isinstance(bugs, list) or len(bugs) != len(originals):
        raise ValueError("只能核查现有 BUG，不能新增、删除或更换问题编号。")
    included = []
    for item, original in zip(bugs, originals):
        if (not isinstance(item, dict) or set(item) != {"id", "text", "included", "feedback_ids"} or
                item["id"] != original["id"] or type(item["included"]) is not bool):
            raise ValueError("BUG 编号或核查格式无效，请重新打开核查界面。")
        value, ids = item["text"], item["feedback_ids"]
        if not isinstance(value, str) or item["included"] and (not value.strip() or len(value) > MAX_TEXT_CHARS):
            raise ValueError(f"每条 BUG 描述须填写 1–{MAX_TEXT_CHARS} 个字符。")
        if item["included"] and any(ord(char) < 32 and char not in "\r\n\t" for char in value):
            raise ValueError("BUG 描述含不可显示的控制字符，请删除后再保存。")
        if (not isinstance(ids, list) or any(not isinstance(fid, str) or fid not in original["feedback_ids"] for fid in ids) or
                len(ids) != len(set(ids)) or item["included"] and not ids):
            raise ValueError("保留的 BUG 至少需要一条原始引用，且不能添加、重复或更换来源反馈。")
        if item["included"]:
            included.append({**original, "text": plain_text(value.strip()),
                             "feedback_ids": [fid for fid in original["feedback_ids"] if fid in ids]})
    prefix, section, suffix = _parts(current)
    updated_section = render_section(included)
    if section != updated_section:
        updated = validate_report_language(prefix + updated_section + suffix)
        temporary = folder / "report.bugs.tmp.md"
        temporary.write_text(updated, encoding="utf-8")
        temporary.replace(folder / "report.md")
    return details(folder)
