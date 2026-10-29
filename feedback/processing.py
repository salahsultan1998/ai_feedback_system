"""Translate selected records and checkpoint after each bounded batch."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import logging
import threading

import pandas as pd

from . import ai_client
from .policies import ABUSE_REASON, is_suggestion_label
from .storage import atomic_csv, read_csv

RESULT_COLUMNS = {"translation_zh": "中文翻译", "label": "最终标签", "theme": "具体主题",
                  "is_suggestion": "是否建议", "is_valid": "是否有效", "exclusion_reason": "排除原因"}
OUTPUT = [*RESULT_COLUMNS.values(), "错误", "处理指纹"]
EXCLUSION_REASONS = ["缺少明确反馈对象或诉求", "内容无法理解", ABUSE_REASON]
logger = logging.getLogger(__name__)

BATCH_INSTRUCTIONS = """

本次调用使用批量输入和输出，以下协议替代上文关于单条输入和单条 JSON 输出的说明，翻译和分类规则不变。
输入是一个 JSON 对象，其中 feedbacks 数组中的每项包含 feedback_id、feedback、original_label。
请独立处理每条反馈，不要把其他反馈的事实、诉求或账号混入当前条目。
每项结果必须原样保留 feedback_id 字符串（包括前导零），不能漏项、重复、改写 ID 或输出未提供的 ID。
结果可以乱序，但每条输入都必须有且只有一个对应结果。
先检查每条原文是否含实际辱骂内容：只要包含，无论是否有有价值的建议或实质问题，整条返回 is_valid=false、exclusion_reason="含辱骂内容"、label=""、theme=""。
其余反馈独立判断 is_valid，必须是布尔值，不能返回字符串或数字。
有效反馈必须有合法模块，exclusion_reason 为空；无效反馈的 label、theme 为空。
不要判断或返回 is_suggestion，“是否建议”由后台按原标签确定，建议价值将在后续提炼阶段筛选。
无效原因从分类表 exclusion_reasons 选择；未配置时仅可用“缺少明确反馈对象或诉求”“内容无法理解”或“含辱骂内容”。
只返回以下 JSON 对象，不要 Markdown 或其他文字：
{"results":[{"feedback_id":"原样的字符串 ID","translation_zh":"简体中文翻译","label":"主模块或空字符串","theme":"固定主题或空字符串","is_valid":true,"exclusion_reason":""}]}
"""


def validate_result(result, config, original_label=""):
    if not isinstance(result, dict) or any(not isinstance(result.get(k), str) for k in ("translation_zh", "label", "theme", "exclusion_reason")):
        raise ValueError("模型必须返回翻译、主模块、主题和排除原因字符串。")
    if type(result.get("is_valid")) is not bool:
        raise ValueError("是否有效必须为布尔值。")
    translation, label, theme = (result[k].strip() for k in ("translation_zh", "label", "theme"))
    reason = result["exclusion_reason"].strip()
    if not translation:
        raise ValueError("模型翻译为空。")
    labels = config["schema"]["labels"]
    if result["is_valid"]:
        if label not in labels or label == "辱骂" or reason:
            raise ValueError("有效反馈的模块必须合法，排除原因必须为空。")
        if theme and theme not in [item["name"] for item in labels[label]["themes"]]:
            raise ValueError("模型子标签不属于所选主模块。")
    elif label or theme or reason not in config["schema"].get("exclusion_reasons", EXCLUSION_REASONS):
        raise ValueError("无效反馈须有合法排除原因，模块和主题为空。")
    return {"translation_zh": translation, "label": label, "theme": theme,
            "is_suggestion": result["is_valid"] and is_suggestion_label(original_label, config),
            "is_valid": result["is_valid"], "exclusion_reason": reason}


def cached_result(row, config):
    value = {field: row.get(column) for field, column in RESULT_COLUMNS.items()}
    for field in ("is_suggestion", "is_valid"):
        if value[field] not in {"是", "否"}:
            raise ValueError("旧结果缺少有效性或建议判断，需要按新规则处理。")
        value[field] = value[field] == "是"
    checked = validate_result(value, config, row.get("反馈标签", ""))
    if value["is_suggestion"] != checked["is_suggestion"]:
        raise ValueError("旧建议判断与原标签不一致，需要按新规则处理。")
    return checked


def fingerprint(row, config):
    content = ["source-suggestions-no-abuse-v3", config["game"], ai_client.model_name(config), config["classification_prompt"], BATCH_INSTRUCTIONS,
               config.get("suggestion_labels", ["建议"]),
               str(row["反馈ID"]), str(row["用户自己填写的内容"]), str(row["反馈标签"])]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()


def validate_batch(value, feedback_ids, config, source_labels=None):
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
            answers[feedback_id] = validate_result(items[0], config, (source_labels or {}).get(feedback_id, ""))
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
            cached_result(old, config)
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
    request_blocked = threading.Event()
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
                values = [("是" if value else "否") if type(value) is bool else value
                          for value in (answer[field] for field in RESULT_COLUMNS)]
                result.loc[indices[feedback_id], [*RESULT_COLUMNS.values(), "错误"]] = [*values, ""]
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
            sources = {item["feedback_id"]: item["original_label"] for item in payload["feedbacks"]}
            answers, invalid = validate_batch(value, remaining, config, sources)
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
            unresolved = {item["feedback_id"]: (message if isinstance(error, ai_client.ModelRequestError)
                                               else errors.get(item["feedback_id"], message))
                          for item in batch if item["feedback_id"] not in accepted}
            logger.warning("Feedback batch failed: model=%s records=%s reason=%s",
                           ai_client.model_name(config), len(unresolved), message)
            if isinstance(error, ai_client.ModelRequestError) and error.stops_processing:
                request_blocked.set()
            record({}, unresolved)

    save()
    progress(stage="processing", message="正在批量翻译与分类", done=done, total=len(result))
    size = config["concurrency"]
    with ThreadPoolExecutor(max_workers=size) as pool:
        for offset in range(0, len(batches), size):
            ai_client.check_stop(should_stop)
            futures = [pool.submit(process_batch, batch) for batch in batches[offset:offset + size]]
            for future in as_completed(futures):
                future.result()
            if request_blocked.is_set():
                break
    ai_client.check_stop(should_stop)
    failed = int(result["错误"].ne("").sum())
    if failed:
        reasons = result.loc[~result["错误"].isin(["", "待处理"]), "错误"].value_counts().head(2).index
        detail = " 失败原因：" + "；".join(reasons) if len(reasons) else ""
        raise ValueError(f"有 {failed} 条反馈尚未处理成功，未生成报告。{detail} 已完成的结果已保存，请修正问题后重试。")
    return result
