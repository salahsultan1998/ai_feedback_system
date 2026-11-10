"""One app process: report jobs and Feishu locks are process-local."""
import os

bind = "127.0.0.1:5050"
workers = 1
worker_class = "gthread"
threads = 4
timeout = 120
graceful_timeout = 30
max_requests = 0
preload_app = False
umask = 0o077
accesslog = "-"
errorlog = "-"
# Keep report references, query strings and referrers out of access logs.
access_log_format = '%(h)s %(t)s "%(m)s" %(s)s %(L)s'


def on_starting(server):
    if server.cfg.workers != 1 or server.cfg.preload_app or server.cfg.reload:
        raise RuntimeError("Use one worker without preload/reload; stop and start for deployments.")
    # Gunicorn's umask setting covers its own sockets/temp files, not uploads.
    os.umask(0o077)
