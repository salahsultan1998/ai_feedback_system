"""Read source columns as text; source uploads are preserved unchanged."""
from pathlib import Path
import zipfile

import pandas as pd

REQUIRED = ["反馈ID", "用户自己填写的内容", "反馈时间"]


def read_feedback(path, config):
    suffix = Path(path).suffix.lower()
    limit = config["max_rows"]
    if suffix == ".csv":
        try:
            data = pd.read_csv(path, dtype=str, keep_default_na=False,
                               encoding=config["csv_encoding"], nrows=limit + 1)
        except (UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError):
            raise ValueError("CSV 无法读取，请检查格式和编码（默认 UTF-8）。") from None
    elif suffix == ".xlsx":
        try:
            with zipfile.ZipFile(path) as archive:
                if sum(i.file_size for i in archive.infolist()) > config["max_expanded_xlsx_mb"] * 1024 ** 2:
                    raise ValueError("Excel 解压后超过大小限制。")
            data = pd.read_excel(path, sheet_name=0, dtype=str, keep_default_na=False,
                                 engine="openpyxl", nrows=limit + 1)
        except (zipfile.BadZipFile, KeyError):
            raise ValueError("Excel 文件损坏或格式无效，请重新导出。") from None
    else:
        raise ValueError("请选择 CSV 或 .xlsx 文件。")
    if len(data) > limit:
        raise ValueError(f"每次最多上传 {limit:,} 行。")
    if data.empty:
        raise ValueError("上传文件没有数据。")
    data.columns = data.columns.astype(str).str.strip()
    mapping = config["column_mapping"]
    data = data.rename(columns={source: standard for standard, source in mapping.items()})
    if data.columns.duplicated().any():
        raise ValueError("存在重复列名，请检查列映射。")
    missing = [c for c in REQUIRED if c not in data]
    if missing:
        raise ValueError("缺少必要列：" + "、".join(missing))
    for name in mapping:
        if name not in data:
            data[name] = ""
    # Never trust translated fields in an uploaded file as successful processing.
    return data[list(mapping)].fillna("")
