"""Snapshot, version and push-lock state shared by both report editors."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .storage import atomic_json


MAX_TEXT_CHARS = 2000
PUSH_LOCK_REASON = "此报告已开始推送，建议已锁定，以保证所有推送分段内容一致。"


class ReviewConflict(ValueError):
    """The report changed since the editor loaded it."""


def plain_text(text):
    # Match the Markdown renderer's single-paragraph representation exactly.
    return text.replace("\r", " ").replace("\n", " ")


def report_version(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_snapshot(folder):
    path = folder / "review.json"
    if not path.exists() or not (folder / "report.original.md").exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or not isinstance(data.get("suggestions"), list) or not isinstance(data.get("sources"), dict):
            raise ValueError
        return data
    except (OSError, json.JSONDecodeError, ValueError, AttributeError):
        raise ValueError("建议原文快照无法读取，请重新生成报告后编辑。") from None


def lock_reason(folder):
    # Read the physical checkpoint: feishu.state may present an older sent report as ready.
    if (folder / "review.lock.json").exists() or (folder / "feishu_push.json").exists():
        return PUSH_LOCK_REASON
    return ""


def mark_push_started(folder):
    """Freeze edits before detail sync or the first outgoing message starts."""
    folder = Path(folder)
    path = folder / "review.lock.json"
    if not path.exists():
        atomic_json(path, {"started_at": datetime.now(timezone.utc).isoformat()})
