"""Source-controlled suggestion candidates and report exclusion rules."""

ABUSE_REASON = "含辱骂内容"


def is_suggestion_label(label, config):
    return str(label).strip() in config.get("suggestion_labels", ["建议"])


def candidate_label_column(config):
    return "具体主题" if config.get("classification_source") == "source_label" else "反馈标签"


def suggestion_mask(rows, config):
    if config.get("candidate_source") == "ai":
        return rows["是否建议"].eq("是")
    return rows[candidate_label_column(config)].astype(str).str.strip().isin(config.get("suggestion_labels", ["建议"]))


def bug_mask(rows, config):
    if config.get("candidate_source") == "ai":
        return rows["是否疑似BUG"].eq("是")
    return rows[candidate_label_column(config)].astype(str).str.strip().isin(config.get("bug_labels", ["BUG"]))


def abuse_mask(rows):
    # The retired module is also blocked if an older/inconsistent row is supplied.
    return rows["排除原因"].eq(ABUSE_REASON) | rows["最终标签"].eq("辱骂")
