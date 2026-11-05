"""Human-readable Chinese report, shared by browser preview and download."""
import html

from ..report_language import validate_report_language


def safe(text):
    value = html.escape(str(text).replace("\r", " ").replace("\n", " "), quote=False)
    for char in "|`\\[]*!_":
        value = value.replace(char, f"&#{ord(char)};")
    return value


def change(current, previous):
    if not previous:
        return "新增（上周同期为 0）" if current else "持平"
    delta = current - previous
    return "持平（0.0%）" if not delta else f"{'↑' if delta > 0 else '↓'} {abs(delta):,} 条（{delta / previous:+.1%}）"


def render_markdown(snapshot, analysis, config):
    current, previous = (snapshot["selection"]["periods"][key] for key in ("current", "previous"))
    lines = [f"# {safe(config['name'])} 用户反馈报告", "",
        f"报告期间：{current['start']} 至 {current['end']}（含首尾日期）",
        f"对比期间：{previous['start']} 至 {previous['end']}（上周相同日期）", "",
        "## 反馈概况", "",
        f"本期有效反馈：{snapshot['total']:,} 条；上周同期：{snapshot['previous_total']:,} 条。"
        f"变化：{change(snapshot['total'], snapshot['previous_total'])}。", "",
        "## 有价值的玩家建议", ""]
    if not analysis["suggestions"]:
        lines.append("本期未发现明确、具体的产品改进建议。")
    feedback_ids_in_current = ({str(fid) for fid in snapshot["current"]["反馈ID"]}
                               if analysis["suggestions"] or analysis.get("bugs") else set())
    for index, item in enumerate(analysis["suggestions"], 1):
        if any(fid not in feedback_ids_in_current for fid in item["feedback_ids"]):
            raise ValueError("建议引用了不属于本期有效反馈的记录。")
        feedback_ids = "、".join(dict.fromkeys(item["feedback_ids"]))
        lines.append(f"{index}. {safe(item['text'])}（反馈 ID：{safe(feedback_ids)}）")
    if "bugs" in analysis:
        from ..bug_review import render_section
        if any(fid not in feedback_ids_in_current for item in analysis["bugs"] for fid in item["feedback_ids"]):
            raise ValueError("BUG 引用了不属于本期有效反馈的记录。")
        lines += ["", render_section(analysis["bugs"]).strip()]
    lines += ["", "## 反馈统计", "",
        "| 反馈模块 | 本期数量 | 上周同期数量 | 变化 | 重点问题分析 |",
        "| :--- | :--- | :--- | :--- | :--- |"]
    for module in snapshot["modules"]:
        issues = analysis["issues"].get(module["label"], [])
        notes = []
        for issue in issues:
            notes.append(f"{safe(issue['theme'])}（{issue['count']} 条）：{safe(issue['text'])}（反馈 ID：{safe('、'.join(issue['feedback_ids']))}）")
        if module["flagged"] and not notes:
            raise ValueError("重点模块缺少 AI 分析，报告未导出。")
        remaining = module["count"] - sum(item["count"] for item in issues)
        if notes and remaining:
            notes.append(f"另有 {remaining} 条，详见反馈明细。")
        module_change = change(module["count"], module["previous"]).replace(" 条", "")
        lines.append(f"| {safe(module['label'])} | {module['count']}（{module['share']:.1%}） | {module['previous']} | {module_change} | {'<br>'.join(notes) or '—'} |")
    return validate_report_language("\n".join(lines) + "\n")
