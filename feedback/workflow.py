"""The single report workflow used by background jobs and tests."""
from .ai_client import check_stop
from .cleaning import clean_feedback, parse_dates
from .selection import select_feedback, period_mask
from .processing import process_feedback
from .statistics import build_statistics
from .analysis import analyze_report
from .exports.markdown import render_markdown
from .exports.excel import write_excel
from .storage import read_csv, atomic_json


def prepare_selection(folder, config, start, end, confirmed=False):
    raw = read_csv(folder / "rows.csv")
    cleaned, excluded = clean_feedback(raw, config)
    selected, info = select_feedback(raw, cleaned, start, end, config, confirmed)
    dates = parse_dates(excluded["反馈时间"])
    mask = period_mask(dates, info["periods"]["current"]) | period_mask(dates, info["periods"]["previous"])
    info.update(excluded_in_period=int(mask.sum()), invalid_date_count=int(dates.isna().sum()))
    return selected, info


def run_report(folder, config, start, end, confirmed, job_id, progress, should_stop=None):
    selected, info = prepare_selection(folder, config, start, end, confirmed)
    if not info["ready"]:
        raise ValueError("文件记录未覆盖两期边界。请补充数据；若边界日期确实无反馈，请确认完整导出后再生成。")
    processed = process_feedback(selected, config, folder / "processed.csv", progress, should_stop)
    check_stop(should_stop)
    progress(stage="statistics", message="正在计算同期趋势", done=0, total=0)
    snapshot = build_statistics(processed, info, config)
    info = snapshot["selection"]
    analysis = analyze_report(snapshot, config, folder / "analysis_cache", progress, should_stop)
    check_stop(should_stop)
    progress(stage="export", message="正在生成报告与翻译明细", done=0, total=0)
    markdown = render_markdown(snapshot, analysis, config)
    # A unique job folder means retries cannot replace a previously completed report.
    output = folder / "reports" / job_id
    output.mkdir(parents=True)
    write_excel(snapshot, config, output / "feedback_details.tmp.xlsx")
    (output / "report.tmp.md").write_text(markdown, encoding="utf-8")
    check_stop(should_stop)
    (output / "feedback_details.tmp.xlsx").replace(output / "feedback_details.xlsx")
    (output / "report.tmp.md").replace(output / "report.md")
    atomic_json(output / "config_snapshot.json", config)
    return {"total": snapshot["total"], "previous_total": snapshot["previous_total"],
            "suggestions": len(analysis["suggestions"]),
            "suggestion_feedback_count": snapshot["suggestion_feedback_count"], "selection": info}
