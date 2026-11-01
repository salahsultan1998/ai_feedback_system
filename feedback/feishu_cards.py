# """Render report Markdown as mixed text/native-table Feishu cards."""
# import html
# import re

# MAX_TABLES = 5
# DEFAULT_COLUMN_WIDTHS = {"反馈模块": "12%", "本期数量": "8%", "本期占比": "8%",
#                          "上周同期数量": "12%", "变化": "20%", "重点问题分析": "40%"}


# def column_widths(config):
#     settings = config.get("feishu", {}).get("card", {})
#     if not isinstance(settings, dict):
#         raise ValueError("飞书 card 配置必须为对象。")
#     overrides = settings.get("table_column_widths", {})
#     if not isinstance(overrides, dict):
#         raise ValueError("飞书 table_column_widths 必须为列名与列宽的对应表。")
#     for name, width in overrides.items():
#         if not isinstance(name, str) or not name.strip() or name != name.strip() or not isinstance(width, str):
#             raise ValueError("飞书表格列名和列宽必须为非空字符串。")
#         pixels = re.fullmatch(r"([1-9]\d*)px", width)
#         percent = re.fullmatch(r"([1-9]\d*)%", width)
#         if not (width == "auto" or pixels and 80 <= int(pixels[1]) <= 600 or percent and 1 <= int(percent[1]) <= 100):
#             raise ValueError(f"飞书表格「{name}」列宽须为 auto、80–600px 或 1%–100%。")
#     return {**DEFAULT_COLUMN_WIDTHS, **overrides}


# def split_row(line):
#     """Report cells escape literal pipes; also accept Markdown's backslash escape."""
#     text = line.strip()
#     if "|" not in text:
#         return None
#     cells, cell, escaped = [], "", False
#     for char in text:
#         if char == "|" and not escaped:
#             cells.append(cell.strip())
#             cell = ""
#         else:
#             cell += char
#         escaped = not escaped if char == "\\" else False
#     cells.append(cell.strip())
#     if text.startswith("|"):
#         cells.pop(0)
#     if text.endswith("|") and cells[-1] == "":
#         cells.pop()
#     return cells


# def table_header(lines, index):
#     if index + 1 >= len(lines):
#         return None
#     names, separators = split_row(lines[index]), split_row(lines[index + 1])
#     if not names or not separators or len(names) != len(separators):
#         return None
#     if not all(re.fullmatch(r":?-+:?", cell) for cell in separators):
#         return None
#     return names, separators


# def native_table(names, separators, rows, widths):
#     columns = []
#     for index, (name, separator) in enumerate(zip(names, separators)):
#         name = html.unescape(name).replace("\\|", "|")
#         alignment = "center" if separator.startswith(":") and separator.endswith(":") else "right" if separator.endswith(":") else "left"
#         columns.append({"name": f"col_{index}", "display_name": name, "data_type": "markdown",
#                         "width": widths.get(name, "auto"), "vertical_align": "top", "horizontal_align": alignment})
#     return {"tag": "table", "page_size": min(10, len(rows)), "row_height": "auto", "row_max_height": "999px",
#             "freeze_first_column": True, "header_style": {"bold": True, "background_style": "grey"},
#             "columns": columns,
#             "rows": [{f"col_{index}": re.sub(r"<br\s*/?>", "\n", cell, flags=re.I)
#                       for index, cell in enumerate(row)} for row in rows]}


# def card_elements(text, widths):
#     lines, elements, pending = text.splitlines(), [], []
#     index, fence = 0, None

#     def flush():
#         content = "\n".join(pending).strip()
#         if content:
#             elements.append({"tag": "markdown", "content": re.sub(r"<br\s*/?>", "\n", content, flags=re.I)})
#         pending.clear()

#     while index < len(lines):
#         marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", lines[index])
#         if marker:
#             if fence is None:
#                 fence = marker[1]
#             elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
#                 fence = None
#             pending.append(lines[index])
#             index += 1
#             continue
#         header = table_header(lines, index) if fence is None else None
#         if header:
#             names, separators = header
#             end, rows = index + 2, []
#             while end < len(lines) and lines[end].strip() and not table_header(lines, end):
#                 cells = split_row(lines[end])
#                 if cells is None or len(cells) != len(names):
#                     break
#                 rows.append(cells)
#                 end += 1
#             if rows and len(names) <= 50:
#                 flush()
#                 elements.append(native_table(names, separators, rows, widths))
#                 index = end
#                 continue
#         pending.append(lines[index])
#         index += 1
#     flush()
#     return elements


# def card_payload(title, text, widths=None):
#     widths = DEFAULT_COLUMN_WIDTHS if widths is None else widths
#     return {"msg_type": "interactive", "card": {
#         "schema": "2.0",
#         "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
#         "body": {"elements": card_elements(text, widths)}}}
