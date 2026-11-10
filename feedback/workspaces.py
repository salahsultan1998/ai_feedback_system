"""Temporary page workspaces; cleanup waits for requests and report workers."""
import json
import secrets
import shutil
import threading
import time

from werkzeug.exceptions import Gone, NotFound

from .storage import atomic_json


class Workspaces:
    idle_seconds = 30 * 60

    def __init__(self, storage, jobs, logger):
        self.storage, self.jobs, self.logger = storage, jobs, logger
        self.lock = threading.RLock()
        self.pages = {}
        # Only folders explicitly created as temporary workspaces are disposable.
        for marker in storage.root.glob("*/*/workspace.json"):
            if marker.is_symlink() or marker.parent.is_symlink():
                continue
            if json.loads(marker.read_text()) == {"kind": "temporary-page-v1"}:
                self.remove(marker.parent.parent.name, marker.parent.name)

    def create(self, owner):
        with self.lock:
            token = secrets.token_urlsafe(32)
            self.pages[token] = dict(owner=owner, touched=time.monotonic(), closed=False, users=0, runs=set())
            return token

    def acquire(self, token, owner, game=None, run=None, closing=False):
        with self.lock:
            page = self.pages.get(token)
            if not page or not owner or page["owner"] != owner:
                raise Gone("此页面已过期，请刷新后重新上传文件。")
            if page["closed"] and not closing:
                raise Gone("此页面已关闭，请重新上传文件。")
            if run and (game, run) not in page["runs"]:
                raise NotFound("文件不属于此页面。")
            page["users"] += 1
            page["touched"] = time.monotonic()

    def upload(self, token, game):
        with self.lock:
            page = self.pages[token]
            if page["closed"]:
                raise Gone("此页面已关闭，请重新上传文件。")
            run, folder = self.storage.create(game)
            page["runs"].add((game, run))
            atomic_json(folder / "workspace.json", {"kind": "temporary-page-v1"})
            return run, folder

    def close(self, token):
        with self.lock:
            page = self.pages[token]
            page["closed"] = True
            active = self.jobs.current()
            if active and (active["game"], active["run"]) in page["runs"]:
                self.jobs.stop(active["game"], active["run"], active["id"])

    def start_report(self, token, game, run, config, options):
        with self.lock:
            if self.pages[token]["closed"]:
                raise Gone("此页面已关闭，请重新上传文件。")
            return self.jobs.start(game, run, config, options)

    def release(self, token):
        with self.lock:
            self.pages[token]["users"] -= 1
        self.sweep()

    def remove(self, game, run):
        folder = self.storage.folder(game, run)
        if folder.exists() and not folder.is_symlink() and not folder.parent.is_symlink():
            shutil.rmtree(folder)

    def sweep(self):
        with self.lock:
            now = time.monotonic()
            for token, page in list(self.pages.items()):
                if now - page["touched"] >= self.idle_seconds:
                    self.close(token)
                if not page["closed"] or page["users"]:
                    continue
                active = self.jobs.current()
                if active and (active["game"], active["run"]) in page["runs"]:
                    continue
                for game, run in page["runs"]:
                    self.remove(game, run)
                del self.pages[token]

    def start_cleaner(self):
        def clean():
            while True:
                time.sleep(15)
                try:
                    self.sweep()
                except Exception:
                    self.logger.exception("Temporary workspace cleanup failed")
        threading.Thread(target=clean, daemon=True, name="workspace-cleanup").start()
