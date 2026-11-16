"""长条款概述：表格里放不下招标文件原文时，压缩为完整的短句，不做半句截断。"""

from __future__ import annotations

import re

_NUMBERING = re.compile(r"^\s*(?:[（(]\d+[)）]|\d+(?:\.\d+)*[.、]?)\s*")
_PARENTHESES = re.compile(r"[（(][^（()）]*[)）]")
_SENTENCE_END = "。；;"
_CLAUSE_END = "，,、"


def strip_numbering(text: str) -> str:
    return _NUMBERING.sub("", text or "").strip()


def condense_text(text: str, limit: int = 90) -> str:
    """把 ``text`` 压缩到 ``limit`` 字以内：去括注 → 保留靠前的完整句 → 在分句处收尾。"""
    value = re.sub(r"\s+", "", strip_numbering(text))
    if len(value) <= limit:
        return value
    value = _PARENTHESES.sub("", value)
    if len(value) <= limit:
        return value
    kept = ""
    for sentence in re.findall(rf"[^{_SENTENCE_END}]+[{_SENTENCE_END}]?", value):
        if len(kept) + len(sentence) > limit:
            break
        kept += sentence
    if kept:
        return kept.rstrip("；;") + ("" if kept.endswith("。") else "。")
    head = value[:limit]
    cut = max(head.rfind(mark) for mark in _CLAUSE_END)
    return (head[:cut] if cut > limit // 3 else head).rstrip(_CLAUSE_END) + "等。"
