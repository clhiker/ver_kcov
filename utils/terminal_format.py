"""
终端文本排版工具。
"""
import unicodedata
from typing import Iterable, List, Sequence, Tuple


def display_width(text: object) -> int:
    """计算字符串在等宽终端中的显示宽度。"""
    value = str(text)
    width = 0
    for char in value:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("F", "W") else 1
    return width


def truncate_display(text: object, max_width: int) -> str:
    """按终端显示宽度截断字符串。"""
    value = str(text)
    if max_width <= 0:
        return ""
    if display_width(value) <= max_width:
        return value

    if max_width == 1:
        return "…"

    result: List[str] = []
    current_width = 0
    target_width = max_width - 1
    for char in value:
        char_width = 0 if unicodedata.combining(char) else (
            2 if unicodedata.east_asian_width(char) in ("F", "W") else 1
        )
        if current_width + char_width > target_width:
            break
        result.append(char)
        current_width += char_width

    return "".join(result) + "…"


def pad_display(text: object, width: int, align: str = "left") -> str:
    """按终端显示宽度填充字符串。"""
    value = str(text)
    current_width = display_width(value)
    if current_width > width:
        value = truncate_display(value, width)
        current_width = display_width(value)

    padding = max(0, width - current_width)
    if align == "right":
        return (" " * padding) + value
    if align == "center":
        left = padding // 2
        right = padding - left
        return (" " * left) + value + (" " * right)
    return value + (" " * padding)


def format_table_row(columns: Sequence[Tuple[object, int, str]]) -> str:
    """格式化一行表格。"""
    return " ".join(pad_display(value, width, align) for value, width, align in columns)
