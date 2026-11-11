"""Saved prompt text and its previous version, scoped to one game."""
from contextlib import closing
from hashlib import sha256
import sqlite3


class PromptConflict(ValueError):
    pass


def prompt_state(default, row=None):
    if row is None:
        digest = sha256(default.encode()).hexdigest()
        return {"version": 0, "revision": f"default-{digest}", "text": default, "previous": None}
    version, body, previous = row
    return {"version": version, "revision": str(version), "text": body, "previous": previous}


class PromptStore:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS prompts (
                game TEXT NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL,
                body TEXT NOT NULL, previous TEXT NOT NULL, PRIMARY KEY (game, name)
            )""")

    def read(self, game, defaults):
        with closing(sqlite3.connect(self.path)) as db:
            rows = {name: (version, body, previous) for name, version, body, previous in
                    db.execute("SELECT name, version, body, previous FROM prompts WHERE game = ?", (game,))}
        return {name: prompt_state(default, rows.get(name)) for name, default in defaults.items()}

    def overrides(self, game):
        with closing(sqlite3.connect(self.path)) as db:
            return dict(db.execute("SELECT name, body FROM prompts WHERE game = ?", (game,)))

    def save(self, game, name, default, revision, body):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version, body, previous FROM prompts WHERE game = ? AND name = ?",
                             (game, name)).fetchone()
            current = prompt_state(default, row)
            if current["revision"] != revision:
                raise PromptConflict("此提示词已有更新。请先重新读取，再保存你的修改。")
            if current["text"] == body:
                return current
            version = current["version"] + 1
            db.execute("""INSERT INTO prompts (game, name, version, body, previous) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (game, name) DO UPDATE SET
                version = excluded.version, body = excluded.body, previous = excluded.previous""",
                (game, name, version, body, current["text"]))
            return prompt_state(default, (version, body, current["text"]))
