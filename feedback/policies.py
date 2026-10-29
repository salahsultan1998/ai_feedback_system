"""Source-controlled suggestion candidates and report exclusion rules."""

ABUSE_REASON = "含辱骂内容"


def is_suggestion_label(label, config):
    return str(label).strip() in config.get("suggestion_labels", ["建议"])


def suggestion_mask(rows, config):
    return rows["反馈标签"].map(lambda label: is_suggestion_label(label, config))


def abuse_mask(rows):
    # The retired module is also blocked if an older/inconsistent row is supplied.
    return rows["排除原因"].eq(ABUSE_REASON) | rows["最终标签"].eq("辱骂")
