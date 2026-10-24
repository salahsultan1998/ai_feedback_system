"""Translate selected records and checkpoint after each bounded batch."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json

import pandas as pd

from . import ai_client
from .storage import atomic_csv, read_csv

OUTPUT = ["中文翻译", "最终标签", "具体主题", "错误", "处理指纹"]


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


def process_feedback(rows, config, path, progress, should_stop=None):
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

    def save():
        # Retain previous work for another date selection in this same upload.
        historical = [dict(value, 反馈ID=fid) for fid, value in saved.items() if fid not in set(result["反馈ID"])]
        atomic_csv(path, pd.concat([pd.DataFrame(historical), result], ignore_index=True))

    def one(index):
        row = result.loc[index]
        try:
            answer = ai_client.request_json(config["classification_prompt"],
                {"feedback": row["用户自己填写的内容"], "original_label": row["反馈标签"]},
                config, lambda value: validate_result(value, config), should_stop)
            return [answer["translation_zh"], answer["label"], answer["theme"], ""]
        except InterruptedError:
            return None
        except Exception as error:
            return ["", "", "", str(error) if isinstance(error, ValueError) else type(error).__name__]

    done = len(result) - len(pending)
    save()
    progress(stage="processing", message="正在翻译与分类", done=done, total=len(result))
    size = config["concurrency"]
    with ThreadPoolExecutor(max_workers=size) as pool:
        for offset in range(0, len(pending), size):
            ai_client.check_stop(should_stop)
            batch = pending[offset:offset + size]
            for index, answer in zip(batch, pool.map(one, batch)):
                if answer is not None:
                    result.loc[index, OUTPUT[:4]] = answer
                    done += 1
            save()
            progress(stage="processing", message="正在翻译与分类", done=done, total=len(result))
    ai_client.check_stop(should_stop)
    failed = int(result["错误"].ne("").sum())
    if failed:
        raise ValueError(f"有 {failed} 条反馈尚未处理成功，未生成报告。已完成的结果已保存，请重试。")
    return result
