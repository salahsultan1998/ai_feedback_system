"""Review report suggestions against immutable, report-specific source feedback."""
import html
from pathlib import Path
import re
from zipfile import BadZipFile

from openpyxl.utils.exceptions import InvalidFileException

from .exports.markdown import safe
from .report_language import validate_report_language
from .review_state import (MAX_TEXT_CHARS, ReviewConflict, load_snapshot, lock_reason,
                           plain_text, report_version)
from .storage import atomic_json


UNAVAILABLE_REASON = "此报告尚未保存建议原文，请重新生成报告后编辑。"
REMOVED_TEXT = "本期建议已全部移除。"


def _parts(text):
    start = re.search(r"(?m)^## 有价值的玩家建议\n", text)
    end = re.search(r"(?m)^## ", text[start.end():]) if start else None
    if not start or not end:
        raise ValueError("报告建议区结构已变化，无法安全修改。请重新生成报告。")
    end_offset = start.end() + end.start()
    return text[:start.end()], text[start.end():end_offset], text[end_offset:]


def _active(text, suggestions):
    _, body, _ = _parts(text)
    lines = body.strip().splitlines()
    by_id = {item["id"]: item for item in suggestions}
    ids = list(by_id)
    manifest = re.fullmatch(r"<!-- suggestion-ids: (.*?) -->", lines[0]) if lines else None
    if manifest:
        ids = manifest[1].split(",") if manifest[1] else []
        if ids != [item["id"] for item in suggestions if item["id"] in ids]:
            raise ValueError("报告建议编号已变化，请重新打开编辑界面。")
        if len(lines) < 2 or lines[1] != "":
            raise ValueError("报告建议区格式已变化，无法安全修改。请重新生成报告。")
        lines = lines[2:]
        if not ids:
            if lines != [REMOVED_TEXT]:
                raise ValueError("报告建议区格式已变化，无法安全修改。请重新生成报告。")
            return {}
    elif not suggestions:
        return {}
    if len(lines) != len(ids):
        raise ValueError("报告建议数量已变化，无法安全修改。请重新生成报告。")
    active = {}
    for index, (line, sid) in enumerate(zip(lines, ids), 1):
        item = by_id[sid]
        prefix = f"{index}. "
        suffix = f"（反馈 ID：{safe('、'.join(item['feedback_ids']))}）"
        if not line.startswith(prefix) or not line.endswith(suffix):
            raise ValueError("报告建议引用已变化，无法安全修改。请重新生成报告。")
        active[sid] = html.unescape(line[len(prefix):-len(suffix)])
    return active


def initialize(folder, snapshot, analysis, markdown):
    """Freeze evidence and the original report while this job is being exported."""
    folder = Path(folder)
    if (folder / "review.json").exists() or (folder / "report.original.md").exists():
        raise ValueError("此报告的原始建议已保存，不能覆盖。")
    suggestions = []
    rows = ({str(row["反馈ID"]): row for row in snapshot["current"].fillna("").to_dict("records")}
            if analysis["suggestions"] or analysis.get("bugs") else {})
    for index, item in enumerate(analysis["suggestions"]):
        ids = list(dict.fromkeys(item["feedback_ids"]))
        if not ids or any(not isinstance(fid, str) or fid not in rows for fid in ids):
            raise ValueError("建议引用了不属于本期有效反馈的记录。")
        suggestions.append({"id": f"s{index}", "text": plain_text(item["text"]), "feedback_ids": ids})
    if _active(markdown, suggestions) != {item["id"]: item["text"] for item in suggestions}:
        raise ValueError("报告建议与原文快照不一致，无法保存人工核查资料。")
    data = {"schema_version": 1, "suggestions": suggestions}
    if "bugs" in analysis:
        from . import bug_review

        bugs = bug_review.snapshot_entries(analysis["bugs"])
        for item in bugs:
            ids = item["feedback_ids"]
            if not ids or any(not isinstance(fid, str) or fid not in rows for fid in ids):
                raise ValueError("疑似 BUG 引用了不属于本期有效反馈的记录。")
        parts = bug_review._parts(markdown)
        if parts is None or bug_review._active(parts[1], bugs) != {item["code"]: item for item in bugs}:
            raise ValueError("报告 BUG 清单与原文快照不一致，无法保存核查资料。")
        data["bugs"] = bugs
    references = dict.fromkeys(fid for item in [*suggestions, *data.get("bugs", [])] for fid in item["feedback_ids"])
    data["sources"] = {fid: {"feedback_id": fid, "original_text": str(rows[fid]["用户自己填写的内容"]),
                             "translation_zh": str(rows[fid]["中文翻译"])} for fid in references}
    folder.mkdir(parents=True, exist_ok=True)
    # The original is never changed by later manual saves or restores.
    (folder / "report.original.md").write_text(markdown, encoding="utf-8")
    atomic_json(folder / "review.json", data)


def prepare_existing(folder):
    """Recover an unpushed report's evidence from its completed Excel, without AI calls."""
    folder = Path(folder)
    if any((folder / name).exists() for name in
           ("review.json", "report.original.md", "feishu_push.json", "review.lock.json")):
        return False
    try:
        markdown = (folder / "report.md").read_text(encoding="utf-8")
        _, body, _ = _parts(markdown)
        suggestions, references = [], set()
        if body.strip() != "本期未发现明确、具体的产品改进建议。":
            for index, line in enumerate(body.strip().splitlines(), 1):
                match = re.fullmatch(r"(\d+)\. (.+)（反馈 ID：(.+)）", line)
                if not match or match[1] != str(index):
                    return False
                ids = html.unescape(match[3]).split("、")
                if any(not fid or fid != fid.strip() for fid in ids) or len(set(ids)) != len(ids):
                    return False
                text = html.unescape(match[2])
                if line != f"{index}. {safe(text)}（反馈 ID：{safe('、'.join(ids))}）":
                    return False
                suggestions.append({"text": text, "feedback_ids": ids})
                references.update(ids)
            if not suggestions:
                return False
        if not suggestions:
            initialize(folder, {}, {"suggestions": []}, markdown)
            return True
        from openpyxl import load_workbook
        import pandas as pd

        book = load_workbook(folder / "feedback_details.xlsx", read_only=True, data_only=False, keep_links=False)
        try:
            if "本期反馈明细" not in book.sheetnames:
                return False
            rows = book["本期反馈明细"].iter_rows(min_row=4, values_only=True)
            headers = next(rows, ())
            required = ("反馈ID", "原文", "中文翻译")
            if any(headers.count(name) != 1 for name in required):
                return False
            columns = [headers.index(name) for name in required]
            sources = {}
            for row in rows:
                fid, original, translation = (row[column] if column < len(row) else None for column in columns)
                if not isinstance(fid, str) or fid not in references:
                    continue
                if fid in sources or any(not isinstance(value, str) or not value.strip() for value in (original, translation)):
                    return False
                sources[fid] = {"反馈ID": fid, "用户自己填写的内容": original, "中文翻译": translation}
            if set(sources) != references:
                return False
        finally:
            book.close()
        snapshot = {"current": pd.DataFrame(sources.values())}
        initialize(folder, snapshot, {"suggestions": suggestions}, markdown)
        return True
    except (OSError, ValueError, KeyError, TypeError, SyntaxError, BadZipFile, InvalidFileException):
        # An unsupported old layout keeps the original report fully usable.
        return False


def _read(folder, text=None, include_sources=True):
    folder = Path(folder)
    text = (folder / "report.md").read_text(encoding="utf-8") if text is None else text
    data = load_snapshot(folder)
    base = {"available": data is not None, "editable": False, "locked_reason": UNAVAILABLE_REASON,
            "version": report_version(text), "modified": False}
    if data is None:
        if include_sources:
            base["suggestions"] = []
        return base
    suggestions = data["suggestions"]
    active = _active(text, suggestions)
    reason = lock_reason(folder) or ("本期没有可编辑的建议。" if not suggestions else "")
    base.update(editable=not reason, locked_reason=reason,
                modified=active != {item["id"]: item["text"] for item in suggestions})
    if include_sources:
        base["suggestions"] = [{"id": item["id"], "text": active.get(item["id"], item["text"]),
                                "original_text": item["text"], "included": item["id"] in active,
                                "feedback_ids": list(item["feedback_ids"]),
                                "sources": [dict(data["sources"][fid]) for fid in item["feedback_ids"]]}
                               for item in suggestions]
    return base


def details(folder):
    return _read(folder)


def summary(folder, text=None):
    return _read(folder, text=text, include_sources=False)


def save(folder, version, texts):
    """Review wording and inclusion; IDs, evidence and other report sections stay fixed."""
    folder = Path(folder)
    current = (folder / "report.md").read_text(encoding="utf-8")
    state = _read(folder, text=current)
    if not state["editable"]:
        raise ValueError(state["locked_reason"])
    if not isinstance(version, str) or version != state["version"]:
        raise ReviewConflict("报告已更新，请重新打开编辑界面后再保存，避免覆盖其他修改。")
    data = load_snapshot(folder)
    suggestions = data["suggestions"]
    if not isinstance(texts, list) or len(texts) != len(suggestions):
        raise ValueError("只能核查现有建议，请保留所有编号并使用保留选项移除建议。")
    values = {}
    for item, original in zip(texts, suggestions):
        if (not isinstance(item, dict) or set(item) not in ({"id", "text"}, {"id", "text", "included"}) or
                item["id"] != original["id"] or type(item.get("included", True)) is not bool):
            raise ValueError("建议编号或引用不可修改，请重新打开编辑界面。")
        included = item.get("included", True)
        value = item["text"]
        if not isinstance(value, str) or included and (not value.strip() or len(value) > MAX_TEXT_CHARS):
            raise ValueError(f"每条建议须填写 1–{MAX_TEXT_CHARS} 个字符。")
        if included and any(ord(char) < 32 and char not in "\r\n\t" for char in value):
            raise ValueError("建议文案含不可显示的控制字符，请删除后再保存。")
        if included:
            values[original["id"]] = plain_text(value.strip())
    if values != {item["id"]: item["text"] for item in state["suggestions"] if item["included"]}:
        prefix, _, suffix = _parts(current)
        kept = [item for item in suggestions if item["id"] in values]
        lines = [f"{index}. {safe(values[item['id']])}（反馈 ID：{safe('、'.join(item['feedback_ids']))}）"
                 for index, item in enumerate(kept, 1)]
        if len(kept) != len(suggestions):
            lines = [f"<!-- suggestion-ids: {','.join(values)} -->", "", *(lines or [REMOVED_TEXT])]
        updated = validate_report_language(prefix + "\n" + "\n".join(lines) + "\n\n" + suffix)
        temporary = folder / "report.review.tmp.md"
        temporary.write_text(updated, encoding="utf-8")
        temporary.replace(folder / "report.md")
    return details(folder)
