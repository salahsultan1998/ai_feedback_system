"""Each upload and report has a unique identifier and private storage folder."""
import json
from pathlib import Path
import re
import uuid

import pandas as pd


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.json")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def atomic_csv(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.csv")
    data.to_csv(temporary, index=False, encoding="utf-8-sig")
    temporary.replace(path)


def read_csv(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")


class Storage:
    def __init__(self, root):
        self.root = Path(root)

    def folder(self, game, run):
        if not re.fullmatch(r"[a-z0-9_-]+", game) or not re.fullmatch(r"[0-9a-f]{32}", run):
            raise ValueError("文件记录不存在。")
        return self.root / game / run

    def create(self, game):
        run = uuid.uuid4().hex
        folder = self.folder(game, run)
        folder.mkdir(parents=True)
        return run, folder

    def metadata(self, game, run):
        path = self.folder(game, run) / "upload.json"
        if not path.exists():
            raise ValueError("上传记录不存在，请重新上传。")
        return json.loads(path.read_text(encoding="utf-8"))
