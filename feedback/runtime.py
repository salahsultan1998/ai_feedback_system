"""Local defaults and the single-process Linux deployment contract."""
import os
from pathlib import Path
import re

from werkzeug.middleware.proxy_fix import ProxyFix

from .config import ROOT


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    if value.lower() not in {"true", "false", "1", "0"}:
        raise RuntimeError(f"{name} must be true or false.")
    return value.lower() in {"true", "1"}


def configure_runtime(app):
    environment = os.getenv("FEEDBACK_ENV", "development")
    if environment not in {"development", "production"}:
        raise RuntimeError("FEEDBACK_ENV must be development or production.")
    production = environment == "production"
    secret = os.getenv("FLASK_SECRET_KEY")
    storage = os.getenv("FEEDBACK_STORAGE")
    hosts = os.getenv("FEEDBACK_ALLOWED_HOSTS")
    secure = env_bool("FEEDBACK_SECURE_COOKIES", production)
    behind_proxy = env_bool("FEEDBACK_BEHIND_PROXY")
    if production:
        if not secret or len(secret.strip()) < 32:
            raise RuntimeError("Production requires FLASK_SECRET_KEY with at least 32 characters.")
        if not storage or not Path(storage).is_absolute():
            raise RuntimeError("Production requires an absolute FEEDBACK_STORAGE directory.")
        if not hosts or not hosts.strip():
            raise RuntimeError("Production requires FEEDBACK_ALLOWED_HOSTS.")
    if secret:
        app.config["SECRET_KEY"] = secret
    if hosts is not None:
        allowed = [host.strip() for host in hosts.split(",")]
        if any(not re.fullmatch(r"(?:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?|\[::1\])", host) for host in allowed):
            raise RuntimeError("FEEDBACK_ALLOWED_HOSTS must contain comma-separated hostnames without schemes, ports or wildcards.")
        app.config["TRUSTED_HOSTS"] = allowed
    root = Path(storage or app.config["STORAGE_ROOT"]).resolve()
    if root == Path(root.anchor) or root == ROOT or root.is_relative_to(ROOT / "static"):
        raise RuntimeError("FEEDBACK_STORAGE must be a private data directory, outside static files.")
    app.config.update(STORAGE_ROOT=root, SESSION_COOKIE_SECURE=secure)
    if production:
        # Acquire before Jobs scans persisted tasks. A second process must never
        # mark the first process's live work as interrupted.
        import fcntl
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.stat().st_mode & 0o077:
            raise RuntimeError("FEEDBACK_STORAGE must be private (directory permissions 0700).")
        lock = (root / ".app.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise RuntimeError("FEEDBACK_STORAGE is already in use. Run exactly one app worker; stop it before restarting.") from None
        # Keep the descriptor open for the process lifetime. Never unlink it.
        app.extensions["storage_lock"] = lock
    if behind_proxy:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0)
