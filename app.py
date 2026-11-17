"""Run the local feedback app with: .venv/bin/python app.py"""
import os
from pathlib import Path
import secrets
import threading

from dotenv import load_dotenv
from flask import Flask, jsonify, request, url_for

from feedback.config import ROOT, load_settings
from feedback.storage import Storage
from feedback.jobs import Jobs
from feedback.prompt_store import PromptStore
from feedback.runtime import configure_runtime
from feedback.workspaces import Workspaces
from web.pages import pages
from web.api import api
from web.auth import auth_settings, install_auth


def create_app(test_config=None):
    load_dotenv(ROOT / ".env")
    app = Flask(__name__)
    settings = load_settings()
    app.config.update(SECRET_KEY=secrets.token_hex(32), PROJECT_ROOT=ROOT,
        STORAGE_ROOT=ROOT / "storage", MAX_CONTENT_LENGTH=settings["max_upload_mb"] * 1024 ** 2,
        SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_NAME="feedback_session",
        TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"])
    if test_config:
        app.config.update(test_config)
    identity = auth_settings()
    configure_runtime(app)
    storage = Storage(Path(app.config["STORAGE_ROOT"]))
    app.extensions.update(storage=storage, jobs=Jobs(storage, app.logger), feishu_lock=threading.Lock())
    app.extensions["prompts"] = PromptStore(storage.root / "prompts.sqlite3")
    workspaces = Workspaces(storage, app.extensions["jobs"], app.logger)
    app.extensions["workspaces"] = workspaces
    if not app.testing:
        workspaces.start_cleaner()
    app.register_blueprint(pages)
    app.register_blueprint(api)
    install_auth(app, identity)

    @app.template_global()
    def icon_url(name):
        filename = f"images/{name}"
        try:
            version = (Path(app.static_folder) / filename).stat().st_mtime_ns
        except FileNotFoundError:
            version = None
        return url_for("static", filename=filename, v=version)

    @app.get("/healthz")
    def health():
        return jsonify(status="ok")

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(error=f"上传文件不可超过 {settings['max_upload_mb']} MB。"), 413

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        if (request.endpoint == "static" and request.view_args["filename"].startswith("images/")
                and request.args.get("v") and response.status_code in {200, 304}):
            response.headers["Cache-Control"] = "public, max-age=604800, immutable"
        elif response.mimetype in {"application/json", "text/html"} or "Content-Disposition" in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response
    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.getenv("PORT", "5050")), debug=False)
