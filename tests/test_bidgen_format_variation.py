"""AI 标书生成：按招标文件版式生成、长条款概述、同一招标文件多次生成措辞不同。"""

import json
from copy import deepcopy
from zipfile import ZipFile

import pytest
from docx import Document

from api.services.bidgen.business_sections import is_requirement_clause, respond_to_requirement
from api.services.bidgen.composer import compose_document
from api.services.bidgen.document_model import ComposeContext
from api.services.bidgen.profile import DEFAULT_PROFILE
from api.services.bidgen.service import BidGenService
from api.services.bidgen.summary import condense_text
from api.services.bidgen.variation import content_digest
from api.services.bidgen.word_writer import render_document

# 取自国家电网 2026 年华中、川渝区域联合采购招标文件第一章的原文
BAD_BEHAVIOR_CLAUSE = (
    "（3）根据《国家电网有限公司供应商关系管理办法》的规定，若投标人为制造商，存在导致其被暂停中标资格或取消中标资格的不良行为且在处理有效期内的，"
    "投标人在投标截止之日至中标通知书到达之日前，任一日在《国家电网有限公司关于供应商不良行为处理情况的通报》处理期内的均适用不良行为处理结果，"
    "或投标人存在触发《国家电网有限公司供应商关系管理办法》第六十三条安全及质量重大问题“熔断机制”情形的，其投标将被否决。"
)
OUTSOURCING_CLAUSE = (
    "（6）投标人不得选配国家电网有限公司《关于供应商不良行为处理情况的通报》和华中、川渝区域所投分标的相应各省公司《关于供应商不良行为处理的通报/公告》中"
    "正接受暂停中标资格、取消中标资格的外购外协供应商或代理（集成服务）制造商的产品；不得选配涉嫌存在《招标投标法》第53条、《招标投标法实施条例》第51条所列行为，"
    "已被司法机关立案的外购外协供应商或代理（集成服务）制造商的产品。"
)
SUBCONTRACT_CLAUSE = (
    "为进一步规范市场秩序，打击违规转分包行为，营造公平的市场环境，国家电网有限公司招标采购的各类产品，除合同约定允许外购外协等分包的之外，"
    "中标人不得进行任何转包、分包行为，不得将中标产品委托其他供应商进行贴牌代工，中标人也不得承接国家电网有限公司招标采购产品其他中标人的贴牌代工及任何转包、分包。"
    "此类行为一经发现将按《国家电网有限公司供应商不良行为处理管理细则》进行严肃处理。请各供应商严格履行投标承诺，诚信履约。"
)
SHORT_CLAUSE = "（2）应有良好的财务状况和商业信用。"


def make_context(**profile_patch):
    profile = deepcopy(DEFAULT_PROFILE)
    profile["company"].update(name="测试电缆有限公司", address="测试路8号", postcode="450000", creditCode="91410100TEST000001")
    profile["legalRep"].update(name="王某", title="高级工程师", phone="0371-1234567")
    profile["staff"].update(total="320", senior="21", middle="40")
    profile["declarations"].update(greenPlan=True, innovationIncentive=True, supplyChainMeasures=True, emergencyProof=True)
    for key, value in profile_patch.items():
        profile[key] = value
    directory = [
        ("商务文件", "1", "商务偏差表", 1), ("商务文件", "2", "补充文件", 1),
        ("商务文件", "2.1", "投标人基本情况表", 2),
        ("商务文件", "2.2", "符合招标文件投标人资格要求的证明文件（如需）", 2),
        ("商务文件", "2.2.1", "符合招标文件投标人资格要求的证明文件（如有）", 3),
        ("商务文件", "2.2.2", "查询报告及截图", 3),
        ("商务文件", "2.3", "招标文件第一章招标公告10.3要求补充的商务支撑证明材料", 2),
        ("商务文件", "3", "法定代表人（单位负责人）授权委托书", 1),
        ("技术文件", "1", "技术偏差表", 1),
        ("技术文件", "2", "售后服务保障及承诺", 1),
        ("技术文件", "3", "质量保证措施方案", 1),
        ("技术文件", "4", "技术服务，包括培训、安装指导等", 1),
    ]
    supplements = [
        {"volume": "business", "scope": "", "project": "履约能力评价", "item": "应急保供", "note": "对应急抢险保电物资做出突出贡献证明材料"},
        {"volume": "business", "scope": "", "project": "高质量发展评价", "item": "绿色发展规划及管理体系认证", "note": "投标人建立的绿色发展规划制度证明材料"},
        {"volume": "business", "scope": "", "project": "高质量发展评价", "item": "创新激励机制、供应链保障措施", "note": "企业制定的创新激励相关政策和机制的情况"},
    ]
    tender = {
        "project_name": "回归验证项目", "tender_no": "TEST-002", "criteria": [], "supplements": supplements,
        "general_qualifications": ["3.2投标人及其投标产品须满足如下通用资格要求：", SHORT_CLAUSE, BAD_BEHAVIOR_CLAUSE,
                                   OUTSOURCING_CLAUSE, "3.7关于转包、分包和贴牌代工等行为的要求", SUBCONTRACT_CLAUSE,
                                   "详见投标邀请书附件2：专用资格条件。"],
        "format_items": [{"volume": volume, "number": number, "title": title, "level": level, "required": True,
                          "has_format": False, "not_applicable": False, "port": "", "template": []}
                         for volume, number, title, level in directory],
    }
    return ComposeContext(tender=tender, profile=profile, lots=[], options={})


def find_section(roots, title):
    return next(node for root in roots for node in root.walk() if node.title == title)


def all_text(roots):
    return "\n".join(root.plain_text() for root in roots)


# ---------------- 版式与内容 ----------------


def test_basic_info_table_follows_tender_layout_with_merged_cells(tmp_path):
    context = make_context()
    roots = compose_document(context)
    path = render_document([roots[0]], context, tmp_path / "business.docx", "商务文件")
    word_table = next(t for t in Document(path).tables if t.cell(0, 0).text == "投标人名称")
    assert len(word_table.columns) == 7

    def distinct(row_index):
        cells, seen = [], []
        for cell in word_table.rows[row_index].cells:
            if cell._tc not in seen:
                seen.append(cell._tc)
                cells.append(cell.text)
        return cells

    assert distinct(0) == ["投标人名称", "测试电缆有限公司"]
    assert distinct(1) == ["注册地址", "测试路8号", "邮政编码", "450000"]
    assert distinct(2)[:2] == ["联系方式", "联系人"] and distinct(2)[3] == "电话"
    assert distinct(3)[:2] == ["联系方式", "传真"] and distinct(3)[3] == "网址"
    assert word_table.cell(2, 0)._tc is word_table.cell(3, 0)._tc  # “联系方式”跨两行
    assert distinct(5) == ["法定代表人（单位负责人）", "姓名", "王某", "技术职称", "高级工程师", "电话", "0371-1234567"]
    assert distinct(6)[0] == "技术负责人"
    assert distinct(7) == ["成立时间", "【待填：成立时间】", "员工总人数：320"]
    assert distinct(8) == ["企业资质等级", "—", "其中", "项目经理", "—"]
    assert distinct(9) == ["统一社会信用代码", "91410100TEST000001", "其中", "高级职称人员", "21"]
    assert word_table.cell(8, 3)._tc is word_table.cell(12, 3)._tc  # “其中”跨五行
    assert [distinct(index)[0] for index in (10, 11, 12, 13, 14)] == ["注册资金", "开户银行", "账号", "经营范围", "备注"]


def test_company_overview_section_is_not_generated():
    roots = compose_document(make_context())
    titles = [node.title for root in roots for node in root.walk()]
    assert "企业概况" not in titles
    assert "【待填：企业简介】" not in all_text(roots)


def test_same_named_child_directory_item_does_not_repeat_heading():
    roots = compose_document(make_context())
    title = "符合招标文件投标人资格要求的证明文件"
    assert [node.title for root in roots for node in root.walk()].count(title) == 1
    section = find_section(roots, title)
    assert [child.title for child in section.children] == ["投标人资格要求逐条响应表", "查询报告及截图"]


def test_supplement_sections_do_not_echo_tender_requirement():
    roots = compose_document(make_context())
    assert "招标文件要求：" not in all_text(roots)
    green = find_section(roots, "绿色发展规划及管理体系认证")
    assert green.blocks[0]["text"].startswith("测试电缆有限公司已制定《绿色发展规划》")


@pytest.mark.parametrize(
    ("clause", "brief", "response_keyword"),
    [
        (BAD_BEHAVIOR_CLAUSE, "投标截止日至中标通知书到达日前，投标人（及所代理产品的制造商）不得处于国家电网供应商不良行为处理期内，不得触发安全及质量重大问题“熔断机制”。", "未触发安全及质量重大问题“熔断机制”"),
        (OUTSOURCING_CLAUSE, "不得选配正处于暂停或取消中标资格处理期内、或已被司法机关立案的外购外协供应商或代理（集成服务）制造商的产品。", "外购外协供应商均不在"),
        (SUBCONTRACT_CLAUSE, "中标人不得转包、违规分包，不得委托其他供应商贴牌代工，也不得承接其他中标人的贴牌代工。", "不进行任何转包、违规分包"),
        (SHORT_CLAUSE, "应有良好的财务状况和商业信用。", "财务状况和商业信用良好"),
    ],
)
def test_long_requirement_is_summarized_and_matched_to_its_own_response(clause, brief, response_keyword):
    shown, response = respond_to_requirement(clause)
    assert shown == brief
    assert response_keyword in response


def test_qualification_table_has_complete_sentences_and_skips_headings():
    assert not is_requirement_clause("3.2投标人及其投标产品须满足如下通用资格要求：")
    assert not is_requirement_clause("3.7关于转包、分包和贴牌代工等行为的要求")
    assert not is_requirement_clause("详见投标邀请书附件2：专用资格条件。")
    roots = compose_document(make_context())
    rows = find_section(roots, "投标人资格要求逐条响应表").blocks[0]["rows"]
    assert [row[0] for row in rows] == ["1", "2", "3", "4"]
    for _number, requirement, _response, conclusion in rows:
        assert "……" not in requirement
        assert requirement.endswith("。") and len(requirement) <= 90
        assert conclusion == "满足"


def test_condense_text_cuts_at_sentence_boundary_without_ellipsis():
    text = "（新增）本采购项目不收取履约保证金。通用条款及合同协议书中有关履约保证金相关条款，均不适用于本次采购项目。另有其他补充约定详见附件。"
    assert condense_text(text, 60) == "本采购项目不收取履约保证金。通用条款及合同协议书中有关履约保证金相关条款，均不适用于本次采购项目。"
    assert condense_text("短句。", 60) == "短句。"
    single = condense_text("甲方应当在收到货物后" + "逐项核对，" * 20 + "并出具验收意见", 40)
    assert len(single) <= 42 and single.endswith("等。") and "……" not in single


# ---------------- 多次生成措辞不同 ----------------


def test_same_seed_reproduces_and_different_seeds_change_wording():
    first, again, other = (compose_document(make_context(), seed) for seed in (7, 7, 20261113))
    assert content_digest(first) == content_digest(again)
    assert content_digest(first) != content_digest(other)
    changed = [a for a, b in zip(all_text(first).splitlines(), all_text(other).splitlines(), strict=True) if a != b]
    assert len(changed) >= 10


def test_variation_keeps_facts_structure_and_fixed_formats():
    baseline = compose_document(make_context())
    for seed in (1, 2, 3):
        varied = compose_document(make_context(), seed)
        assert [(n.key, n.heading) for r in varied for n in r.walk()] == [(n.key, n.heading) for r in baseline for n in r.walk()]
        # 招标文件给定格式的授权委托书、偏差表不得改写
        for title in ("法定代表人（单位负责人）授权委托书", "商务偏差表", "技术偏差表"):
            assert [s.plain_text() for r in varied for s in r.walk() if s.title == title] == \
                [s.plain_text() for r in baseline for s in r.walk() if s.title == title]
        text = all_text(varied)
        for fact in ("测试电缆有限公司", "91410100TEST000001", "TEST-002", "0371-1234567", "员工总人数：320", "“熔断机制”"):
            assert fact in text
        assert text.count("【待填") == all_text(baseline).count("【待填")
        basic = find_section(varied, "投标人基本情况表1").blocks[0]
        assert basic == find_section(baseline, "投标人基本情况表1").blocks[0]


def test_without_seed_output_is_stable():
    assert content_digest(compose_document(make_context())) == content_digest(compose_document(make_context()))


def test_service_regenerating_same_tender_produces_different_documents(tmp_path):
    service = BidGenService(tmp_path, 1)
    profile = make_context().profile
    service.profiles.save(profile)
    task_id = "0" * 32
    folder = tmp_path / "bidgen" / "tasks" / "1" / task_id
    folder.mkdir(parents=True)
    tender = {
        **make_context().tender, "package_no": "包1", "lots": [], "purchasers": [], "agency": "", "bid_deadline": "",
        "validity_days": 90, "validity_end": "", "publish_date": "", "bid_bond_required": False, "consortium_allowed": False,
        "tech_specs": [], "files": [], "warnings": [], "key_clauses": [],
    }
    state = {"id": task_id, "archiveName": "招标文件.rar", "status": "parsed", "createdAt": "2026-11-13T00:00:00",
             "updatedAt": "2026-11-13T00:00:00", "tree": [], "tender": tender,
             "selection": {"lotCodes": [], "packageNo": "包1", "signDate": "2026-11-13"}, "result": None}
    (folder / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    digests, bodies = [], []
    for _round in range(4):
        result = service.generate(task_id, {"signDate": "2026-11-13"})["result"]
        digests.append(result["contentDigest"])
        full = next(item for item in result["files"] if item["key"] == "full")
        with ZipFile(service.output_file(task_id, full["name"])) as package:
            bodies.append(package.read("word/document.xml"))
    assert len(set(digests)) == 4
    assert len(set(bodies)) == 4
    assert all("测试电缆有限公司".encode() in body for body in bodies)
