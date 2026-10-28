"""OpenRouter access, bounded retries and cooperative cancellation."""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import time

from openai import OpenAI, APIConnectionError, APIStatusError

from .storage import atomic_json

logger = logging.getLogger(__name__)


class ModelRequestError(ValueError):
    def __init__(self, message, status_code, stops_processing=False):
        super().__init__(message)
        self.status_code = status_code
        self.stops_processing = stops_processing


def request_error(error, prompt, payload):
    """Keep the provider's reason, without dumping credentials or input bodies."""
    body = error.body
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]
    detail = body.get("message", "") if isinstance(body, dict) else ""
    detail = detail if isinstance(detail, str) else ""
    metadata = body.get("metadata", {}) if isinstance(body, dict) else {}
    raw = metadata.get("raw") if isinstance(metadata, dict) else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if isinstance(raw, dict):
        provider = raw.get("error", raw)
        provider_detail = provider.get("message") if isinstance(provider, dict) else None
        if isinstance(provider_detail, str) and provider_detail and provider_detail != detail:
            detail += "；" + provider_detail
    if not detail and isinstance(body, str) and body.strip():
        detail = "接口返回了非 JSON 错误页面，请检查网络或网关拦截。"
    # A provider can echo rejected content. Hide any quoted input before logging.
    def hide_inputs(value):
        nonlocal detail
        if isinstance(value, str) and len(value) >= 4:
            detail = detail.replace(value, "[输入已隐藏]")
        elif isinstance(value, dict):
            for item in value.values():
                hide_inputs(item)
        elif isinstance(value, list):
            for item in value:
                hide_inputs(item)
    hide_inputs(payload)
    for value in (os.getenv("OPENROUTER_API_KEY"), prompt):
        if value:
            detail = detail.replace(value, "[已隐藏]")
    detail = re.sub(r"sk-[A-Za-z0-9_-]+", "[密钥已隐藏]", detail)
    detail = re.sub(r"https?://\S+", "[链接已隐藏]", detail)
    detail = " ".join(detail.split())[:500]
    status = error.status_code
    hints = {401: "请检查 OPENROUTER_API_KEY 是否有效，并在修改后重启 Flask。",
             402: "请检查 OpenRouter 余额和密钥额度。",
             403: "请求被拒绝，请检查 OpenRouter 访问权限、预算限制或内容策略。",
             404: "请检查 OPENROUTER_MODEL 和模型可用性。",
             429: "请求受到限流，请稍后重试或降低并发。"}
    message = f"模型请求失败（HTTP {status}）。" + hints.get(status, "请检查模型服务和请求配置。")
    if detail:
        message += f" 服务返回：{detail}"
    # A 403 may concern just one batch's content, so do not assume all other
    # batches are forbidden. Authentication, credit and model errors are global.
    return ModelRequestError(message, status, stops_processing=status in {401, 402, 404})


def check_stop(should_stop=None):
    if should_stop and should_stop():
        raise InterruptedError("已停止。完成的处理结果已保存，可再次生成以继续。")


def model_name(config):
    return os.getenv("OPENROUTER_MODEL") or config["model"]


def request_json(prompt, payload, config, validate, should_stop=None):
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise ValueError("请先在项目 .env 中设置 OPENROUTER_API_KEY。")
    with OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1",
                timeout=config["timeout_seconds"], max_retries=0) as client:
        for attempt in range(config["max_retries"] + 1):
            check_stop(should_stop)
            try:
                response = client.chat.completions.create(model=model_name(config), temperature=0,
                    response_format={"type": "json_object"}, messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
                return validate(json.loads(response.choices[0].message.content or "{}"))
            except (APIConnectionError, APIStatusError, ValueError) as error:
                if isinstance(error, APIStatusError) and error.status_code < 500 and error.status_code not in {408, 409, 429}:
                    raise request_error(error, prompt, payload) from None
                if attempt == config["max_retries"]:
                    if isinstance(error, APIStatusError):
                        raise request_error(error, prompt, payload) from None
                    reason = ("连接超时或无法连接 OpenRouter。" if isinstance(error, APIConnectionError)
                              else "模型响应未通过 JSON 或内容校验。")
                    logger.warning("Model request exhausted retries: model=%s reason=%s", model_name(config), reason)
                    raise ValueError("模型请求失败，" + reason + "请检查后重试。") from None
                deadline = time.monotonic() + min(config["retry_delay"] * 2 ** attempt, 30)
                while time.monotonic() < deadline:
                    check_stop(should_stop)
                    time.sleep(min(.2, max(0, deadline - time.monotonic())))


def cached_request(name, payload, config, cache_dir, validate, should_stop=None):
    prompt = config[name + "_prompt"]
    content = json.dumps([model_name(config), prompt, payload], ensure_ascii=False, sort_keys=True)
    if len(json.dumps(payload, ensure_ascii=False, sort_keys=True)) > config["report"]["ai_max_input_chars"]:
        raise ValueError("报告分析输入超过配置的长度限制。")
    path = Path(cache_dir) / (hashlib.sha256(content.encode()).hexdigest() + ".json")
    check_stop(should_stop)
    if path.exists():
        return validate(json.loads(path.read_text(encoding="utf-8")))
    result = request_json(prompt, payload, config, validate, should_stop)
    atomic_json(path, result)
    return result
