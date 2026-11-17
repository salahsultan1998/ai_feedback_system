"""Persistent project configuration and prompt overrides."""
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import sqlite3
from time import time


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
            db.execute("""CREATE TABLE IF NOT EXISTS game_config_versions (
                game TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
                saved_at TEXT, editor TEXT, PRIMARY KEY (game, version)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS game_config_unlocks (
                owner TEXT NOT NULL, game TEXT NOT NULL, expires_at REAL NOT NULL,
                PRIMARY KEY (owner, game)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS game_config_attempts (
                owner TEXT NOT NULL, game TEXT NOT NULL, failures INTEGER NOT NULL, expires_at REAL NOT NULL,
                PRIMARY KEY (owner, game)
            )""")

    def read(self, game, defaults):
        with closing(sqlite3.connect(self.path)) as db:
            rows = {name: (version, body, previous) for name, version, body, previous in
                    db.execute("SELECT name, version, body, previous FROM prompts WHERE game = ?", (game,))}
        return {name: prompt_state(default, rows.get(name)) for name, default in defaults.items()}

    def runtime_overrides(self, game, with_prompts=False):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN")
            row = db.execute("SELECT body FROM game_config_versions WHERE game = ? ORDER BY version DESC LIMIT 1",
                             (game,)).fetchone()
            prompts = dict(db.execute("SELECT name, body FROM prompts WHERE game = ?", (game,))) if with_prompts else None
            return json.loads(row[0]) if row else None, prompts

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

    @staticmethod
    def _config_state(db, game, default):
        history = [{"version": version, "revision": str(version), "saved_at": saved_at, "editor": editor}
                   for version, saved_at, editor in db.execute(
                       "SELECT version, saved_at, editor FROM game_config_versions WHERE game = ? ORDER BY version DESC",
                       (game,))]
        if not history:
            revision = "default-" + sha256(json.dumps(default, ensure_ascii=False).encode()).hexdigest()
            history = [{"version": 0, "revision": revision, "saved_at": None, "editor": None}]
            value = default
        else:
            value, = db.execute("SELECT body FROM game_config_versions WHERE game = ? AND version = ?",
                                (game, history[0]["version"])).fetchone()
            value = json.loads(value)
        return {**history[0], "value": value, "history": history}

    def read_config(self, game, default):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN")
            return self._config_state(db, game, default)

    def config_version(self, game, default, version):
        if not 0 <= version < 2 ** 63:
            raise ValueError("请选择有效的配置版本。")
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT body FROM game_config_versions WHERE game = ? AND version = ?",
                             (game, version)).fetchone()
        if row:
            return json.loads(row[0])
        if version == 0:
            return default
        raise ValueError("这个配置版本不存在。")

    def save_config(self, game, default, revision, value, editor=None):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = self._config_state(db, game, default)
            if current["revision"] != revision:
                raise PromptConflict("项目配置已被修改，请重新读取后再保存。")
            body = json.dumps(value, ensure_ascii=False, allow_nan=False)
            if json.dumps(current["value"], ensure_ascii=False, allow_nan=False) == body:
                return current
            if current["version"] == 0:
                db.execute("INSERT INTO game_config_versions VALUES (?, 0, ?, NULL, NULL)",
                           (game, json.dumps(default, ensure_ascii=False)))
            db.execute("INSERT INTO game_config_versions VALUES (?, ?, ?, ?, ?)",
                       (game, current["version"] + 1, body, datetime.now(timezone.utc).isoformat(), editor))
            return self._config_state(db, game, default)

    def unlock(self, owner, game, valid, throttle_owner):
        """Limit each account (or local session) to five failures per five minutes."""
        now = time()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM game_config_attempts WHERE expires_at <= ?", (now,))
            row = db.execute("SELECT failures, expires_at FROM game_config_attempts WHERE owner = ? AND game = ?",
                             (throttle_owner, game)).fetchone()
            if row and row[0] >= 5:
                return None, math.ceil(row[1] - now)
            if valid:
                db.execute("DELETE FROM game_config_attempts WHERE owner = ? AND game = ?", (throttle_owner, game))
                db.execute("DELETE FROM game_config_unlocks WHERE expires_at <= ?", (now,))
                expires = now + 30 * 60
                db.execute("INSERT OR REPLACE INTO game_config_unlocks VALUES (?, ?, ?)", (owner, game, expires))
                return expires, 0
            elif row:
                db.execute("UPDATE game_config_attempts SET failures = failures + 1 WHERE owner = ? AND game = ?",
                           (throttle_owner, game))
            else:
                db.execute("INSERT INTO game_config_attempts VALUES (?, ?, 1, ?)", (throttle_owner, game, now + 300))
        return None, 0

    def unlock_expiry(self, owner, game):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT expires_at FROM game_config_unlocks WHERE owner = ? AND game = ? AND expires_at > ?",
                             (owner, game, time())).fetchone()
        return row[0] if row else None

    def lock(self, owner, game):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM game_config_unlocks WHERE owner = ? AND game = ?", (owner, game))
