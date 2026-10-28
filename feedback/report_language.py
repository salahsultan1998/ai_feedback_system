"""Language rules for Chinese-facing report text, not source feedback data."""
import html
import unicodedata


REPORT_LANGUAGE_INSTRUCTIONS = """

报告语言要求（适用于提取、分析和合并的每个阶段）：
报告供不懂阿拉伯语的中国团队阅读。所有 text 正文必须使用简体中文，不得出现阿拉伯语文字。
玩法、活动、礼包、道具及玩家原话中的阿语名称都要用中文表达；能确定中文名称时使用对应中文名。
无法确认名称时，用“某玩法”“某活动”“某道具”或“名称不明”等中文描述保留已知诉求，不编造具体名称。
不得在括号、引号、示例或引用中附带阿语原词，不要中阿混写，也不要用 HTML 实体编码隐藏阿语。
数字使用 0–9，VIP、AI 等常见英文缩写和业务确认的英文术语（如 project）可以保留。feedback_ids 原样返回，不翻译或改写证据 ID。
这些语言规则不允许改变原意、删除重要诉求、猜测事实或编造结论。
"""


def validate_report_language(text):
    normalized = unicodedata.normalize("NFKC", html.unescape(text))
    if any("ARABIC" in unicodedata.name(char, "") for char in normalized):
        raise ValueError("报告文字含阿拉伯语字符，请改为中文表述，不要保留阿语原词。")
    return text
