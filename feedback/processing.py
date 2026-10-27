"""Translate selected records and checkpoint after each bounded batch."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import threading

import pandas as pd

from . import ai_client
from .storage import atomic_csv, read_csv

OUTPUT = ["中文翻译", "最终标签", "具体主题", "错误", "处理指纹"]

BATCH_INSTRUCTIONS = """

本次调用使用批量输入和输出，以下协议替代上文关于单条输入和单条 JSON 输出的说明，翻译和分类规则不变。
输入是一个 JSON 对象，其中 feedbacks 数组中的每项包含 feedback_id、feedback、original_label。
请独立处理每条反馈，不要把其他反馈的事实、诉求或账号混入当前条目。
每项结果必须原样保留 feedback_id 字符串（包括前导零），不能漏项、重复、改写 ID 或输出未提供的 ID。
结果可以乱序，但每条输入都必须有且只有一个对应结果。
只返回以下 JSON 对象，不要 Markdown 或其他文字：
{"results":[{"feedback_id":"原样的字符串 ID","translation_zh":"简体中文翻译","label":"主标签","theme":"固定主题或空字符串"}]}
"""


def validate_result(result, config):
    if not isinstance(result, dict) or any(not isinstance(result.get(k), str) for k in ("translation_zh", "label", "theme")):
        raise ValueError("模型必须返回翻译、主标签和子标签字符串。")
    translation, label, theme = (result[k].strip() for k in ("translation_zh", "label", "theme"))
    labels = config["schema"]["labels"]
    if not translation or label not in labels:
        raise ValueError("模型翻译为空或主标签不在配置中。")
    if theme and theme not in [item["name"] for item in labels[label]["themes"]]:
        raise ValueError("模型子标签不属于所选主标签。")
    return {"translation_zh": translation, "label": label, "theme": theme}


def fingerprint(row, config):
    content = [config["game"], ai_client.model_name(config), config["classification_prompt"],
               str(row["反馈ID"]), str(row["用户自己填写的内容"]), str(row["反馈标签"])]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()


def validate_batch(value, feedback_ids, config):
    if not isinstance(value, dict) or not isinstance(value.get("results"), list):
        raise ValueError("批量模型响应必须包含 results 数组。")
    grouped = {feedback_id: [] for feedback_id in feedback_ids}
    for item in value["results"]:
        if isinstance(item, dict) and isinstance(item.get("feedback_id"), str) and item["feedback_id"] in grouped:
            grouped[item["feedback_id"]].append(item)
    answers, errors = {}, {}
    for feedback_id, items in grouped.items():
        if len(items) != 1:
            errors[feedback_id] = "模型未返回此反馈的唯一结果，请重试。"
            continue
        try:
            answers[feedback_id] = validate_result(items[0], config)
        except ValueError as error:
            errors[feedback_id] = str(error)
    return answers, errors


def make_batches(items, size, max_chars):
    """Bound item count and serialized input length without truncating a record."""
    batch, chars = [], len('{"feedbacks": []}')
    for item in items:
        length = len(json.dumps(item, ensure_ascii=False)) + (2 if batch else 0)
        if batch and (len(batch) >= size or chars + length > max_chars):
            yield batch
            batch, chars = [], len('{"feedbacks": []}')
            length = len(json.dumps(item, ensure_ascii=False))
        batch.append(item)
        chars += length
    if batch:
        yield batch


def process_feedback(rows, config, path, progress, should_stop=None):
    batch_size = config.get("classification_batch_size", 10)
    max_chars = config.get("classification_batch_max_chars", 16000)
    if any(type(value) is not int or value < 1 for value in (batch_size, max_chars)):
        raise ValueError("翻译分类批量大小和输入长度限制必须为正整数。")
    saved = read_csv(path).set_index("反馈ID").to_dict("index") if path.exists() else {}
    result = rows.reset_index(drop=True).copy()
    for column in OUTPUT:
        result[column] = ""
    pending = []
    for index, row in result.iterrows():
        old = saved.get(str(row["反馈ID"]), {})
        key = fingerprint(row, config)
        valid = False
        try:
            validate_result({"translation_zh": old.get("中文翻译"), "label": old.get("最终标签"), "theme": old.get("具体主题")}, config)
            valid = old.get("处理指纹") == key and not old.get("错误")
        except ValueError:
            pass
        if valid:
            for column in OUTPUT:
                result.at[index, column] = old[column]
        else:
            result.at[index, "处理指纹"] = key
            result.at[index, "错误"] = "待处理"
            pending.append(index)

    active_ids = set(result["反馈ID"])
    historical = [dict(value, 反馈ID=fid) for fid, value in saved.items() if fid not in active_ids]

    def save():
        # Retain previous work for another date selection in this same upload.
        atomic_csv(path, pd.concat([pd.DataFrame(historical), result], ignore_index=True))

    done = len(result) - len(pending)
    lock = threading.Lock()
    indices = {str(result.at[index, "反馈ID"]): index for index in pending}
    items = [{"feedback_id": str(result.at[index, "反馈ID"]),
              "feedback": result.at[index, "用户自己填写的内容"],
              "original_label": result.at[index, "反馈标签"]} for index in pending]
    batches = list(make_batches(items, batch_size, max_chars))
    prompt = config["classification_prompt"] + BATCH_INSTRUCTIONS

    def record(answers, errors):
        nonlocal done
        # Worker responses can finish in any order; serialize DataFrame writes,
        # disk checkpoints and progress updates, never network requests.
        with lock:
            for feedback_id, answer in answers.items():
                result.loc[indices[feedback_id], OUTPUT[:4]] = [
                    answer["translation_zh"], answer["label"], answer["theme"], ""]
            for feedback_id, error in errors.items():
                result.at[indices[feedback_id], "错误"] = error
            done += len(answers)
            save()
            progress(stage="processing", message="正在批量翻译与分类", done=done, total=len(result))

    def process_batch(batch):
        payload = {"feedbacks": list(batch)}
        accepted, errors = {}, {}

        def validate(value):
            remaining = [item["feedback_id"] for item in payload["feedbacks"]]
            answers, invalid = validate_batch(value, remaining, config)
            if answers:
                accepted.update(answers)
                record(answers, {})
            if invalid:
                errors.update(invalid)
                # request_json serializes this payload again on its next retry.
                # Successful rows are already saved and must not be resubmitted.
                payload["feedbacks"] = [item for item in payload["feedbacks"]
                                        if item["feedback_id"] not in answers]
                raise ValueError(f"批量响应中有 {len(invalid)} 条反馈缺失、重复或校验失败。")
            return answers

        try:
            ai_client.request_json(prompt, payload, config, validate, should_stop)
        except InterruptedError:
            return
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else type(error).__name__
            record({}, {item["feedback_id"]: errors.get(item["feedback_id"], message)
                        for item in batch if item["feedback_id"] not in accepted})

    save()
    progress(stage="processing", message="正在批量翻译与分类", done=done, total=len(result))
    size = config["concurrency"]
    with ThreadPoolExecutor(max_workers=size) as pool:
        for offset in range(0, len(batches), size):
            ai_client.check_stop(should_stop)
            futures = [pool.submit(process_batch, batch) for batch in batches[offset:offset + size]]
            for future in as_completed(futures):
                future.result()
    ai_client.check_stop(should_stop)
    failed = int(result["错误"].ne("").sum())
    if failed:
        raise ValueError(f"有 {failed} 条反馈尚未处理成功，未生成报告。已完成的结果已保存，请重试。")
    return result
