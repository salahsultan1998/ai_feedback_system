"""Run the local feedback app with: .venv/bin/python app.py"""
import os
from pathlib import Path
import secrets

from dotenv import load_dotenv
from flask import Flask, jsonify

from feedback.config import ROOT, load_settings
from feedback.storage import Storage
from feedback.jobs import Jobs
from web.pages import pages
from web.api import api


def create_app(test_config=None):
    load_dotenv(ROOT / ".env")
    app = Flask(__name__)
    settings = load_settings()
    app.config.update(SECRET_KEY=secrets.token_hex(32), PROJECT_ROOT=ROOT,
        STORAGE_ROOT=ROOT / "storage", MAX_CONTENT_LENGTH=settings["max_upload_mb"] * 1024 ** 2,
        SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_HTTPONLY=True,
        TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"])
    if test_config:
        app.config.update(test_config)
    storage = Storage(Path(app.config["STORAGE_ROOT"]))
    app.extensions.update(storage=storage, jobs=Jobs(storage, app.logger))
    app.register_blueprint(pages)
    app.register_blueprint(api)

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(error=f"上传文件不可超过 {settings['max_upload_mb']} MB。"), 413

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        if response.mimetype == "application/json":
            response.headers["Cache-Control"] = "no-store"
        return response
    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.getenv("PORT", "5050")), debug=False)

