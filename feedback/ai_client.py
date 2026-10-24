"""OpenRouter access, bounded retries and cooperative cancellation."""
import hashlib
import json
import os
from pathlib import Path
import time

from openai import OpenAI, APIConnectionError, APIStatusError

from .storage import atomic_json


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
                    raise ValueError(f"模型请求失败（HTTP {error.status_code}），请检查密钥和模型配置。") from error
                if attempt == config["max_retries"]:
                    raise ValueError("模型请求失败，请检查连接、配置或响应格式后重试。") from error
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
