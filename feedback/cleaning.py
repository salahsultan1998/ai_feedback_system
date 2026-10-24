"""Exclude invalid feedback while retaining an explanation for every exclusion."""
import pandas as pd


def parse_dates(values):
    dates = pd.to_datetime(values, errors="coerce", format="mixed")
    try:
        if dates.dt.tz is not None:
            dates = dates.dt.tz_localize(None)
        return dates
    except AttributeError:
        raise ValueError("反馈时间包含混合时区，请统一为源系统本地时间后上传。") from None


def clean_feedback(raw, config):
    data = raw.copy().fillna("")
    for column in config["column_mapping"]:
        data[column] = data[column].astype(str).str.strip()
    reasons = pd.Series("", index=data.index)

    def exclude(mask, reason):
        reasons.loc[mask & reasons.eq("")] = reason

    exclude(data["反馈标签"].str.casefold().isin({s.casefold() for s in config["ignored_labels"]}), "源标签排除")
    exclude(data["反馈ID"].eq(""), "反馈ID为空")
    exclude(data["用户自己填写的内容"].eq(""), "反馈内容为空")
    dates = parse_dates(data["反馈时间"])
    exclude(dates.isna(), "反馈时间无效")
    valid = reasons.eq("")
    duplicate = data.loc[valid, "反馈ID"].duplicated(keep="first")
    exclude(data.index.isin(duplicate[duplicate].index), "反馈ID重复")
    excluded = data.loc[reasons.ne("")].copy()
    excluded["排除原因"] = reasons.loc[reasons.ne("")]
    data["反馈时间"] = dates
    return data.loc[reasons.eq("")].reset_index(drop=True), excluded.reset_index(drop=True)
