"""Human-readable Chinese report, shared by browser preview and download."""
import html


def safe(text):
    value = html.escape(str(text).replace("\r", " ").replace("\n", " "), quote=False)
    for char in "|`\\[]*!_":
        value = value.replace(char, f"&#{ord(char)};")
    return value


def change(current, previous):
    if not previous:
        return "新增（上周同期为 0）" if current else "持平"
    delta = current - previous
    return "持平（0.0%）" if not delta else f"{'增加' if delta > 0 else '减少'} {abs(delta):,} 条（{abs(delta / previous):.1%}）"


def render_markdown(snapshot, analysis, config):
    selection = snapshot["selection"]
    current, previous = (selection["periods"][key] for key in ("current", "previous"))
    lines = [f"# {safe(config['name'])} 玩家反馈报告", "",
        f"报告期间：{current['start']} 至 {current['end']}（含首尾日期）",
        f"对比期间：{previous['start']} 至 {previous['end']}（上周相同日期）", "",
        "## 反馈概况", "",
        f"本期有效反馈：{snapshot['total']:,} 条；上周同期：{snapshot['previous_total']:,} 条。",
        f"变化：{change(snapshot['total'], snapshot['previous_total'])}。", "",
        "## 有价值的玩家建议", ""]
    if not analysis["suggestions"]:
        lines.append("本期未发现明确、具体的产品改进建议。")
    for index, item in enumerate(analysis["suggestions"], 1):
        lines.append(f"{index}. {safe(item['text'])}（反馈 ID：{safe('、'.join(item['feedback_ids']))}）")
    lines += ["", "## 按主标签统计", "",
        "| 反馈模块 | 本期数量 | 本期占比 | 上周同期数量 | 变化 | 重点问题分析 |",
        "| :--- | ---: | ---: | ---: | :--- | :--- |"]
    for module in snapshot["modules"]:
        notes = []
        for issue in analysis["issues"].get(module["label"], []):
            notes.append(f"{safe(issue['theme'])}（{issue['count']} 条）：{safe(issue['text'])}（反馈 ID：{safe('、'.join(issue['feedback_ids']))}）")
        if module["flagged"] and not notes:
            raise ValueError("重点模块缺少 AI 分析，报告未导出。")
        remaining = module["count"] - sum(item["count"] for item in analysis["issues"].get(module["label"], []))
        if notes and remaining:
            notes.append(f"另有 {remaining} 条，详见反馈明细。")
        lines.append(f"| {safe(module['label'])} | {module['count']} | {module['share']:.1%} | {module['previous']} | {change(module['count'], module['previous'])} | {'<br>'.join(notes) or '—'} |")
    # lines += ["", "[下载完整翻译明细](feedback_details.xlsx)", "", "## 统计说明", "",
    #     "- 每条有效反馈只计入一个主标签；占比以本期有效反馈数为分母。",
    #     "- 对比期间为报告日期向前移动七天；超过七天的期间可能重叠，处理时会去重。",
    #     f"- 两期合计选择 {selection['selected_count']} 条有效记录；其余有效记录未进入处理。",
    #     f"- 两期范围内排除 {selection.get('excluded_in_period', 0)} 条记录（空内容、无效 ID、重复记录或忽略标签）；上传文件另有 {selection.get('invalid_date_count', 0)} 条无效日期记录无法归属期间。",
    #     "- 日期覆盖范围根据文件记录估计，不能证明源系统导出完整。" + ("本次已由上传者确认覆盖两期。" if selection["confirmed"] else ""),
    #     "- 未标记重点不代表没有问题；玩家描述与 AI 概述不代表已确认的产品故障。"]
    return "\n".join(lines) + "\n"
