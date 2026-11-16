"""企业自主招标文件：没有“投标文件格式”章节，须知中逐条列出商务标书 / 技术标书的组成。

正文片段为人工摘录，期望值为人工标注，不依赖本机原件。
"""

from docx import Document

from api.services.bidgen.archive import ArchiveEntry
from api.services.bidgen.library_fields import extract_profile_fields
from api.services.bidgen.tender_parser import parse_tender_package

REVIEW_HEADER = ["评审项目", "评审分类", "评审内容", "分配分数"]


def parse_document(tmp_path, sections, name="招标文件.docx"):
    document = Document()
    for section in sections:
        if isinstance(section, str):
            document.add_paragraph(section)
        else:
            table = document.add_table(rows=0, cols=max(map(len, section)))
            for source_row in section:
                for cell, text in zip(table.add_row().cells, source_row):
                    cell.text = text
    path = tmp_path / name
    document.save(path)
    entry = ArchiveEntry(name, name, [], 0, ".docx", path.stat().st_size, name)
    return parse_tender_package([entry], tmp_path, name)


def parse_sample(tmp_path):
    return parse_document(tmp_path, [
        "招标说明",
        "一、招标方：某某新材料科技有限公司",
        "二、招标项目：《生产运营管理平台》项目",
        "五、投标截止时间：2026 年 11 月 18 日下午 03:30",
        "投标须知",
        "四、投标文件的编制及要求",
        "商务标书应包括下列内容：",
        "1. 投标承诺函",
        "2. 法定代表人身份证明书、授权委托书",
        "3. 营业执照、资质认证证书等。",
        "4. 投标报价表(产品、实施分项报价)及报价说明。",
        "技术标书应包括下列内容：",
        "1. 项目整体规划技术方案（内容包括：方案总体设计、实施计划及进度管理）",
        "2. 项目实施周期和计划。",
        "技术标应与商务标分开装订。",
        "六、开标和评标",
        [REVIEW_HEADER,
         ["商务标部分\n（总分 10）", "投标企业资质的完整性", "资格证明文件是否齐全有效。", "4"]],
        [REVIEW_HEADER,
         ["技术标部分\n（总分 40）", "投标产品", "品牌形象及市场占有率", "3"],
         ["", "", "技术先进性", "3"],
         ["", "项目工程实施方案", "实施流程明晰。", "6"]],
        [REVIEW_HEADER, ["价格标部分\n（总分 30）", "价格", "最低报价为满分。", "30"]],
        "十一、附件",
        "附一、投标承诺函模板。",
        "附二、报价单模板。",
        "附一：投标承诺函模板",
        "投标承诺函",
        "致：某某新材料科技有限公司",
        "投标人(盖公章)：",
        "附二：报价单模板",
        "项目总报价：",
        [["序号", "项目内容", "金额(元)"], ["1", "平台总费用", ""]],
    ])


def test_composition_list_builds_directory(tmp_path):
    info = parse_sample(tmp_path)

    assert info.format_layout == "integrated"
    assert [(item.volume, item.number, item.title) for item in info.format_items] == [
        ("商务文件", "1", "投标承诺函"),
        ("商务文件", "2", "法定代表人身份证明书、授权委托书"),
        ("商务文件", "3", "营业执照、资质认证证书等"),
        ("商务文件", "4", "投标报价表(产品、实施分项报价)及报价说明"),
        ("技术文件", "1", "项目整体规划技术方案"),
        ("技术文件", "1.1", "方案总体设计"),  # 括注的“内容包括”逐项成节
        ("技术文件", "1.2", "实施计划及进度管理"),
        ("技术文件", "2", "项目实施周期和计划"),
    ]
    assert info.format_source == "list"
    assert [item.level for item in info.format_items] == [1, 1, 1, 1, 1, 2, 2, 1]
    assert not any("投标文件格式目录" in warning for warning in info.warnings)


def test_composition_list_reads_main_info(tmp_path):
    info = parse_sample(tmp_path)

    assert info.project_name == "《生产运营管理平台》项目"
    assert info.purchasers == ["某某新材料科技有限公司"]
    assert info.bid_deadline == "2026-11-18T15:30"


def test_attachment_templates_become_format_text(tmp_path):
    items = parse_sample(tmp_path).format_items

    assert not any(item.template for item in items if item.level == 2)
    assert [block["text"] for block in items[0].template] == ["致：某某新材料科技有限公司", "投标人(盖公章)："]
    assert [block["kind"] for block in items[3].template] == ["paragraph", "table"]
    assert items[3].template[1]["rows"][1][1] == "平台总费用"
    assert items[0].has_format and items[3].has_format
    assert not items[1].template and not items[1].has_format


def test_review_table_scoring(tmp_path):
    info = parse_sample(tmp_path)

    assert [(c.volume, c.group, c.factor, c.max_score, c.rule) for c in info.criteria] == [
        ("business", "商务标部分", "投标企业资质的完整性", 4.0, "资格证明文件是否齐全有效。"),
        ("technical", "技术标部分", "品牌形象及市场占有率", 3.0, "投标产品：品牌形象及市场占有率"),
        ("technical", "技术标部分", "技术先进性", 3.0, "投标产品：技术先进性"),
        ("technical", "技术标部分", "项目工程实施方案", 6.0, "实施流程明晰。"),
        ("business", "价格标部分", "价格", 30.0, "最低报价为满分。"),
    ]


def test_id_number_joined_to_label_by_ocr():
    front = "姓名\n张三\n性别男\n民族汉\n出生1949年12月31日\n住址\n北京市朝阳区某某路1号\n公民身份号码11010519491231002X"

    patch = extract_profile_fields(fields=[], text=front, slot="id_agent", title="授权代理人身份证", role="")

    assert patch == {"agent": {"name": "张三", "idNumber": "11010519491231002X"}}


def test_id_number_with_wrong_check_digit_is_not_guessed():
    front = "姓名\n张三\n公民身份号码110105194912310021"

    patch = extract_profile_fields(fields=[], text=front, slot="id_agent", title="授权代理人身份证", role="")

    assert patch == {"agent": {"name": "张三"}}


# ---------------- 清单式目录的编排与编写 ----------------

def parse_full_sample(tmp_path):
    """在基础样例上补充功能需求清单（含上线阶段）与交付时间、付款方式。"""
    return parse_document(tmp_path, [
        "一、招标方：某某新材料科技有限公司",
        "二、招标项目：《生产运营管理平台》项目",
        "八、交付时间：合同签订生效后100个自然日内完成交付。",
        "商务标书应包括下列内容：",
        "1. 法定代表人身份证明书、授权委托书",
        "2. 投标报价表及报价说明。",
        "技术标书应包括下列内容：",
        "1. 项目整体规划技术方案（内容包括：方案总体设计、系统接口要求、实施计划及进度管理）",
        "2. 项目实施周期和计划。",
        "3. 系统的验收。",
        "技术标应与商务标分开装订。",
        [REVIEW_HEADER,
         ["技术标部分\n（总分 40）", "系统技术方案", "方案总体架构组成的合理性", "5"],
         ["", "", "系统各功能模块之间的关联", "3"],
         ["", "", "系统上线方案的合理性", "3"],
         ["", "", "工程实施计划安排和各阶段内容的合理性", "5"]],
        [REVIEW_HEADER, ["价格标部分\n（总分 30）", "价格", "最低报价为满分。", "30"]],
        "八、付款方式：",
        "合同签订后7个工作日内，甲方向乙方支付总费用的30%；乙方完成项目一阶段上线，甲方签字确认后7个工作日内，甲方支付总费用的30%；甲方验收合格并签字确认后7个工作日内，甲方支付总费用的40%。",
        "九、罚则",
        "附一：报价单模板",
        "项目总报价：",
        [["序号", "项目内容", "金额(元)"], ["1", "平台总费用", ""]],
        "附二：招标产品功能需求清单及说明",
        [["板块", "功能模块", "功能说明", "上线阶段", "优先级"],
         ["MES生产执行", "工序流转报工", "报工数据实时回传ERP核算", "一阶段", "高"],
         ["", "生产订单领退料", "工单领料/退料与WMS联动过账", "一阶段", "高"],
         ["QMS质量管理", "来料检验", "采购到货触发检验任务", "二阶段", "中"]],
    ])


def compose(info):
    from copy import deepcopy

    from api.services.bidgen.composer import compose_document
    from api.services.bidgen.document_model import ComposeContext
    from api.services.bidgen.profile import DEFAULT_PROFILE

    profile = deepcopy(DEFAULT_PROFILE)
    profile["company"]["name"] = "测试软件有限公司"
    ctx = ComposeContext(tender=info.to_dict(), profile=profile, lots=[], options={"includeScoreIndex": True})
    return ctx, compose_document(ctx)


def test_requirement_attachment_keeps_modules_and_phases(tmp_path):
    facts = parse_full_sample(tmp_path).project_facts

    modules = {node["title"]: node["items"] for node in facts["requirements"] if node["items"]}
    assert list(modules) == ["MES生产执行", "QMS质量管理"]
    assert modules["MES生产执行"][1] == {"label": "生产订单领退料", "text": "工单领料/退料与WMS联动过账",
                                     "attrs": {"上线阶段": "一阶段", "优先级": "高"}}
    assert facts["schedule_days"] == 100
    assert facts["payment_terms"].startswith("合同签订后7个工作日内") and facts["payment_terms"].endswith("40%。")


def test_listed_layout_has_one_chapter_per_volume_and_one_section_per_topic(tmp_path):
    _ctx, roots = compose(parse_full_sample(tmp_path))

    # 商务、技术两卷的条目都从 1 编号：各成一章，完整版里不出现两个“一、”
    assert [(root.key, root.title) for root in roots] == [("business", "商务标书"), ("technical", "技术标书"), ("index", "附录  评分标准响应索引")]
    technical = roots[1]
    assert [child.heading for child in technical.children] == ["一、项目整体规划技术方案", "二、项目实施周期和计划", "三、系统的验收"]
    assert [child.heading for child in technical.children[0].children] == ["（一）方案总体设计", "（二）系统接口要求", "（三）实施计划及进度管理"]


def test_each_criterion_is_answered_once_and_sections_do_not_repeat(tmp_path):
    ctx, roots = compose(parse_full_sample(tmp_path))

    located = {}
    for root in roots:
        for node in root.walk():
            for criterion_id in node.criteria:
                located.setdefault(criterion_id, []).append(node.title)
    factors = {criterion["criterion_id"]: criterion["factor"] for criterion in ctx.tender["criteria"]}
    assert {factors[key]: titles for key, titles in located.items()} == {
        "方案总体架构组成的合理性": ["方案总体设计"],
        "系统各功能模块之间的关联": ["系统接口要求"],
        "系统上线方案的合理性": ["项目实施周期和计划"],
        "工程实施计划安排和各阶段内容的合理性": ["项目实施周期和计划"],
        "价格": ["投标报价表及报价说明"],
    }
    # 原先四个“方案类”评分点各写一遍同样的内容：现在任何两节正文都不相同
    bodies = [node.plain_text().split("\n", 1)[1] for root in roots[:2] for node in root.walk()
              if not node.children and "\n" in node.plain_text()]
    assert len(bodies) == len(set(bodies))


def test_listed_sections_use_tender_facts(tmp_path):
    _ctx, roots = compose(parse_full_sample(tmp_path))
    sections = {node.title: node for root in roots for node in root.walk()}

    schedule = next(block for block in sections["实施周期与进度计划"].blocks if block["type"] == "table")
    assert schedule["rows"][0][1] == "第1日" and schedule["rows"][-1][2] == "第100日"  # 排期合计等于招标文件工期
    assert [row[0] for row in sections["验收阶段"].blocks[-1]["rows"]] == ["一阶段上线确认", "最终验收"]  # 取自付款节点
    interfaces = sections["系统接口要求"].plain_text()
    assert "报工数据实时回传ERP核算" in interfaces and "工单领料/退料与WMS联动过账" in interfaces
    assert "采购到货触发检验任务" not in interfaces  # 不涉及其他系统的功能不进接口清单
    assert sections["投标报价表及报价说明"].blocks[0]["text"] == "项目总报价："  # 招标文件给了格式的条目照原文


def test_unstated_validity_is_reported_as_unstated(tmp_path):
    info = parse_full_sample(tmp_path)

    assert info.validity_days == 0
    assert any("没有规定有效期天数" in warning for warning in info.warnings)


# ---------------- 措辞变化不得改坏专有词 ----------------

def test_variation_keeps_words_that_contain_short_synonyms():
    import random

    from api.services.bidgen.variation import _vary_text

    text = "开户许可证在有效期内，按投标须知及时提交，验收依据为合同，系统使用手册随附。"
    for seed in range(30):
        assert _vary_text(text, random.Random(seed), "我公司") == text
