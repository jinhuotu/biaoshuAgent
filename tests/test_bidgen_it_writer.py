"""软件 / 信息化采购的技术响应：需求章节结构化 + 按评分要素逐条响应。

夹具为人工摘录的 MES 谈判采购文件片段，期望值逐字段手写，不由被测实现生成。
"""

from copy import deepcopy

from docx import Document

from api.services.bidgen.archive import ArchiveEntry
from api.services.bidgen.composer import compose_document
from api.services.bidgen.document_model import ComposeContext
from api.services.bidgen.it_writer import respond
from api.services.bidgen.profile import DEFAULT_PROFILE
from api.services.bidgen.tender_parser import parse_tender_package


def build(tmp_path):
    document = Document()

    def heading(text, level=1):
        document.add_heading(text, level)

    def rows(data):
        table = document.add_table(rows=0, cols=max(map(len, data)))
        for source in data:
            for cell, text in zip(table.add_row().cells, source):
                cell.text = text

    heading("第一章 采购公告")
    document.add_paragraph("项目名称：某厂区生产运营数字化改造项目")
    document.add_paragraph("采购人：河南汇融数字科技有限公司")
    document.add_paragraph("2.5 工期要求：合同签订之日起10日内人员进场，合同签订之日起90日内完成系统需求分析、设计、开发、测试、部署、优化、上线、试运行等工作，试运行期限不少于1个月。")
    document.add_paragraph("2.7质保期：项目最终验收通过之日起12个月，质保期内提供免费质保及运维保障服务。")
    heading("第二章 供应商须知")
    heading("第三章 评审办法")
    rows([["条款号及名称", "评分因素", "评分标准"],
          ["9", "技术部分 （20分）", "需求理解 （5分）", "对项目背景、建设目标、应用场景具有深度理解"],
          ["", "", "技术方案 （5分）", "包括但不限于系统的架构设计、安全设计、接口规范、性能指标"],
          ["", "", "计划与实施方案（5分）", "包含进度计划、组织人员保障"],
          ["", "", "运维保障方案 （5分）", "针对响应流程、服务方式、服务人员数量及能力水平等方面综合评定"]])
    heading("第五章 项目采购需求")
    heading("项目概述", 2)
    heading("项目背景", 3)
    document.add_paragraph("本项目旨在打通生产端与管理端的数据链路，解决当前信息不通、决策滞后的问题。")
    heading("建设目标", 3)
    document.add_paragraph("本项目旨在打造一套MES制造执行系统，配套建设移动端应用与金蝶云星空 ERP 集成。")
    heading("建设内容", 2)
    heading("建设主要内容", 3)
    document.add_paragraph("（一）MES 制造执行系统主体建设")
    document.add_paragraph("搭建完整的 MES 业务软件系统，覆盖计划、执行与质量。")
    heading("建设详细内容", 3)
    document.add_paragraph("1. 生产计划与排程")
    document.add_paragraph("搭建以智能排程引擎为核心的计划排程业务体系。")
    document.add_paragraph("1）订单管理：销售订单数据从金蝶云星空 ERP 系统自动同步，保留手工插单能力，应对客户临时加急业务。")
    document.add_paragraph("2）色系权重优先排程（粉末行业个性化需求）：优先同色工单集中生产，排程结果在甘特图上展示。")
    heading("技术要求", 2)
    heading("性能要求", 3)
    rows([["指标项", "要求标准"], ["响应时间", "关键业务操作 ≤ 3秒"], ["数据吞吐量", "≥ 200MB/s（SSD阵列）"]])
    heading("安全性要求", 3)
    heading("访问控制", 4)
    document.add_paragraph("客户端与服务器端采用SSL传输，通过用户名加口令的方式登录。")
    heading("项目建设要求", 2)
    heading("质量保证期要求", 3)
    document.add_paragraph("质量保证期内，对影响生产的软件故障提供7*24服务，半小时内响应，1小时内提供补救方案，2小时内恢复；对不影响生产的软件故障，提供7*24服务，半小时内响应，1日内恢复。")
    heading("第六章 响应文件格式")
    document.add_paragraph("谈 判 响 应 文 件")
    heading("一、响应函", 2)
    document.add_paragraph("（采购人名称）：")
    heading("九、技术响应文件", 2)
    for line in ("1.需求理解", "2.技术方案", "3.计划与实施方案", "4.运维保障方案"):
        document.add_paragraph(line)
    heading("第七章 其他")
    path = tmp_path / "采购文件.docx"
    document.save(path)
    entry = ArchiveEntry(path.name, path.name, [], 0, ".docx", path.stat().st_size, path.name)
    return parse_tender_package([entry], tmp_path, path.name)


def compose(info):
    profile = deepcopy(DEFAULT_PROFILE)
    profile["company"]["name"] = "测试软件有限公司"
    ctx = ComposeContext(tender=info.to_dict(), profile=profile, lots=[], options={"includeScoreIndex": False})
    return ctx, compose_document(ctx)


def test_requirement_tree_and_software_domain(tmp_path):
    info = build(tmp_path)
    facts = info.project_facts
    assert facts["domain"] == "it"
    titles = [node["title"] for node in facts["requirements"]]
    assert "生产计划与排程" in titles and "访问控制" in titles
    plan = next(node for node in facts["requirements"] if node["title"] == "生产计划与排程")
    assert [item["label"] for item in plan["items"]] == ["订单管理", "色系权重优先排程（粉末行业个性化需求）"]
    assert info.cover_title == "谈判响应文件"


def test_software_scoring_points_answer_requirements(tmp_path):
    info = build(tmp_path)
    ctx, roots = compose(info)
    technical = roots[-1]
    assert [child.heading for child in technical.children] == ["9.1 需求理解", "9.2 技术方案", "9.3 计划与实施方案", "9.4 运维保障方案"]
    understanding = technical.children[0].plain_text()
    assert "我方理解，本项目旨在打通生产端与管理端的数据链路" in understanding
    assert "与金蝶云星空ERP的双向集成" in understanding
    plan = technical.children[2]
    schedule = next(block for node in plan.walk() for block in node.blocks if block["type"] == "table" and "里程碑成果" in block["header"])
    assert schedule["rows"][0][:3] == ["项目启动与人员进场", "第1日", "第10日"]
    trial = next(row for row in schedule["rows"] if row[0] == "试运行与优化")
    assert trial[2] == "第90日"
    tech = technical.children[1]
    perf = next(block for node in tech.walk() for block in node.blocks if block["type"] == "table" and block["header"][:2] == ["指标项", "采购要求"])
    assert perf["rows"][1][3].startswith("SSD阵列存储")  # 数据吞吐量不被“吞吐”误配
    operation = technical.children[3]
    sla = next(block for node in operation.walk() for block in node.blocks if block["type"] == "table" and "响应时限" in block["header"])
    assert sla["rows"] == [["影响生产", "7*24服务", "半小时", "1小时", "2小时"], ["不影响生产", "7*24服务", "半小时", "—", "1日"]]
    assert [child.title for child in operation.children][1:] == ["响应流程", "服务方式", "服务人员数量及能力水平"]
    assert all(block["type"] != "placeholder" for node in technical.walk() for block in node.blocks)


def test_respond_rewrites_requirement_voice_without_breaking_words():
    assert respond("供应商应提供详细的培训计划") == "我方提供详细的培训计划。"
    assert respond("系统应支持与第三方系统的数据对接") == "系统支持与第三方系统的数据对接。"
    assert respond("保留手工插单能力，应对客户临时加急业务") == "保留手工插单能力，应对客户临时加急业务。"


def test_business_scoring_items_respond_from_company_profile():
    from api.services.bidgen.business_response import is_scoring_text, write_business_point

    profile = deepcopy(DEFAULT_PROFILE)
    profile["company"]["name"] = "测试软件有限公司"
    profile["certificates"] = [{"id": "q1", "category": "quality_system", "name": "ISO9001质量管理体系认证证书", "number": "",
                                "issuer": "", "validFrom": "", "validTo": "2028-01-01", "perpetual": False}]
    profile["performances"] = [
        {"project": "某公司MES系统建设项目", "buyer": "甲公司", "product": "", "quantity": "", "unit": "", "year": "2024"},
        {"project": "某公司MES系统实施", "buyer": "乙公司", "product": "", "quantity": "", "unit": "", "year": "2021"},
        {"project": "某公司OA系统", "buyer": "丙公司", "product": "", "quantity": "", "unit": "", "year": "2025"},
    ]
    ctx = ComposeContext(tender={"cover_title": "谈判响应文件", "purchasers": ["采购人甲"], "project_name": "测试项目"},
                         profile=profile, lots=[], options={})
    # 人工摘录的评分原文
    certificates = write_business_point(ctx, "资质证书", "供应商具有以下有效证书的： 1.具有高新技术企业证书的，得1分； 2.具有质量管理体系认证证书的，得1分；", [])
    rows = next(block for block in certificates.blocks if block["type"] == "table")["rows"]
    assert [row[1] for row in rows] == ["高新技术企业证书", "质量管理体系认证证书"]
    assert rows[0][3].startswith("【待填") and rows[1][3] == "ISO9001质量管理体系认证证书"
    performance = write_business_point(ctx, "业绩要求", "供应商另外2023年1月1日以来MES相关系统建设或实施的合同业绩。每满足一项合同业绩得2分，此项最高得6分。",
                                       [[["序号", "项目名称", "委托单位", "合同金额（元）", "合同签订时间"]]])
    table_rows = next(block for block in performance.blocks if block["type"] == "table")["rows"]
    assert table_rows == [["1", "某公司MES系统建设项目", "甲公司", "见合同", "2024"]]  # 2021年与非MES业绩不计
    team = write_business_point(ctx, "团队配置", "2.供应商自行承诺：①供应商拟派项目团队核心人员数量不少于4人；②项目启动后供应商需要在5日内保证人员到位。有此承诺得2分。", [])
    text = "\n".join(block.get("text", "") for block in team.blocks)
    assert "①我方拟派项目团队核心人员数量不少于4人。" in text and "②项目启动后我方在5日内保证人员到位。" in text
    assert team.blocks[-1]["lines"][0] == "供应商：测试软件有限公司（盖单位章）"
    assert is_scoring_text("同一成员具有多个上述证书的，按一个证书计分。") and not is_scoring_text("此表为业绩汇总表。")
