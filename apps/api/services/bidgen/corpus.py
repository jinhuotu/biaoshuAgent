"""历史标书段落库：导入公司往期投标文件，按评分点检索同类段落复用（BM25，不调用模型）。

导入：Word / PDF 按标题切成章节，记下原文件中的项目名称、招标人、投标人，复用时替换为本项目信息。
检索：汉字二元组 BM25，标题权重加倍；只返回与评分点足够相关的章节。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from api.services.bidgen.docx_reader import DocBlock, read_docx_blocks
from api.services.bidgen.pdf_reader import read_pdf_blocks

CORPUS_SUFFIXES = (".docx", ".pdf")
_HEADING = re.compile(r"^\s*(?:第[一二三四五六七八九十]+[章节部分]|[一二三四五六七八九十]+[、．.]|[（(][一二三四五六七八九十]+[）)]|\d+(?:\.\d+){0,3}\s*[、．.]?)\s*\S")
_MIN_CHARS, _MAX_CHARS = 60, 3000
_K1, _B = 1.5, 0.75


def _grams(text: str) -> list[str]:
    value = re.sub(r"[^一-鿿A-Za-z0-9]", "", text)
    return [value[i:i + 2] for i in range(len(value) - 1)]


def _is_heading(block: DocBlock) -> bool:
    text = block.text.strip()
    return bool(block.level) or (len(text) <= 30 and bool(_HEADING.match(text)) and not re.search(r"[，。；：:]$", text))


def _source_facts(blocks: list[DocBlock]) -> dict:
    """原标书中的项目名称、招标（采购）人、投标人，复用时替换。"""
    text = "\n".join(block.text for block in blocks[:200] if block.kind == "paragraph")
    facts = {}
    for key, pattern in (("project", r"项目名称[：:]\s*([^\n]{4,60})"), ("owner", r"(?:招标人|采购人)[：:]\s*([^\n（(]{4,40})"),
                         ("company", r"(?:投标人|供应商)(?:名称)?[：:]\s*([^\n（(]{4,40}?)(?:（|\(|$|\n)")):
        matched = re.search(pattern, text)
        if matched and not re.search(r"盖章|签字|电子签章", matched.group(1)):
            facts[key] = matched.group(1).strip()
    # 封面上最长的“××项目 / ××工程 / ××总承包”标题行比正文里截出来的片段更完整
    titles = [block.text.strip() for block in blocks[:30] if block.kind == "paragraph" and len(block.text.strip()) <= 60
              and re.search(r"(?:项目|工程|总承包|采购|服务)(?:[（(][^）)]*[）)])?$", block.text.strip())]
    longest = max(titles, key=len, default="")
    if longest and len(longest) > len(facts.get("project", "")):
        facts["project"] = longest
    return facts


def split_sections(blocks: list[DocBlock]) -> list[dict]:
    sections: list[dict] = []
    path: list[str] = []
    current: dict | None = None
    for block in blocks:
        if block.kind == "paragraph" and _is_heading(block):
            title = re.sub(r"^\s*(?:第[一二三四五六七八九十]+[章节部分]|[一二三四五六七八九十]+[、．.]|[（(][一二三四五六七八九十]+[）)]|\d+(?:\.\d+){0,3}\s*[、．.]?)\s*", "", block.text).strip()
            level = block.level or (1 if block.text.startswith("第") else 2)
            path = path[:max(0, level - 1)] + [title]
            current = {"title": title, "path": list(path), "paragraphs": []}
            sections.append(current)
            continue
        if current is None:
            continue
        if block.kind == "paragraph":
            current["paragraphs"].append(block.text.strip())
        else:
            current["paragraphs"] += [" | ".join(row) for row in block.rows[:20]]
    result = []
    for section in sections:
        text = "\n".join(p for p in section["paragraphs"] if p)
        if len(text) >= _MIN_CHARS:
            result.append({"title": section["title"], "path": section["path"], "text": text[:_MAX_CHARS]})
    return result


class CorpusStore:
    """每个用户一个段落库：``storage/bidgen/corpus/{user}/corpus.json``。"""

    def __init__(self, storage_root: str | Path, user_id: int | str) -> None:
        self.folder = Path(storage_root).resolve() / "bidgen" / "corpus" / str(user_id)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._file = self.folder / "corpus.json"

    def load(self) -> dict:
        if self._file.is_file():
            return json.loads(self._file.read_text(encoding="utf-8"))
        return {"sources": [], "sections": []}

    def _save(self, data: dict) -> None:
        self._file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def import_file(self, path: Path, file_name: str) -> dict:
        suffix = path.suffix.lower()
        if suffix not in CORPUS_SUFFIXES:
            raise ValueError("历史标书仅支持 Word（.docx）或 PDF 文件")
        blocks = read_docx_blocks(path) if suffix == ".docx" else read_pdf_blocks(path)
        sections = split_sections(blocks)
        if not sections:
            raise ValueError(f"未能从“{file_name}”中切分出有效章节（需要有标题结构的投标文件）")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        data = self.load()
        if any(source["digest"] == digest for source in data["sources"]):
            raise ValueError(f"“{file_name}”已导入过")
        source_id = uuid4().hex[:12]
        source = {"id": source_id, "name": file_name, "digest": digest, "sectionCount": len(sections),
                  "facts": _source_facts(blocks), "importedAt": datetime.now().isoformat(timespec="seconds")}
        data["sources"].append(source)
        data["sections"] += [{**section, "source": source_id} for section in sections]
        self._save(data)
        return source

    def delete(self, source_id: str) -> None:
        data = self.load()
        data["sources"] = [s for s in data["sources"] if s["id"] != source_id]
        data["sections"] = [s for s in data["sections"] if s["source"] != source_id]
        self._save(data)

    def summary(self) -> dict:
        data = self.load()
        return {"sources": data["sources"], "sectionCount": len(data["sections"])}


class CorpusIndex:
    """BM25 检索器（构建一次，检索多次）。"""

    def __init__(self, data: dict) -> None:
        self.sections = data.get("sections", [])
        self.sources = {source["id"]: source for source in data.get("sources", [])}
        self.docs = [Counter(_grams(s["title"]) * 3 + _grams(" ".join(s["path"])) + _grams(s["text"])) for s in self.sections]
        self.lengths = [sum(doc.values()) for doc in self.docs]
        self.average = sum(self.lengths) / len(self.lengths) if self.lengths else 0
        frequency: Counter = Counter()
        for doc in self.docs:
            frequency.update(doc.keys())
        total = len(self.docs)
        self.idf = {gram: math.log(1 + (total - count + 0.5) / (count + 0.5)) for gram, count in frequency.items()}

    def search(self, query: str, limit: int = 3) -> list[tuple[float, dict]]:
        terms = Counter(_grams(query))
        scored = []
        for index, doc in enumerate(self.docs):
            score = 0.0
            for term in terms:
                if term not in doc:
                    continue
                tf = doc[term]
                score += self.idf[term] * tf * (_K1 + 1) / (tf + _K1 * (1 - _B + _B * self.lengths[index] / (self.average or 1)))
            if score:
                scored.append((score, self.sections[index]))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[:limit]

    def best_for(self, title: str, rule: str = "") -> dict | None:
        """评分点最相关的历史章节：标题与评分点的二元组重合度不足时不复用，避免张冠李戴。"""
        query_grams = set(_grams(title))
        for score, section in self.search(f"{title} {title} {rule[:80]}", limit=5):
            title_grams = set(_grams(section["title"] + " ".join(section["path"][-2:])))
            overlap = len(query_grams & title_grams) / max(1, len(query_grams))
            if overlap >= 0.5 and score > 4:
                return section
        return None


def adapt(section: dict, source: dict | None, values: dict) -> list[str]:
    """把历史章节中的原项目名称、招标人、投标人替换为本项目信息，按段落返回。"""
    facts = (source or {}).get("facts", {})
    paragraphs = []
    for paragraph in section["text"].split("\n"):
        text = paragraph
        for key in ("project", "owner", "company"):
            old, new = facts.get(key, ""), values.get(key, "")
            # 新旧名称互相包含时（同一项目、或只截到片段）不替换，避免名称重复拼接
            if len(old) >= 4 and new and old not in new and new not in old:
                text = text.replace(old, new)
        if " | " in text or len(text) < 12:
            continue  # 表格行与零碎短句不直接复用
        paragraphs.append(text)
    return paragraphs
