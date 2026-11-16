"""整本 EPC 招标文件：人工摘录正文片段与独立标注期望，不依赖本机原件。"""

from pathlib import Path

from docx import Document

from api.services.bidgen.archive import ArchiveEntry
from api.services.bidgen.tender_parser import parse_tender_package


PROJECT = "郑州市郑东新区分布式公共新能源充电桩建设项目（一期）EPC总承包"


def parse_document(tmp_path, sections, name="招标文件正文.docx"):
    document = Document()
    for section in sections:
        if isinstance(section, str):
            document.add_paragraph(section)
        elif isinstance(section, tuple):
            document.add_heading(section[0], section[1])
        else:
            table = document.add_table(rows=0, cols=max(map(len, section)))
            for source_row in section:
                for cell, text in zip(table.add_row().cells, source_row):
                    cell.text = text
    path = tmp_path / name
    document.save(path)
    entry = ArchiveEntry(name, name, [], 0, ".docx", path.stat().st_size, name)
    return parse_tender_package([entry], tmp_path, name)


def test_integrated_announcement_and_split_front_table(tmp_path):
    info = parse_document(tmp_path, [
        "第一章 招标公告", f"2.1项目名称：{PROJECT}",
        "3.投标人资格要求", "3.1资质要求：具有有效的营业执照。",
        "4.招标文件的获取", "2026年10月31日至2026年11月6日下载招标文件。",
        "5.1投标文件递交截止时间：2026年11月20日10时00分（北京时间）；",
        "招标人（异议受理单位）：郑州新发展基础设施建设有限公司",
        "招标代理机构：河南联创工程造价管理有限公司",
        ("第二章 投标人须知", 1), "投标人须知前附表",
        [["条款号", "条 款 名 称", "编 列 内 容"],
         ["1.1.4", "项目名称", PROJECT.replace("EPC", "E PC")],
         ["1.3.1", "招标范围", "设计招标范围：方案设计。施工招标范围："]],
        [["", "", "施工、设备采购与安装调试。"],
         ["2.2.2", "投标截止时间", "2026年11月20日10时00分（北京时间）"],
         ["3.3.1", "投标有效期", "投标截止之日起120日历天"],
         ["3.4.1", "投标保证金", "□不要求 ☑要求，投标保证金的金额：500000.00元"]],
        [["", "", "信用评价结果为AAA级时，免缴全部投标保证金。"],
         ["10.3 知识产权", "", ""], ["", "", "未经书面同意不得复印。"]],
        ("1. 总则", 1),
        [["9.9", "非前附表内容", "不应混入"]],
    ])
    assert info.project_name == PROJECT
    assert info.bid_deadline == "2026-11-20T10:00"
    assert info.validity_days == 120
    assert info.validity_end == "2027-03-20"
    assert info.purchasers == ["郑州新发展基础设施建设有限公司"]
    assert info.agency == "河南联创工程造价管理有限公司"
    assert info.bid_bond_required is True  # 有条件免缴不等于全体免缴
    assert info.general_qualifications == ["3.1资质要求：具有有效的营业执照。"]
    clauses = {item["number"]: item for item in info.key_clauses}
    assert "安装调试" in clauses["1.3.1"]["content"]
    assert "AAA级" in clauses["3.4.1"]["content"]
    assert clauses["10.3"]["name"] == "知识产权"
    assert clauses["10.3"]["content"] == "未经书面同意不得复印。"
    assert "9.9" not in clauses


def test_scoring_columns_ranges_and_cross_page_continuations(tmp_path):
    # 原表明确为 4 + 5 + 7 + 2 + 1 个评分要素。括号标分是上限，非假定零下限。
    info = parse_document(tmp_path, [
        ("第三章 评标办法（综合评估法）", 1),
        [["条款号", "评审因素", "", "评审标准"],
         ["2.1.1", "形式评审标准", "投标人名称", "与营业执照一致"],
         ["条款号", "条款内容", "", "评分标准"],
         ["2.2.4（1）", "承包人建议书评分标准（8分）", "项目详细说明和总体管理方案、目标 0-2分", ""],
         ["", "", "项目管理机构设置与人员配备计划（含设计） 0-2分", ""],
         ["", "", "项目质量、进度、投资、合同、信息管理与控制 0-2分", ""],
         ["", "", "工程竣工验收、结算、移交管理 0-2分", ""],
         ["2.2.4（2）", "设计组织技术措施评分标准（14分）", "1、设计组织技术措施评分标准（6分）", "设计质量保证体系及措施 0-2分"],
         ["", "", "", "风险防范措施合理可行 0-2分"],
         ["", "", "", "重要设计节点安排等方面有针对性表述 0-2分"]],
        [["", "", "2、设计进度安排（2分）", "有设计进度计划表 0-2分"],
         ["", "", "3、限额设计（2分）", "限额设计的保证措施 0-2分"],
         ["", "", "4、对本项目的设计意图和重点难点的理解 0-2分", ""],
         ["", "", "5、新技术、新材料在本项目设计中的应用 0-2分", ""],
         ["2.2.4（3）", "实施方案评分标准（32分）", "施工方案与技术措施", "施工方法合理、可行。0-6分"],
         ["", "", "设备安装方案与技术措施", "安装方法合理。0-6分"],
         ["", "", "质量管理体系与措施", "明确工程质量主控项目。0-5分"],
         ["", "", "安全管理体系与措施", "技术组织措施合理。0-5分"],
         ["", "", "环境保护管理体系及施工现场扬尘治理措施", "措施合理。0- 4分"],
         ["", "", "工程进度计划与措施", "计划明确。0-3分"],
         ["", "", "风险管理措施", "预控符合规范。0-3分"],
         ["注：缺项不得分。", "", "", ""],
         ["2.2.4 (4)", "投标报价评分标准（40分）", "设计费投标报价评分标准（10分）", "最高投标限价的96%（含）至100%参与基准价计算；"]],
        [["", "", "", "每高于评标基准价1%扣0.5分，扣完为止。"],
         ["", "", "工程费投标报价评分标准（30分）", "最高投标限价的94%（含）至100%参与基准价计算。"],
         ["2.2.4 (5)", "其他评分因素评分标准（6分）", "服务承诺评分标准（6分）", "（1）连续施工的承诺。0-1分"],
         ["", "", "", "（2）质保期内、质保期外的服务承诺。0-1分"],
         ["", "", "", "（3）协调周边关系。0-1分"],
         ["", "", "", "（4）与招标人的配合和保证措施。0-1分"],
         ["", "", "", "（5）其他实质性承诺及合理化建议。0-2分"]],
    ])
    assert len(info.criteria) == 19
    assert len({item.criterion_id for item in info.criteria}) == 19
    assert sum(item.max_score for item in info.criteria) == 100
    assert (info.criteria[0].min_score, info.criteria[0].max_score) == (0, 2)
    assert info.criteria[4].max_score == 6
    assert "风险防范措施" in info.criteria[4].rule
    assert "重要设计节点" in info.criteria[4].rule
    assert info.criteria[4].min_score is None
    assert info.criteria[13].max_score == 4
    prices = [item for item in info.criteria if item.volume == "business"]
    assert [item.max_score for item in prices] == [10, 30]
    assert "96%（含）至100%" in prices[0].rule
    assert "扣0.5分" in prices[0].rule
    assert info.criteria[-1].volume == "technical"
    assert "合理化建议" in info.criteria[-1].rule


def test_format_body_sequence_ignores_toc_and_retains_templates(tmp_path):
    info = parse_document(tmp_path, [
        ("目 录", 1), "第七章 投标文件格式 85", "一、投标函及投标函附录 86",
        ("第一章 招标公告", 1), f"项目名称：{PROJECT}",
        ("第七章 投标文件格式", 1), ("（项目名称）", 1), ("投标文件", 1),
        "目 录", "一、投标函及投标函附录二、法定代表人身份证明六、承包人建议书",
        ("一、投标函及投标函附录", 1), ("（一）投标函", 1), "（招标人名称）：", "投标总报价： 元。",
        "3.我方承诺在招标文件规定的投标有效期内不修改、撤销投标文件。",
        "一、公平竞争参加本次招标活动。",
        ("（二）投标函附录", 1), [["投标有效期", "日历天"], ["计划工期", ""]],
        ("二、法定代表人身份证明", 1), "附：法定代表人身份证扫描件。",
        ("六、承包人建议书", 1), "格式自拟。",
        ("七、设计组织技术措施", 1), "格式自拟。",
        ("八、承包人实施计划", 1), "格式自拟。",
        ("九、服务承诺", 1), ("十、承诺书", 1), ("（一）企业信誉承诺书", 1), "我单位郑重承诺如下：",
        ("第八章 其他内容", 1), "不属于投标文件格式。",
    ])
    assert [item.title for item in info.format_items if item.level == 1] == [
        "投标函及投标函附录", "法定代表人身份证明", "承包人建议书", "设计组织技术措施",
        "承包人实施计划", "服务承诺", "承诺书",
    ]
    by_number = {item.number: item for item in info.format_items}
    assert by_number["1.1"].title == "投标函"
    assert by_number["1.1"].template[0] == {"kind": "paragraph", "text": "（招标人名称）："}
    assert by_number["1.2"].template[0]["rows"][0] == ["投标有效期", "日历天"]
    assert by_number["6"].volume == "技术文件"
    assert by_number["6"].has_format is False
    assert by_number["10.1"].volume == "商务文件"
    assert not any("未在招标文件中找到" in warning for warning in info.warnings)


def test_unknown_validity_is_not_invented(tmp_path):
    info = parse_document(tmp_path, ["投标人须知", "投标截止时间：2026年11月20日10:00"])
    assert info.validity_days == 0
    assert info.validity_end == ""
    assert any("未识别到投标有效期" in warning for warning in info.warnings)


def test_unlabelled_filename_is_identified_from_full_document_content(tmp_path):
    info = parse_document(tmp_path, [*[f"封面说明 {i}" for i in range(45)],
        ("第二章 投标人须知", 1), f"项目名称：{PROJECT}",
        ("第七章 投标文件格式", 1), ("一、投标函", 1), "（招标人名称）：",
    ], name="附件.docx")
    assert info.files[0]["role"] == "main"
    assert info.project_name == PROJECT
    assert info.format_items[0].title == "投标函"


def test_deadline_invalid_date_warns_instead_of_crashing(tmp_path):
    info = parse_document(tmp_path, ["投标人须知", "投标截止时间：2026年2月30日10时00分"])
    assert info.bid_deadline == ""
    assert any("截止时间" in warning for warning in info.warnings)


def compose_integrated(tmp_path):
    from copy import deepcopy

    from api.services.bidgen.composer import compose_document
    from api.services.bidgen.document_model import ComposeContext
    from api.services.bidgen.profile import DEFAULT_PROFILE

    # 人工摘录：郑东新区充电桩 EPC 招标文件第三章评分表与第七章格式（只保留编排相关的条目）
    info = parse_document(tmp_path, [
        ("第三章 评标办法（综合评估法）", 1),
        [["条款号", "条款内容", "", "评分标准"],
         ["2.2.4（1）", "承包人建议书评分标准（4分）", "项目详细说明和总体管理方案、目标 0-2分", ""],
         ["", "", "项目管理机构设置与人员配备计划（含设计） 0-2分", ""],
         ["2.2.4（2）", "设计组织技术措施评分标准（8分）", "1、设计组织技术措施评分标准（6分）", "设计质量保证体系及措施 0-2分"],
         ["", "", "", "风险防范措施合理可行 0-2分"],
         ["", "", "", "重要设计节点安排等方面有针对性表述 0-2分"],
         ["", "", "2、设计进度安排（2分）", "有设计进度计划表 0-2分"],
         ["2.2.4（3）", "实施方案评分标准（6分）", "施工方案与技术措施", "施工方法合理、可行。0-6分"]],
        ("第七章 投标文件格式", 1),
        ("一、投标函及投标函附录", 1), ("（一）投标函", 1), "（招标人名称）：",
        "我方已仔细研究了 （项目名称）招标文件的全部内容。",
        ("（二）投标函附录", 1), [["投标有效期", "日历天"]],
        ("二、法定代表人身份证明", 1), "系 （投标人名称）的法定代表人。",
        ("三、承包人建议书", 1), "根据承包人建议书评分标准进行编制",
        ("四、设计组织技术措施", 1), "根据设计组织技术措施评分标准进行编制",
        ("五、承包人实施计划", 1), "根据实施方案评分标准进行编制",
        ("六、承诺书", 1), ("（一）企业信誉承诺书", 1), "致： ：（招标人名称）",
        ("第八章 其他内容", 1),
    ])
    profile = deepcopy(DEFAULT_PROFILE)
    profile["company"]["name"] = "测试建设有限公司"
    ctx = ComposeContext(tender=info.to_dict(), profile=profile, lots=[], options={"includeScoreIndex": True})
    return info, compose_document(ctx)


def test_integrated_bid_keeps_tender_order_and_splits_scoring_points(tmp_path):
    info, roots = compose_integrated(tmp_path)
    assert info.format_layout == "integrated"
    assert info.criteria[1].factor == "项目管理机构设置与人员配备计划（含设计）"
    assert [root.heading for root in roots] == [
        "一、投标函及投标函附录", "二、法定代表人身份证明", "三、承包人建议书", "四、设计组织技术措施",
        "五、承包人实施计划", "六、承诺书", "附录  评分标准响应索引",
    ]
    assert [root.key.split(".")[0] for root in roots[:6]] == [
        "business", "business", "technical", "technical", "technical", "business"]
    assert [child.heading for child in roots[0].children] == ["（一）投标函", "（二）投标函附录"]
    assert [child.heading for child in roots[2].children] == [
        "3.1 项目详细说明和总体管理方案、目标", "3.2 项目管理机构设置与人员配备计划（含设计）"]
    assert [child.heading for child in roots[3].children] == [
        "4.1 设计质量保证体系及措施", "4.2 风险防范措施", "4.3 重要设计节点安排", "4.4 设计进度安排"]
    assert [child.heading for child in roots[4].children] == ["5.1 施工方案与技术措施"]
    letter = roots[0].children[0].plain_text()
    assert "（招标人名称）" not in letter and "（项目名称）" not in letter
    assert "测试建设有限公司" in roots[1].plain_text()
    assert "根据承包人建议书评分标准进行编制" not in roots[2].plain_text()


def test_fixed_point_criteria_render_in_score_index(tmp_path):
    _info, roots = compose_integrated(tmp_path)
    rows = [row for child in roots[-1].children for block in child.blocks for row in block["rows"]]
    by_factor = {row[2]: row for row in rows}
    assert by_factor["设计组织技术措施评分标准"][3] == "6"  # “（6分）”定额项没有下限，不得报错
    assert by_factor["项目详细说明和总体管理方案、目标"][3] == "0～2"
    assert by_factor["设计组织技术措施评分标准"][4].endswith("4.3 重要设计节点安排")


def test_tender_library_slots_map_to_their_own_certificate_categories():
    from api.services.bidgen.library_fields import certificate_category

    # 投标资料库页面的槽位 → 证件类别（安全生产许可证不是职业健康安全管理体系证书）
    assert certificate_category("safety", "安全生产许可证") == "safety_license"
    assert certificate_category("qualification", "企业资质证书") == "qualification"
    assert certificate_category("bond", "投标保证金缴存回单") == "bid_bond"
    assert certificate_category("bank_permit", "开户许可证") == "bank_permit"
    assert certificate_category("finance", "近三年财务审计报告 / 完税证明") == "finance_report"
    assert certificate_category("social", "社保缴纳证明") == "social_insurance"
    assert certificate_category("perf", "类似项目合同及发票") == "performance_proof"
    assert certificate_category("id_agent", "授权代理人身份证及近半年社保缴纳证明") == "agent_id"
    assert certificate_category("iso", "ISO/质量管理体系认证证书") == "quality_system"


def test_procurement_response_format_follows_its_own_directory(tmp_path):
    # 人工摘录：竞争性谈判采购文件（供应商须知 / 评审办法 / 响应文件格式），目录行无标题样式、两个“二、”二选一
    info = parse_document(tmp_path, [
        ("第一章 采购公告", 1), "项目名称：某厂区生产运营数字化改造项目", "采购人：河南汇融数字科技有限公司",
        ("第二章 供应商须知", 1), "供应商须知前附表",
        [["条款号", "条款名称", "内容"], ["3.4", "响应文件有效期", "提交电子响应文件截止之日起90日历天。"]],
        ("第三章 评审办法", 1),
        [["条款号及名称", "评分因素", "评分标准"],
         ["7", "报价部分 （35分）", "响应报价 （35分）", "有效报价等于评审基准价的，得基本分30分"],
         ["9", "技术部分 （50分）", "需求理解 （5分）", "对项目背景、建设目标具有深度理解"],
         ["", "", "运维保障方案 （5分）", "提供12个月免费质保及运维保障服务"]],
        ("第六章 响应文件格式", 1), "谈 判 响 应 文 件", "目录",
        "一、响应函", "二、单位负责人身份证明（适用无委托代理人的情况）", "二、授权委托书（适用有委托代理人的情况）",
        "三、报价表", "九、技术响应文件",
        "一、响应函", "根据贵方 （项目名称） 的采购文件，我单位自愿参加谈判。",
        ("二、单位负责人身份证明", 2), "系 （供应商名称）的单位负责人。",
        "二、授权委托书（适用有委托代理人的情况）", "本人系（供应商名称）的法定代表人。",
        ("备注：委托代理人未签字不影响效力。\n三、报价表", 2), [["项目名称", ""]],
        ("九、技术响应文件", 2), "1.需求理解", "2.运维保障方案",
        ("第七章 其他", 1),
    ], name="9.30发布稿-某厂区生产运营数字化改造项目.docx")
    assert info.files[0]["role"] == "main"
    assert info.format_layout == "integrated"
    assert [(item.number, item.title) for item in info.format_items] == [
        ("1", "响应函"), ("2", "单位负责人身份证明"), ("2", "授权委托书（适用有委托代理人的情况）"),
        ("3", "报价表"), ("9", "技术响应文件")]
    assert info.format_items[2].template[-1] == {"kind": "paragraph", "text": "备注：委托代理人未签字不影响效力。"}
    assert info.format_items[-1].volume == "技术文件"
    assert [(c.group, c.factor, c.max_score) for c in info.criteria] == [
        ("报价部分", "响应报价", 35), ("技术部分", "需求理解", 5), ("技术部分", "运维保障方案", 5)]
    assert info.validity_days == 90
    assert info.purchasers == ["河南汇融数字科技有限公司"]

    from copy import deepcopy
    from api.services.bidgen.composer import compose_document
    from api.services.bidgen.document_model import ComposeContext
    from api.services.bidgen.profile import DEFAULT_PROFILE

    profile = deepcopy(DEFAULT_PROFILE)
    profile["company"]["name"] = "测试软件有限公司"
    roots = compose_document(ComposeContext(tender=info.to_dict(), profile=profile, lots=[], options={"includeScoreIndex": False}))
    assert [root.heading for root in roots] == [
        "一、响应函", "二、单位负责人身份证明", "二、授权委托书（适用有委托代理人的情况）", "三、报价表", "九、技术响应文件"]
    assert [child.heading for child in roots[-1].children] == ["9.1 需求理解", "9.2 运维保障方案"]
    technical_text = roots[-1].plain_text()
    assert all(block["type"] != "placeholder" for node in roots[-1].walk() for block in node.blocks)  # 无专门写法时由通用编写器写出正文
    assert "施工" not in technical_text  # 软件采购不套用施工写法
    assert "测试软件有限公司" in roots[1].plain_text()


def test_unrecognised_document_is_blocked_instead_of_using_default_directory(tmp_path):
    import pytest
    from api.services.bidgen.service import BidGenService

    document = Document()
    document.add_paragraph("会议纪要：本文件不是招标文件。")
    source = tmp_path / "会议纪要.docx"
    document.save(source)
    service = BidGenService(tmp_path / "storage", 1)
    task = service.create_task(source.name, source)
    with pytest.raises(ValueError, match="未能从“会议纪要.docx”中识别出投标（响应）文件格式章节"):
        service.generate(task["id"], {})


def test_task_parsed_by_older_rules_is_reparsed_before_generation(tmp_path):
    import json
    from api.services.bidgen.service import PARSER_VERSION, BidGenService

    document = Document()
    document.add_heading("第二章 投标人须知", 1)
    document.add_heading("第三章 评标办法", 1)
    document.add_heading("第七章 投标文件格式", 1)
    document.add_heading("一、投标函", 1)
    document.add_paragraph("（招标人名称）：")
    source = tmp_path / "招标文件正文.docx"
    document.save(source)
    service = BidGenService(tmp_path / "storage", 1)
    task = service.create_task(source.name, source)
    state_file = tmp_path / "storage" / "bidgen" / "tasks" / "1" / task["id"] / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    # 模拟旧规则解析出的结果：没有格式目录、没有版本号
    state["tender"]["format_items"] = []
    state.pop("parserVersion")
    state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    result = service.generate(task["id"], {})["result"]
    assert result["outline"][0]["heading"] == "一、投标函"
    assert json.loads(state_file.read_text(encoding="utf-8"))["parserVersion"] == PARSER_VERSION
