"""Authenticate existing Supabase users and issue app-local sessions."""
from contextlib import closing
from hashlib import sha256
import json
import os
import re
import secrets
import sqlite3
import ssl
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, session, url_for

from feedback.runtime import env_bool


auth = Blueprint("auth", __name__)
SESSION_SECONDS = 8 * 60 * 60
LOGIN_LIMIT = 20
LOGIN_WINDOW = 10 * 60
LOGIN_ERROR = "无法登录，请确认账号已获授权，并检查邮箱和密码。"


def auth_settings():
    if not env_bool("FEEDBACK_AUTH_ENABLED", os.getenv("FEEDBACK_ENV") == "production"):
        return None
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip() or os.getenv("SUPABASE_ANON_KEY", "").strip()
    addresses = os.getenv("FEEDBACK_ALLOWED_EMAILS", "").casefold().split(",")
    allowed = frozenset(email.strip() for email in addresses if email.strip())
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path or parsed.query or parsed.fragment):
        raise RuntimeError("Platform login requires an HTTPS SUPABASE_URL without a path.")
    if not key or key.startswith("sb_secret_"):
        raise RuntimeError("Platform login requires SUPABASE_PUBLISHABLE_KEY or SUPABASE_ANON_KEY.")
    if not allowed or any(not re.fullmatch(r"[^@\s]+@[^@\s]+", email) for email in allowed):
        raise RuntimeError("Platform login requires FEEDBACK_ALLOWED_EMAILS with comma-separated addresses.")
    return url, key, allowed


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def auth_https_handler():
    context = ssl.create_default_context()
    # Some macOS Python installations have no certificates in the OpenSSL store.
    if not context.get_ca_certs() and os.path.isfile("/etc/ssl/cert.pem"):
        context.load_verify_locations(cafile="/etc/ssl/cert.pem")
    return HTTPSHandler(context=context)


def supabase_sign_in(url, key, email, password):
    """Return only verified identity. Supabase tokens never leave this call."""
    data = json.dumps({"email": email, "password": password}).encode()
    query = Request(url + "/auth/v1/token?grant_type=password", data=data,
                    headers={"apikey": key, "Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect(), auth_https_handler()).open(query, timeout=10) as response:
            result = json.loads(response.read(65536))
    except HTTPError as error:
        error.close()
        if error.code in {400, 401, 403, 422}:
            return None
        raise OSError("Supabase sign-in unavailable") from None
    user = result.get("user") if isinstance(result, dict) else None
    if not isinstance(user, dict) or not result.get("access_token"):
        return None
    confirmed = user.get("email")
    account = user.get("id")
    if not isinstance(confirmed, str) or not user.get("email_confirmed_at") or not isinstance(account, str) or not account:
        return None
    return account, confirmed.casefold()


class SessionStore:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                digest TEXT PRIMARY KEY, user_id TEXT NOT NULL, email TEXT NOT NULL, expires REAL NOT NULL)""")
        path.chmod(0o600)

    @staticmethod
    def digest(key):
        return sha256(key.encode()).hexdigest()

    def create(self, user_id, email):
        key = secrets.token_urlsafe(32)
        now = time.time()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                       (self.digest(key), user_id, email, now + SESSION_SECONDS))
        return key

    def read(self, key):
        if not key:
            return None
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT user_id, email FROM sessions WHERE digest = ? AND expires > ?",
                              (self.digest(key), time.time())).fetchone()

    def delete(self, key):
        if key:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("DELETE FROM sessions WHERE digest = ?", (self.digest(key),))


def install_auth(app, settings):
    app.config["AUTH_ENABLED"] = settings is not None
    app.register_blueprint(auth)
    if settings is None:
        return
    app.extensions["feedback_identity"] = settings
    app.extensions["feedback_sessions"] = SessionStore(app.config["STORAGE_ROOT"] / "sessions.sqlite3")
    app.extensions["feedback_login_limit"] = (threading.Lock(), {})

    @app.before_request
    def require_account():
        if request.endpoint in {"static", "health"}:
            return None
        current = app.extensions["feedback_sessions"].read(session.get("auth_sid"))
        if current and current[1] in settings[2]:
            g.auth_user_id, g.auth_email = current
            return None
        if "auth_sid" in session:
            session.clear()
        if request.blueprint == "auth":
            return None
        if request.blueprint == "api":
            return jsonify(error="登录已过期，请重新登录。", login_url=url_for("auth.login_page")), 401
        return redirect(url_for("auth.login_page"))


def check_csrf():
    token = session.get("csrf", "")
    return token and secrets.compare_digest(token.encode(), request.form.get("csrf", "").encode())


def login_limited():
    lock, windows = current_app.extensions["feedback_login_limit"]
    address = request.remote_addr
    now = time.monotonic()
    with lock:
        for ip, (expires, _) in list(windows.items()):
            if expires <= now:
                del windows[ip]
        expires, count = windows.get(address, (now + LOGIN_WINDOW, 0))
        if count >= LOGIN_LIMIT:
            return True
        windows[address] = expires, count + 1
        return False


def login_form(email="", error=None, status=200):
    session.setdefault("csrf", secrets.token_hex(24))
    return render_template("login.html", email=email, error=error), status


@auth.get("/login")
def login_page():
    if not current_app.config["AUTH_ENABLED"] or getattr(g, "auth_email", None):
        return redirect(url_for("pages.home"))
    return login_form()


@auth.post("/login")
def login():
    if not current_app.config["AUTH_ENABLED"] or getattr(g, "auth_email", None):
        return redirect(url_for("pages.home"), 303)
    if not check_csrf():
        return login_form(error="页面已过期，请重新输入邮箱和密码。", status=403)
    email = request.form.get("email", "").strip().casefold()
    password = request.form.get("password", "")
    url, key, allowed = current_app.extensions["feedback_identity"]
    if login_limited():
        return login_form(error="登录尝试过多，请稍后再试。", status=429)
    if email not in allowed or not password or len(password) > 1024:
        return login_form(email[:254], LOGIN_ERROR, 401)
    try:
        identity = supabase_sign_in(url, key, email, password)
    except (OSError, ValueError):
        return login_form(email, "登录服务暂不可用，请稍后再试。", 503)
    if not identity or identity[1] != email:
        return login_form(email, LOGIN_ERROR, 401)
    sid = current_app.extensions["feedback_sessions"].create(*identity)
    session.clear()
    session["auth_sid"] = sid
    return redirect(url_for("pages.home"), 303)


@auth.post("/logout")
def logout():
    if not current_app.config["AUTH_ENABLED"]:
        return redirect(url_for("pages.home"), 303)
    if not check_csrf():
        return login_form(error="页面已过期，请重新打开网页。", status=403)
    current_app.extensions["feedback_sessions"].delete(session.get("auth_sid"))
    session.clear()
    return redirect(url_for("auth.login_page"), 303)
