"""措辞变化：同一份招标文件多次生成时，正文表述不雷同。

只改叙述性文字的说法，不改事实数据；招标文件给定的固定格式（偏差表、授权委托书、承诺函等）、
引号与书名号内的原文、“待填”标记、参数表一律保持原样。
"""

from __future__ import annotations

import hashlib
import random
import re

from api.services.bidgen.document_model import Section

SELF_REFERENCES = ("我公司", "本公司")
# 每组内的说法可以互换；按出现位置逐处随机取用。
# 组内不放单字和会嵌在其他词里的短词（“可”“须”“及”“有效”“使用”“依据”）：
# 按子串替换会把“许可证”“投标须知”“及时”“有效期”“验收依据”改坏
PHRASE_GROUPS: tuple[tuple[str, ...], ...] = (
    ("证明材料附后", "证明材料详见附件", "证明材料随本文件一并提交"),
    ("证书附后", "证书详见附件", "证书复印件附后"),
    ("高度重视", "十分重视", "始终重视"),
    ("严格按工艺文件", "严格依照工艺文件", "严格遵照工艺文件"),
    ("全过程可追溯", "全流程可追溯"),
    ("配置齐全", "配备齐全"),
    ("积极配合", "主动配合"),
    ("免费提供", "无偿提供"),
    ("定期开展", "定期组织开展"),
    ("郑重承诺", "在此承诺", "庄严承诺"),
    ("全力保障", "切实保障", "全力确保"),
    ("为确保", "为保证", "为保障"),
    ("为便于", "为方便"),
    ("立即启动", "第一时间启动"),
    ("立即响应", "第一时间响应"),
    ("具体如下", "详述如下"),
    ("逐项响应如下", "逐条响应如下"),
    ("逐项提交如下", "逐条提交如下"),
    # 通用编写器 / 行业编写器常用表述
    ("我方将", "我方会", "我方拟"),
    ("严格按照", "严格依照", "严格遵照"),
    ("按照采购文件", "依据采购文件", "根据采购文件"),
    ("全过程", "全流程", "各环节"),
    ("建立健全", "建立完善", "健全"),
    ("落实到位", "执行到位", "落到实处"),
    ("充分理解", "全面理解", "深入理解"),
    ("认真研读", "仔细研读", "逐条研读"),
    ("逐项响应如下", "逐条说明如下", "分项响应如下"),
    ("具体措施如下", "主要措施如下", "相关措施如下"),
    ("定期", "按周期", "周期性"),
    ("持续改进", "不断改进", "持续优化"),
    ("闭环管理", "闭环控制", "全程闭环"),
    ("统一管理", "集中管理", "统筹管理"),
    ("专人负责", "专人跟进", "指定专人负责"),
    ("书面报告", "以书面形式报告", "书面报送"),
    ("合理安排", "科学安排", "统筹安排"),
    ("有效保障", "切实保障", "可靠保障"),
    ("提前沟通", "预先沟通", "事前沟通"),
    ("主要包括", "主要涵盖", "重点包括"),
    ("包括但不限于", "涵盖但不限于", "包含但不限于"),
    ("与此同时", "同时", "此外"),
    ("在此基础上", "以此为基础", "基于此"),
    ("满足采购文件要求", "符合采购文件要求", "达到采购文件要求"),
    ("采购人", "采购方", "采购单位"),
    ("实施过程中", "实施期间", "项目执行中"),
    ("发现问题", "发现的问题", "暴露的问题"),
    ("立即整改", "限期整改", "即时整改"),
    ("相关人员", "有关人员", "相应人员"),
    ("避免", "防止", "杜绝"),
    ("提升", "提高", "增强"),
    ("必要时", "如有需要", "视情况"),
    ("可配置", "支持配置", "可灵活配置"),
    ("可视化", "图形化"),
    ("保障措施", "保证措施", "支撑措施"),
    ("应急预案", "应急处置预案", "突发事件预案"),
    ("质量控制", "质量管控", "质量把控"),
    ("进度控制", "进度管控", "进度把控"),
    ("进行", "开展"),
    ("根据", "按照"),
    ("具备", "具有"),
    ("跟踪", "追踪"),
    ("各类", "各种", "各项"),
    ("可以", "能够"),
    ("必须", "务必"),
    ("有关", "相关"),
    ("主动", "积极"),
    ("制定", "编制"),
    ("提前", "预先"),
    ("所有", "全部"),
    ("每个", "每一"),
    ("对于", "针对"),
    ("情况", "状况"),
    ("解决", "处理"),
    ("处理", "处置"),
    ("优化", "改进"),
    ("采用", "选用"),
    ("迅速", "快速"),
    ("目前", "当前"),
    ("逐项", "逐一", "逐条"),
    ("全面响应", "全方位响应", "完整响应"),
    ("组织开展", "组织实施"),
    ("开展培训", "组织培训"),
    ("稳定运行", "平稳运行", "可靠运行"),
    ("保持", "维持"),
    ("快速", "高效"),
)
_ORDINAL_CUE = re.compile(r"（\d+）|[一二三四五六七八九十]是|第[一二三四五六七八九十\d]+|首先|其次|然后|最后|随后|之后|先|再|继而|第\d+日|\d+日内|→")
_PROTECTED = re.compile(r"(“[^”]*”|《[^》]*》|【[^】]*】)")
_GROUP_BY_PHRASE = {phrase: group for group in PHRASE_GROUPS for phrase in group}
_PHRASES = re.compile("|".join(sorted((re.escape(phrase) for phrase in _GROUP_BY_PHRASE), key=len, reverse=True)))
_SELF = re.compile("|".join(re.escape(name) for name in SELF_REFERENCES))


def _is_fixed(section: Section) -> bool:
    """带居中格式标题（如“承 诺 函”）的章节是招标文件给定格式。"""
    return section.fixed or any(block["type"] == "paragraph" and block.get("size") for block in section.blocks)


def _vary_text(text: str, rng: random.Random, self_reference: str) -> str:
    parts = _PROTECTED.split(text)
    for index in range(0, len(parts), 2):
        part = _SELF.sub(self_reference, parts[index])
        parts[index] = _PHRASES.sub(lambda matched: rng.choice(_GROUP_BY_PHRASE[matched.group(0)]), part)
    return "".join(parts)


_CONNECTIVES = ("同时，", "此外，", "另外，", "在此基础上，")
# 3 项及以上的顿号并列（“鉴权、幂等、重试与对账”）：项与项之间无先后关系，可调换次序
_LIST = re.compile(r"(包括|包含|涵盖|覆盖|含|如|：|（)((?:(?:(?!等)[一-鿿]){2,8}、){2,}(?:(?!等)[一-鿿]){2,8})(?=等|，|。|；|）|$)")


def _shuffle_lists(text: str, rng: random.Random) -> str:
    def shuffle(matched: re.Match) -> str:
        items = matched.group(2).split("、")
        if any(re.search(r"第|阶段|准备|启动|调研|设计|开发|测试|上线|验收|先|后|[一二三四五六七八九十]级", item) for item in items):
            return matched.group(0)  # 可能有先后顺序的并列（流程、阶段、等级）不动
        shuffled = items[:]
        rng.shuffle(shuffled)
        return matched.group(1) + "、".join(shuffled)

    parts = _PROTECTED.split(text)
    for index in range(0, len(parts), 2):
        parts[index] = _LIST.sub(shuffle, parts[index])
    return "".join(parts)


def _vary_sentences(text: str, rng: random.Random) -> str:
    """多句段落中，随机为后续某一句加承接词（每段至多一处），改变句子组织而不改事实。"""
    sentences = re.split(r"(?<=。)", text)
    candidates = [i for i in range(1, len(sentences)) if len(sentences[i]) > 12
                  and not re.match(r"[（(\d一二三四五六七八九十]|同时|此外|另外|在此基础上|并|且|但|如|若", sentences[i])]
    if candidates and rng.random() < 0.5:
        index = rng.choice(candidates)
        sentences[index] = rng.choice(_CONNECTIVES) + sentences[index]
    return "".join(sentences)


def _reorder_clauses(text: str, rng: random.Random) -> str:
    """“引导语：措施A；措施B；措施C。”这类并列措施打乱次序；含顺序词、步骤或日期的段落不动。"""
    head, colon, body = text.partition("：")
    if not colon or len(head) > 40:
        return text
    tail = "。" if body.endswith("。") else ""
    clauses = body.rstrip("。").split("；")
    if len(clauses) < 3 or any(_ORDINAL_CUE.search(clause) for clause in clauses) or any(len(c) < 6 for c in clauses):
        return text
    shuffled = clauses[:]
    rng.shuffle(shuffled)
    return f"{head}：{'；'.join(shuffled)}{tail}"


def vary_document(roots: list[Section], rng: random.Random) -> None:
    """就地改写各章节叙述性段落的措辞。"""
    self_reference = rng.choice(SELF_REFERENCES)
    for root in roots:
        for section in root.walk():
            if _is_fixed(section):
                continue
            for block in section.blocks:
                if block["type"] == "paragraph":
                    block["text"] = _vary_sentences(_reorder_clauses(_shuffle_lists(_vary_text(block["text"], rng, self_reference), rng), rng), rng)
                elif block["type"] == "table" and block.get("narrative"):
                    block["rows"] = [[_vary_text(cell, rng, self_reference) for cell in row] for row in block["rows"]]


def content_digest(roots: list[Section]) -> str:
    """正文指纹：用于确认重新生成的内容与上一次不同。"""
    return hashlib.sha256("\n".join(root.plain_text() for root in roots).encode("utf-8")).hexdigest()
