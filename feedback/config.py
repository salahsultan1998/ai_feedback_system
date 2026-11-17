"""Read repository defaults and assemble the prompts used by a report."""
import json
import math
from pathlib import Path

from .report_language import REPORT_LANGUAGE_INSTRUCTIONS
from .taxonomy import validate_labels

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_LIMITS = {
    "classification_batch_size": (1, 500), "classification_batch_max_chars": (1, 250000),
    "concurrency": (1, 256), "timeout_seconds": (1, 600), "max_retries": (0, 10),
    "retry_delay": (0, 60), "max_report_days": (1, 366), "max_rows": (1, 250000),
    "preview_page_size": (1, 1000), "max_expanded_xlsx_mb": (1, 128),
}
EDITABLE_SETTINGS = (*RUNTIME_LIMITS, "column_mapping", "detail_columns", "ignored_labels",
                     "suggestion_labels", "bug_labels", "model", "csv_encoding")


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


def read_prompt_templates(folder):
    names = ["classification", "suggestions", "issues"]
    if (folder / "prompts" / "bugs.md").exists():
        names.append("bugs")
    return {name: (folder / "prompts" / f"{name}.md").read_text(encoding="utf-8") for name in names}


def validate_prompt_template(name, prompt):
    if name == "classification" and "{{LABEL_SCHEMA}}" not in prompt:
        raise ValueError("分类提示词缺少 {{LABEL_SCHEMA}}。")


def project_config_defaults(code, root=ROOT):
    from .exports.excel import COLUMNS

    folder = Path(root) / "config" / code
    settings = {"suggestion_labels": ["建议"], "bug_labels": ["BUG"], "detail_columns": COLUMNS,
                **load_settings(root), **read_json(folder / "settings.json")}
    return {"settings": {key: settings[key] for key in EDITABLE_SETTINGS},
            "report": read_json(folder / "report_rules.json"), "schema": read_json(folder / "labels.json")}


def validate_project_config(code, root, value, default):
    if (not isinstance(value, dict) or set(value) != set(default)
            or any(not isinstance(value[key], dict) for key in default)):
        raise ValueError("请提供完整的项目配置。")
    settings, report, schema = (value[key] for key in ("settings", "report", "schema"))
    if set(settings) != set(default["settings"]) or set(report) != set(default["report"]):
        raise ValueError("配置项缺失或包含不可编辑的设置。")
    if not set(schema) <= set(default["schema"]) | {"terminology", "exclusion_reasons"}:
        raise ValueError("标签配置包含不支持的字段。")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("配置包含无效的文本、数字或嵌套数据。") from None
    if len(encoded) > 512 * 1024:
        raise ValueError("项目配置不能超过 512 KB。")

    for name, (minimum, maximum) in RUNTIME_LIMITS.items():
        setting = settings[name]
        maximum = max(maximum, default["settings"][name])
        if type(setting) is not int or not minimum <= setting <= maximum:
            raise ValueError(f"{name} 必须是 {minimum} 到 {maximum} 之间的整数。")
    if not isinstance(settings["model"], str) or not settings["model"].strip():
        raise ValueError("模型名称不能为空。")
    try:
        if not isinstance(settings["csv_encoding"], str):
            raise LookupError
        "test".encode(settings["csv_encoding"])
    except (UnicodeError, LookupError):
        raise ValueError("CSV 编码无效，例如可使用 utf-8-sig。") from None

    def nonempty_names(values):
        return (isinstance(values, list) and all(isinstance(item, str) and item.strip() and item == item.strip()
                for item in values) and len(values) == len(set(values)))

    for name in ("column_mapping", "detail_columns"):
        mapping = settings[name]
        if (not isinstance(mapping, dict) or set(mapping) != set(default["settings"][name])
                or not nonempty_names(list(mapping.values()))):
            raise ValueError(f"{name} 必须保留现有字段，列名称不能留空或重复。")
    if any(settings["detail_columns"].get(field) != title for field, title in (
        ("反馈时间", "反馈时间"), ("反馈ID", "反馈ID"), ("用户自己填写的内容", "原文"), ("中文翻译", "中文翻译")
    )) or "统计期间" in settings["detail_columns"].values():
        raise ValueError("明细的反馈时间、反馈 ID、原文和中文翻译列名固定，统计期间为保留列名。")
    if not nonempty_names(settings["ignored_labels"]):
        raise ValueError("排除标签必须是不重复的非空字符串列表。")
    for name in set(default["report"]) - {"module_growth_thresholds"}:
        if type(report[name]) is not int:
            raise ValueError(f"{name} 必须为整数。")
    terminology = schema.get("terminology", {})
    if not isinstance(terminology, dict) or any(not name.strip() or not isinstance(term, dict) for name, term in terminology.items()):
        raise ValueError("术语应由非空术语名称及其详细说明组成。")

    effective = load_config(code, root, project_overrides=value)
    if "含辱骂内容" not in schema.get("exclusion_reasons", ["含辱骂内容"]):
        raise ValueError("语义排除原因必须保留「含辱骂内容」。")
    if effective.get("classification_source") == "source_label":
        themes = effective["source_label_modules"]
        if themes.get("信息不完整") != "其他":
            raise ValueError("请在「其他」下保留标准二级标签「信息不完整」。")
        if any(label not in themes for key in ("suggestion_labels", "bug_labels") for label in settings[key]):
            raise ValueError("建议和 BUG 标签必须存在于标准二级标签表中。")


def load_config(code, root=ROOT, prompt_overrides=None, project_overrides=None):
    game = get_game(code, root)
    if not game["enabled"]:
        raise ValueError("此游戏的分类与提示词尚未配置完成。")
    folder = Path(root) / "config" / code
    config = {**load_settings(root), **read_json(folder / "settings.json")}
    config.update(game=code, name=game["name"], schema=read_json(folder / "labels.json"),
                  report=read_json(folder / "report_rules.json"))
    if project_overrides is not None:
        config.update({key: value for key, value in project_overrides["settings"].items() if key in EDITABLE_SETTINGS})
        config.update(schema=project_overrides["schema"], report=project_overrides["report"])
    if config.get("classification_source", "ai") not in {"ai", "source_label"}:
        raise ValueError("classification_source 必须为 ai 或 source_label。")
    if config.get("candidate_source", "source_label") not in {"source_label", "ai"}:
        raise ValueError("candidate_source 必须为 source_label 或 ai。")
    for key in ("concurrency", "timeout_seconds", "max_rows", "max_report_days",
                "classification_batch_size", "classification_batch_max_chars"):
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(f"配置 {key} 必须为正整数。")
    if config["max_retries"] < 0 or config["retry_delay"] < 0:
        raise ValueError("重试配置不可为负数。")
    for key, default in (("suggestion_labels", ["建议"]), ("bug_labels", ["BUG"])):
        source_labels = config.get(key, default)
        if (not isinstance(source_labels, list) or not source_labels or
                any(not isinstance(label, str) or not label.strip() or label != label.strip() for label in source_labels) or
                len(set(source_labels)) != len(source_labels)):
            raise ValueError(f"{key} 必须是不重复的非空源标签列表。")
    report = config["report"]
    if report["ai_max_input_chars"] < 3000 or any(report[k] < 1 for k in
            ("max_suggestions", "max_issues_per_module", "max_evidence_ids")):
        raise ValueError("报告长度或数量限制无效。")
    labels = config["schema"].get("labels")
    validate_labels(labels)
    if config.get("classification_source") == "source_label":
        if "其他" not in labels:
            raise ValueError("按源标签分类时必须配置一级类别「其他」。")
        source_label_modules = {}
        for module, group in labels.items():
            for theme in group["themes"]:
                label = theme["name"]
                if label in source_label_modules:
                    raise ValueError(f"源标签「{label}」只能对应一个一级类别。")
                source_label_modules[label] = module
        config["source_label_modules"] = source_label_modules
    thresholds = report.get("module_growth_thresholds")
    if not isinstance(thresholds, dict) or set(thresholds) != set(labels):
        raise ValueError("module_growth_thresholds 必须为每个 I 类别配置增长率，类别名称须与分类表一致。")
    for label, value in thresholds.items():
        if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)) or value < 0:
            raise ValueError(f"I 类别「{label}」的增长率阈值必须为非负有限数字，0.1 表示 10%。")
    reasons = config["schema"].get("exclusion_reasons")
    if reasons is not None and (not isinstance(reasons, list) or not reasons or
            any(not isinstance(reason, str) or not reason.strip() for reason in reasons) or len(set(reasons)) != len(reasons)):
        raise ValueError("语义排除原因必须为不重复的非空字符串列表。")
    for name, default in read_prompt_templates(folder).items():
        prompt = (prompt_overrides or {}).get(name, default)
        validate_prompt_template(name, prompt)
        config[name + "_prompt"] = prompt.replace("{{GAME_NAME}}", game["name"]).replace(
            "{{LABEL_SCHEMA}}", json.dumps(config["schema"], ensure_ascii=False))
        if name in {"suggestions", "issues", "bugs"}:
            instructions = REPORT_LANGUAGE_INSTRUCTIONS
            if code == "okey":
                instructions = instructions.replace("阿拉伯语", "土耳其语").replace("阿语", "土语").replace("中阿混写", "中土混写")
            config[name + "_prompt"] += instructions
            terminology = config["schema"].get("terminology")
            if terminology:
                config[name + "_prompt"] += (
                    "\n游戏术语约定：下列中文键为本游戏使用的称呼，分析和合并时沿用，不自行改名。"
                    "结合定义保留主体、条件和实际发生状态；未确认的规则不补充推断。\n"
                    + json.dumps(terminology, ensure_ascii=False))
    return config
