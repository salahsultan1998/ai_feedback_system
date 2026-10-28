"""Two translated detail sheets: selected period and same dates last week."""
import math
import unicodedata

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter

COLUMNS = {"最终标签": "反馈模块", "具体主题": "反馈系统", "反馈ID": "反馈ID", "用户ID": "用户ID",
           "VIP等级": "VIP等级", "反馈时间": "反馈时间", "中文翻译": "中文翻译",
           "用户自己填写的内容": "原文", "反馈标签": "原标签", "用户主动选择的标签": "用户主动选择的标签", "附加信息": "附加信息",
           "是否建议": "是否建议"}
EXCLUDED_COLUMNS = {"反馈ID": "反馈ID", "用户ID": "用户ID", "反馈时间": "反馈时间", "中文翻译": "中文翻译",
                    "用户自己填写的内容": "原文", "反馈标签": "原标签", "排除原因": "排除原因"}


def write_excel(snapshot, config, path):
    book = Workbook()
    book.remove(book.active)
    groups = [("current", "本期反馈明细"), ("previous", "上周同期反馈明细")]
    if len(snapshot.get("excluded", [])):
        groups.append(("excluded", "排除反馈明细"))
    for key, title in groups:
        columns = EXCLUDED_COLUMNS if key == "excluded" else COLUMNS
        rows = snapshot[key].sort_values(["最终标签", "具体主题", "反馈时间", "反馈ID"])
        sheet = book.create_sheet(title)
        sheet.sheet_view.showGridLines = False
        period = snapshot["selection"]["periods"]["current" if key == "excluded" else key]
        sheet["A1"] = f"{config['name']} · {title}"
        sheet["A1"].font = Font(size=16, bold=True, color="0F172A")
        sheet["A2"] = (f"本期及上周同期 · {len(rows)} 条语义排除反馈，不计入统计" if key == "excluded" else
                       f"{period['start']} 至 {period['end']}（含首尾日期） · {len(rows)} 条")
        sheet["A2"].font = Font(size=11, color="64748B")
        widths = [20, 20, 24, 64, 64, 22, 40] if key == "excluded" else [20, 24, 20, 20, 12, 24, 64, 64, 22, 24, 40, 12]
        for column, (name, width) in enumerate(zip(columns.values(), widths), 1):
            sheet.column_dimensions[get_column_letter(column)].width = width
            cell = sheet.cell(4, column, name)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="2563EB")
            cell.alignment = Alignment(vertical="center")
        sheet.row_dimensions[4].height = 28
        for index, row in enumerate(rows.to_dict("records"), 5):
            lines = 1
            for column, name in enumerate(columns, 1):
                value = row.get(name, "")
                if name == "反馈时间":
                    value = pd.Timestamp(value).to_pydatetime()
                if isinstance(value, str) and len(value) > 32767:
                    raise ValueError("反馈文字超过 Excel 单元格限制，未截断原文，请检查数据。")
                cell = sheet.cell(index, column, value)
                if isinstance(value, str):
                    cell.data_type = "s"
                    length = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in value)
                    lines = max(lines, math.ceil(length / widths[column-1]), len(value.splitlines()))
                cell.font = Font(size=11, color="0F172A")
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if name in {"反馈ID", "用户ID"}:
                    cell.number_format = "@"
                if name == "反馈时间":
                    cell.number_format = "yyyy-mm-dd hh:mm"
            sheet.row_dimensions[index].height = min(409, max(30, lines * 16 + 8))
        if len(rows):
            table = Table(displayName=f"Details_{key}", ref=f"A4:{get_column_letter(len(columns))}{4 + len(rows)}")
            table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
            sheet.add_table(table)
        sheet.freeze_panes = "C5" if key == "excluded" else "D5"
        sheet.print_title_rows = "1:4"
        sheet.page_setup.orientation = "landscape"
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
    book.save(path)
    book.close()
