"""招标 / 采购文件“需求”章节的结构化：标题树 + 条目。

把“第五章 项目采购需求”这类章节拆成节点（标题、层级、正文、表格、带标签的条目），供编写器逐条响应。
只做结构切分，不改写原文。
"""

from __future__ import annotations

import re

from api.services.bidgen.docx_reader import DocBlock

_CHAPTER = re.compile(r"^第[一二三四五六七八九十百\d]+章")
REQUIREMENT_CHAPTER = re.compile(r"(?:项目)?(?:采购|用户|技术|服务|业务)?需求(?:书)?|发包人要求|技术规范|服务要求|采购内容")
_NUMBERED_TITLE = re.compile(r"^\s*(?:\d+(?:\.\d+){0,3}\s*[.．、]?|[（(][一二三四五六七八九十]+[）)]|[一二三四五六七八九十]+[、.．])\s*(\S.{1,28})$")
_LABELED_ITEM = re.compile(r"^\s*(?:\d+[)）]|[（(]\d+[）)]|\d+[.．、]|[①-⑳])?\s*([^：:，。,；]{2,30})[：:]\s*(.+)$", re.S)
_ATTACHMENT = re.compile(r"^附件?\s*[一二三四五六七八九十\d]+\s*[:：、.．]")
_MODULE_NAME = re.compile(r"(?:功能)?(?:模块|名称)|功能点?")
_MODULE_TEXT = re.compile(r"(?:功能|需求)?(?:说明|描述|要求)")
_CJK_GAP = re.compile(r"(?<=[一-鿿，、。；：（）])[ \t　]+(?=[一-鿿，、。；：（）])")


def tidy(text: str) -> str:
    return _CJK_GAP.sub("", re.sub(r"[ \t　]+", " ", (text or "").strip()))


def _strip_number(text: str) -> str:
    return re.sub(r"^\s*(?:\d+(?:\.\d+){0,3}\s*[.．、]?|[（(][一二三四五六七八九十\d]+[）)]|[一二三四五六七八九十]+[、.．])\s*", "", text).strip()


def find_requirement_chapter(blocks: list[DocBlock]) -> list[DocBlock]:
    """返回需求章节正文（不含章标题）；找不到时返回空列表。"""
    for index, block in enumerate(blocks):
        if block.kind != "paragraph" or not block.level or not _CHAPTER.match(block.text):
            continue
        name = re.sub(r"^第[一二三四五六七八九十百\d]+章\s*", "", block.text)
        if REQUIREMENT_CHAPTER.search(name) and "格式" not in name:
            end = next((i for i in range(index + 1, len(blocks)) if blocks[i].kind == "paragraph" and blocks[i].level
                        and _CHAPTER.match(blocks[i].text)), len(blocks))
            return blocks[index + 1:end]
    # 企业自主招标文件把需求清单放在附件里（“附二：招标产品功能需求清单及说明”）；附件目录里的同名行没有正文
    for index, block in enumerate(blocks):
        if block.kind == "paragraph" and len(block.text) < 40 and "需求" in block.text and _ATTACHMENT.match(block.text):
            end = next((i for i in range(index + 1, len(blocks)) if blocks[i].kind == "paragraph"
                        and _ATTACHMENT.match(blocks[i].text)), len(blocks))
            if end > index + 1:
                return blocks[index + 1:end]
    return []


def _module_columns(header: list[str]) -> tuple[int, int] | None:
    """功能清单表（板块 | 功能模块 | 功能说明 | ……）的 (模块名列, 说明列)；不是这种表返回 None。"""
    titles = [re.sub(r"\s", "", cell) for cell in header]
    name_column = next((i for i, title in enumerate(titles) if _MODULE_NAME.fullmatch(title)), None)
    text_column = next((i for i, title in enumerate(titles) if _MODULE_TEXT.fullmatch(title)), None)
    if name_column is None or text_column is None or text_column <= name_column:
        return None
    return name_column, text_column


def build_requirement_tree(blocks: list[DocBlock]) -> list[dict]:
    """需求章节 → 扁平节点列表（按原文顺序）。

    节点：``{"title", "level", "path", "texts", "items", "tables"}``；``items`` 为“标签：内容”形式的条目
    （如“1）访问控制：……”），``path`` 为祖先标题链。标题来自标题样式，或正文中短的编号行（“1. 基础功能平台”）。
    """
    chapter = find_requirement_chapter(blocks)
    nodes: list[dict] = []
    stack: list[tuple[int, dict]] = []

    def open_node(title: str, level: int) -> dict:
        while stack and stack[-1][0] >= level:
            stack.pop()
        node = {"title": _strip_number(tidy(title)), "raw_title": tidy(title), "level": level,
                "path": [parent["title"] for _, parent in stack], "texts": [], "items": [], "tables": []}
        nodes.append(node)
        stack.append((level, node))
        return node

    current = open_node("需求概述", 1)
    for block in chapter:
        if block.kind == "table":
            columns = _module_columns(block.rows[0])
            if columns is None:
                current["tables"].append([[tidy(cell) for cell in row] for row in block.rows])
                continue
            # 功能清单表：首列板块作小标题，每行“功能模块：功能说明”作该板块下的条目
            name_column, text_column = columns
            group_level = (stack[-1][0] if stack else 1) + 1
            for row in block.rows[1:]:
                cells = [tidy(cell) for cell in row]
                if len(cells) <= text_column:
                    continue
                if name_column and cells[0]:
                    current = open_node(cells[0], group_level)
                if cells[name_column] and cells[text_column]:
                    # 说明列之后的列（上线阶段、优先级等）随条目保留，供排期使用
                    extras = {tidy(title): cells[column] for column, title in enumerate(block.rows[0])
                              if column > text_column and column < len(cells) and cells[column]}
                    current["items"].append({"label": cells[name_column], "text": cells[text_column], "attrs": extras})
            continue
        text = tidy(block.text)
        if not text:
            continue
        if block.level:
            current = open_node(text, block.level + 1)
            continue
        numbered = _NUMBERED_TITLE.match(text)
        if numbered and len(text) <= 30 and not re.search(r"[，。；：:]", text):
            # 正文里的编号短行是下一级小标题：挂在当前标题节点之下
            base = stack[-1][0] if stack else 1
            sibling = next((lvl for lvl, node in reversed(stack) if node.get("numbered")), None)
            level = sibling if sibling is not None and current.get("numbered") else base + 1
            current = open_node(text, level)
            current["numbered"] = True
            continue
        labeled = _LABELED_ITEM.match(text)
        # 标签一般很短；带括注的（“色系权重优先排程（粉末行业个性化需求）”）放宽到 30 字
        label_limit = 30 if labeled and re.search(r"[（(]", labeled.group(1)) else 16
        if labeled and len(labeled.group(1)) <= label_limit and not re.search(r"[，。；]", labeled.group(1)):
            current["items"].append({"label": tidy(labeled.group(1)), "text": tidy(labeled.group(2))})
        else:
            current["texts"].append(text)
    for node in nodes:
        node.pop("numbered", None)
    return [node for node in nodes if node["texts"] or node["items"] or node["tables"] or node is not nodes[0]]


def node_text(node: dict) -> str:
    parts = list(node["texts"]) + [f"{item['label']}：{item['text']}" for item in node["items"]]
    return "\n".join(parts)


def find_nodes(tree: list[dict], pattern: str) -> list[dict]:
    return [node for node in tree if re.search(pattern, node["title"])]


def find_node(tree: list[dict], pattern: str) -> dict | None:
    found = find_nodes(tree, pattern)
    return found[0] if found else None


def children(tree: list[dict], parent: dict) -> list[dict]:
    """直接子节点。"""
    index = tree.index(parent)
    result = []
    for node in tree[index + 1:]:
        if node["level"] <= parent["level"]:
            break
        if node["level"] == min(n["level"] for n in tree[index + 1:tree.index(node) + 1] if n["level"] > parent["level"]):
            result.append(node)
    return [node for node in result if node["path"][-1:] == [parent["title"]] or not node["path"]]


def descendants(tree: list[dict], parent: dict) -> list[dict]:
    index = tree.index(parent)
    result = []
    for node in tree[index + 1:]:
        if node["level"] <= parent["level"]:
            break
        result.append(node)
    return result
