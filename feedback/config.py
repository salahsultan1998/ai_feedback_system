"""Configuration is read from the repository, never changed by web requests."""
import json
from pathlib import Path

from .report_language import REPORT_LANGUAGE_INSTRUCTIONS

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def list_games(root=ROOT):
    return read_json(Path(root) / "config/games.json")


def get_game(code, root=ROOT):
    for game in list_games(root):
        if game["code"] == code:
            return game
    raise ValueError("游戏不存在。")


def load_settings(root=ROOT):
    return read_json(Path(root) / "config/settings.json")


def load_config(code, root=ROOT):
    game = get_game(code, root)
    if not game["enabled"]:
        raise ValueError("此游戏的分类与提示词尚未配置完成。")
    folder = Path(root) / "config" / code
    config = {**load_settings(root), **read_json(folder / "settings.json")}
    config.update(game=code, name=game["name"], schema=read_json(folder / "labels.json"),
                  report=read_json(folder / "report_rules.json"))
    for key in ("concurrency", "timeout_seconds", "max_rows", "max_report_days",
                "classification_batch_size", "classification_batch_max_chars"):
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(f"配置 {key} 必须为正整数。")
    if config["max_retries"] < 0 or config["retry_delay"] < 0:
        raise ValueError("重试配置不可为负数。")
    report = config["report"]
    if report["ai_max_input_chars"] < 3000 or any(report[k] < 1 for k in
            ("max_suggestions", "max_issues_per_module", "max_evidence_ids")):
        raise ValueError("报告长度或数量限制无效。")
    if any(value < 0 for value in report["anomaly"].values()):
        raise ValueError("趋势阈值不可为负数。")
    labels = config["schema"].get("labels")
    if not labels or any(not isinstance(v.get("themes"), list) for v in labels.values()):
        raise ValueError("游戏分类配置无效。")
    reasons = config["schema"].get("exclusion_reasons")
    if reasons is not None and (not isinstance(reasons, list) or not reasons or
            any(not isinstance(reason, str) or not reason.strip() for reason in reasons) or len(set(reasons)) != len(reasons)):
        raise ValueError("语义排除原因必须为不重复的非空字符串列表。")
    for name in ("classification", "suggestions", "issues"):
        prompt = (folder / "prompts" / f"{name}.md").read_text(encoding="utf-8")
        if name == "classification" and "{{LABEL_SCHEMA}}" not in prompt:
            raise ValueError("分类提示词缺少 {{LABEL_SCHEMA}}。")
        config[name + "_prompt"] = prompt.replace("{{GAME_NAME}}", game["name"]).replace(
            "{{LABEL_SCHEMA}}", json.dumps(config["schema"], ensure_ascii=False))
        if name in {"suggestions", "issues"}:
            config[name + "_prompt"] += REPORT_LANGUAGE_INSTRUCTIONS
            terminology = config["schema"].get("terminology")
            if terminology:
                config[name + "_prompt"] += (
                    "\n游戏术语约定：下列中文键为本游戏使用的称呼，分析和合并时沿用，不自行改名。"
                    "结合定义保留主体、条件和实际发生状态；未确认的规则不补充推断。\n"
                    + json.dumps(terminology, ensure_ascii=False))
    return config
