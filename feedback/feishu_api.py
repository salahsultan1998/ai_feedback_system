"""Authenticated Feishu APIs with verified HTTPS and in-memory token caching."""
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE = "https://open.feishu.cn/open-apis"
_tokens = {}
_token_lock = threading.Lock()


class ApiError(ValueError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(method, path, payload=None, token=None):
    """Never follow redirects or expose authentication/report data in errors."""
    url = BASE + path
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(payload, ensure_ascii=False) if payload is not None else None
    request = Request(url, data=body.encode() if body is not None else None,
                      headers=headers, method=method)
    try:
        try:
            with build_opener(NoRedirect()).open(request, timeout=20) as response:
                raw = response.read()
                status = response.status
        except HTTPError as error:
            # API rejections often include useful JSON even with non-2xx HTTP.
            try:
                raw = error.read()
                status = error.code
            finally:
                error.close()
        except URLError as error:
            if not isinstance(error.reason, ssl.SSLCertVerificationError):
                raise
            curl = shutil.which("curl")
            if not curl:
                raise ApiError("飞书 HTTPS 证书验证失败，请安装系统证书或 curl。") from None
            lines = [f"url = {json.dumps(url)}", f"request = {json.dumps(method)}"]
            lines += ["header = " + json.dumps(f"{key}: {value}") for key, value in headers.items()]
            if body is not None:
                lines.append("data-binary = " + json.dumps(body, ensure_ascii=False))
            result = subprocess.run(
                [curl, "--disable", "--config", "-", "--silent", "--show-error",
                 "--connect-timeout", "10", "--max-time", "25", "--proto", "=https",
                 "--write-out", "\n%{http_code}"],
                input="\n".join(lines) + "\n", text=True, capture_output=True, timeout=30)
            if result.returncode:
                raise ApiError(f"飞书接口连接失败（curl {result.returncode}），可再次点击推送以继续。")
            raw, _, status = result.stdout.rpartition("\n")
            if not status.isdigit():
                raise ApiError("飞书接口未返回有效的 HTTP 状态，可再次点击推送以继续。")
        result = json.loads(raw)
    except ApiError:
        raise
    except (URLError, OSError, TimeoutError, subprocess.TimeoutExpired, json.JSONDecodeError):
        raise ApiError("无法获取飞书接口结果，请检查网络后再次点击推送以继续。") from None
    if not isinstance(result, dict) or result.get("code") != 0 or not 200 <= int(status) < 300:
        code = result.get("code") if isinstance(result, dict) else None
        detail = result.get("msg", "响应格式无效") if isinstance(result, dict) else "响应格式无效"
        detail = detail if isinstance(detail, str) else "响应格式无效"
        for value in (token, (payload or {}).get("app_secret")):
            if value:
                detail = detail.replace(value, "[已隐藏]")
        detail = re.sub(r"https?://\S+", "[链接已隐藏]", detail)
        raise ApiError(f"飞书表格接口失败（code={code}）：{' '.join(detail.split())[:500]}", code)
    return result


class Client:
    def __init__(self, settings):
        self.app_id = os.getenv(settings.get("app_id_env", "FEISHU_APP_ID"), "").strip()
        self.secret = os.getenv(settings.get("app_secret_env", "FEISHU_APP_SECRET"), "").strip()
        if not self.app_id or not self.secret:
            raise ValueError("请在 .env 中配置飞书应用 App ID 和 App Secret，并重启 app。")
        self.cache_key = hashlib.sha256((self.app_id + "\n" + self.secret).encode()).hexdigest()

    def token(self, refresh=False):
        with _token_lock:
            cached, expires = _tokens.get(self.cache_key, (None, 0))
            if cached and expires > time.monotonic() and not refresh:
                return cached
            result = request_json("POST", "/auth/v3/tenant_access_token/internal",
                                  {"app_id": self.app_id, "app_secret": self.secret})
            token, lifetime = result.get("tenant_access_token"), result.get("expire")
            if not isinstance(token, str) or not isinstance(lifetime, int) or lifetime <= 0:
                raise ApiError("飞书未返回有效的应用访问凭证。")
            _tokens[self.cache_key] = (token, time.monotonic() + max(0, lifetime - 120))
            return token

    def call(self, method, path, payload=None):
        try:
            return request_json(method, path, payload, self.token()).get("data", {})
        except ApiError as error:
            if error.code not in {99991661, 99991663, 99991668}:
                raise
            return request_json(method, path, payload, self.token(refresh=True)).get("data", {})
