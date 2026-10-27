"""Manual report notifications. Credentials are resolved only when sending."""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
import shutil
import ssl
import subprocess
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

from .storage import atomic_json
from . import feishu_sheets

FORMAT = "card-markdown-v1"
MAX_BODY_BYTES = 18000  # Below Feishu's 20 KB limit, including JSON and signatures.


def credentials(config):
    settings = config.get("feishu", {})
    if not settings.get("enabled"):
        raise ValueError("未配置飞书推送。")
    url = os.getenv(settings.get("webhook_env", ""), "").strip()
    if not re.fullmatch(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9_-]+", url):
        raise ValueError("请配置此游戏的飞书 webhook 环境变量。")
    secret_name = settings.get("secret_env")
    secret = os.getenv(secret_name, "").strip() if secret_name else ""
    if secret_name and not secret:
        raise ValueError("请配置此游戏的飞书签名密钥，或移除 secret_env 配置。")
    return url, secret


def state(folder, config):
    try:
        credentials(config)
        available = True
        reason = ""
    except ValueError as error:
        available, reason = False, str(error)
    sheet_settings = feishu_sheets.settings_for(config)
    sheet = feishu_sheets.saved_state(folder) if sheet_settings else {}
    if sheet_settings:
        try:
            feishu_sheets.destination(sheet_settings)
            feishu_sheets.Client(sheet_settings)
        except ValueError as error:
            available, reason = False, str(error)
    path = folder / "feishu_push.json"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    # An interrupted process may have sent the message before saving success.
    if saved.get("status") == "sending":
        saved = {**saved, "status": "unknown"}
    if saved.get("status") == "sent" and saved.get("format") != FORMAT:
        saved = {"status": "ready", "reason": "此前使用旧版消息格式，可推送新版完整报告卡片。"}
    if sheet_settings and saved.get("status") == "sent" and not saved.get("sheet_url"):
        saved = {"status": "ready", "reason": "此前卡片未包含在线明细，可推送明细页签及新版卡片。"}
    result = {**saved, "available": available, "reason": reason or saved.get("reason", "")}
    if sheet_settings:
        result["sheet"] = {key: sheet[key] for key in
                           ("status", "url", "title", "written_rows", "total_rows", "error") if key in sheet}
        if sheet.get("status") == "failed":
            result["last_error"] = sheet.get("error", "明细推送失败，请重试。")
    return result


def report_text(folder, detail_url=None):
    text = (folder / "report.md").read_text(encoding="utf-8")
    # A relative link to a local workbook is unusable in a group message.
    replacement = f"[打开完整反馈明细]({detail_url})" if detail_url else "完整翻译明细 Excel 请在 app 中下载。"
    text, count = re.subn(r"\[[^\]]*\]\(feedback_details\.xlsx\)", lambda _: replacement, text)
    if detail_url and not count:
        text += "\n\n" + replacement
    # The card Markdown component does not support arbitrary HTML.
    return re.sub(r"<br\s*/?>", "；", text, flags=re.I).strip()


def card_payload(title, text):
    return {"msg_type": "interactive", "card": {
        "schema": "2.0",
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
        "body": {"elements": [{"tag": "markdown", "content": text}]}}}


def report_parts(text, title):
    """Split paragraphs and table rows; repeat headers on continued tables."""
    def fits(value):
        payload = card_payload(title + "（999999/999999）", value)
        payload.update(timestamp="9" * 20, sign="x" * 44)
        return len(json.dumps(payload, ensure_ascii=False).encode()) <= MAX_BODY_BYTES

    parts, current = [], ""

    def flush():
        nonlocal current
        if current.strip():
            parts.append(current.strip())
        current = ""

    def append(block):
        nonlocal current
        if fits(current + block):
            current += block
            return
        flush()
        if fits(block):
            current = block
            return
        # A single unusually long paragraph/line must still be sent in full.
        while block:
            low, high = 0, len(block)
            while low < high:
                middle = (low + high + 1) // 2
                if fits(block[:middle]):
                    low = middle
                else:
                    high = middle - 1
            if not low:
                raise ValueError("报告标题过长，无法构造飞书消息。")
            current, block = block[:low], block[low:]
            if block:
                flush()

    for block in re.split(r"(\n\s*\n)", text):
        lines = block.splitlines(keepends=True)
        table = len(lines) >= 2 and lines[0].lstrip().startswith("|") and re.fullmatch(
            r"[\s|:\-]+", lines[1]) and "-" in lines[1]
        if not table or fits(block):
            append(block)
            continue
        header = "".join(lines[:2])
        flush()
        current = header
        for row in lines[2:]:
            if not fits(header + row):
                # Pathological long rows become labeled paragraphs, preserving
                # every cell instead of producing broken table syntax.
                flush()
                labels = lines[0].strip().strip("|").split("|")
                cells = row.strip().strip("|").split("|")
                for index, cell in enumerate(cells):
                    label = labels[index].strip() if index < len(labels) else str(index + 1)
                    append(f"\n\n**{label}**\n\n{cell.strip()}\n\n")
                flush()
                current = header
            else:
                if not fits(current + row):
                    flush()
                    current = header
                current += row
        if current == header:
            current = ""
    flush()
    if not parts:
        raise ValueError("报告内容为空，无法推送。")
    return parts


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_payload(url, payload):
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode(),
                      headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    try:
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            return json.loads(response.read())
    except URLError as error:
        if not isinstance(error.reason, ssl.SSLCertVerificationError):
            raise
        # TLS verification fails before HTTP data is transmitted. On macOS curl
        # uses the system trust store, unlike a standalone Python installation.
        # Never disable certificate verification or retry an ambiguous delivery.
        curl = shutil.which("curl")
        if not curl:
            raise ValueError("Python 无法验证飞书 HTTPS 证书。请安装系统 CA 证书或 curl 后重试。") from None
        # Keep the credential URL and report out of process arguments and logs.
        config = f'url = "{url}"\nheader = "Content-Type: application/json; charset=utf-8"\ndata-binary = {json.dumps(json.dumps(payload, ensure_ascii=False), ensure_ascii=False)}\n'
        result = subprocess.run(
            [curl, "--disable", "--config", "-", "--silent", "--show-error",
             "--connect-timeout", "10", "--max-time", "20", "--proto", "=https",
             "--write-out", "\n%{http_code}"],
            input=config, text=True, capture_output=True, timeout=25)
        if result.returncode:
            if result.returncode in {5, 6, 7, 35, 60, 77}:
                descriptions = {5: "代理地址解析失败", 6: "飞书域名解析失败", 7: "无法连接飞书",
                                35: "HTTPS 握手失败", 60: "系统无法验证 HTTPS 证书", 77: "CA 证书无法读取"}
                raise ValueError(f"{descriptions[result.returncode]}（curl {result.returncode}），请检查网络或证书后重试。")
            raise TimeoutError("Ambiguous curl delivery")
        body, _, status = result.stdout.rpartition("\n")
        if not status.isdigit() or not 200 <= int(status) < 300:
            raise TimeoutError("Unexpected HTTP response")
        return json.loads(body)


def push(folder, config, retry_unknown=False):
    url, secret = credentials(config)
    saved = state(folder, config)
    if saved.get("status") == "sent":
        return saved
    if saved.get("status") == "unknown" and not retry_unknown:
        raise ValueError("上次发送结果未知，请先检查群消息；为避免重复，本报告暂不再次发送。")
    sheet = feishu_sheets.sync(folder, config)
    text = report_text(folder, sheet["url"] if sheet else None)
    title = re.sub(r"^#+\s*", "", text.splitlines()[0])[:100]
    parts = report_parts(text, title)
    digest = hashlib.sha256(json.dumps([FORMAT, parts], ensure_ascii=False).encode()).hexdigest()
    completed = saved.get("sent_parts", 0) if saved.get("digest") == digest else 0
    path = folder / "feishu_push.json"
    progress = {"format": FORMAT, "digest": digest, "sent_parts": completed, "total_parts": len(parts)}
    if sheet:
        progress["sheet_url"] = sheet["url"]
    for index in range(completed, len(parts)):
        if index > completed:
            time.sleep(0.65)
        numbered_title = title if len(parts) == 1 else f"{title}（{index + 1}/{len(parts)}）"
        payload = card_payload(numbered_title, parts[index])
        if secret:
            timestamp = str(int(time.time()))
            signature = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
            payload.update(timestamp=timestamp, sign=base64.b64encode(signature).decode())
        atomic_json(path, {**progress, "status": "sending"})
        send_part(url, payload, path, progress)
        progress["sent_parts"] = index + 1
        atomic_json(path, {**progress, "status": "partial"})
    atomic_json(path, {**progress, "status": "sent", "sent_at": datetime.now(timezone.utc).isoformat()})
    return state(folder, config)


def send_part(url, payload, path, progress):
    def save(status, **details):
        atomic_json(path, {**progress, "status": status, **details})

    try:
        result = send_payload(url, payload)
        if not isinstance(result, dict) or result.get("code") != 0:
            code = result.get("code") if isinstance(result, dict) else None
            code = str(code) if isinstance(code, int) else "未知"
            detail = result.get("msg") or result.get("StatusMessage") if isinstance(result, dict) else None
            detail = detail if isinstance(detail, str) else "飞书未返回详细错误说明。"
            # Error responses can echo request values; never expose credentials.
            detail = detail.replace(url, "[webhook 已隐藏]")
            for key in ("sign",):
                if payload.get(key):
                    detail = detail.replace(payload[key], "[已隐藏]")
            detail = re.sub(r"https?://\S+", "[链接已隐藏]", detail)
            detail = " ".join(detail.split())[:600]
            message = f"飞书拒绝了消息（code={code}）：{detail}"
            save("failed", last_error=message, error_code=code)
            raise FeishuRejected(message)
    except FeishuRejected:
        raise
    except HTTPError:
        save("unknown")
        raise ValueError("飞书请求异常，发送结果未知，请先检查群消息。") from None
    except URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            message = "Python 无法验证飞书 HTTPS 证书，请检查 CA 证书后重试。"
        elif isinstance(error.reason, OSError) and error.reason.errno in {-2, 8, 61, 111}:
            message = "无法解析或连接飞书地址，请检查网络后重试。"
        else:
            save("unknown")
            raise ValueError("发送结果未知，请先检查群消息，避免重复推送。") from None
        save("failed")
        raise ValueError(message) from None
    except (TimeoutError, OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
        save("unknown")
        raise ValueError("发送结果未知，请先检查群消息，避免重复推送。") from None
    except ValueError:
        save("failed")
        raise


class FeishuRejected(ValueError):
    """Explicit service rejection; preserve diagnostic details in the checkpoint."""
