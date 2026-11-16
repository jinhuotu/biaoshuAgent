"""整本 EPC 招标文件的技术标编写：项目事实抽取、按评分要点写正文、格式表单填报。

期望值均为人工从郑东新区充电桩 EPC 招标文件原文摘录，不由被测实现生成。
"""

from copy import deepcopy

from docx import Document

from api.services.bidgen.archive import ArchiveEntry
from api.services.bidgen.composer import compose_document
from api.services.bidgen.document_model import ComposeContext
from api.services.bidgen.epc_writer import split_days
from api.services.bidgen.profile import DEFAULT_PROFILE
from api.services.bidgen.tender_parser import parse_tender_package

PROJECT = "郑州市郑东新区分布式公共新能源充电桩建设项目（一期）EPC总承包"
SCOPE = ("设计招标范围：本项目的方案设计、初步设计、施工图设计、相关配套设计以及与工程竣工验收前的全部相关设计服务工作。"
         "施工招标范围：承包人须完成场站开放运营所需的全部施工、设备采购与安装调试、竣工验收及移交备案等全部工作。"
         "特别强调：依据供电答复单明确由供电公司投资建设的内容，由承包人免费无条件协调建设完成；"
         "外网市政电力排管须完成通管并满足供电公司各项技术规范要求，承包人向发包人移交本招标工程时，该部分须同步具备正式使用条件。"
         "承包人须无条件配合将本次招标范围内所有设备接入招标人指定运营平台。")
DUST = ("达到施工工地扬尘治理8个100%标准（即工地周边100%围挡、各类物料堆放100%覆盖、土方开挖及拆迁作业100%湿法作业、"
        "出入车辆100%清洗、施工现场路面100%硬化、渣土车辆100%密闭运输、涉土石方作业的施工工地100%安装在线视频监控、"
        "工地内非道路移动机械使用油品及车辆100%达标），对道路全面控尘。")


def build(tmp_path):
    document = Document()

    def heading(text):
        document.add_heading(text, 1)

    def rows(data):
        table = document.add_table(rows=0, cols=max(map(len, data)))
        for source in data:
            for cell, text in zip(table.add_row().cells, source):
                cell.text = text

    heading("第二章 投标人须知")
    document.add_paragraph("投标人须知前附表")
    rows([["条款号", "条款名称", "编列内容"],
          ["1.1.2", "招标人", "名称：郑州新发展基础设施建设有限公司"],
          ["1.1.4", "项目名称", PROJECT],
          ["1.1.5", "建设地点", "郑州市郑东新区"],
          ["1.2.1", "资金来源及比例", "资金来源：专项债券资金+企业自筹资金 比例：100%"],
          ["1.3.1", "招标范围", SCOPE],
          ["1.3.2", "计划工期", "单个场站150日历天"],
          ["1.3.3", "质量标准", "施工：工程质量符合国家现行规范和标准，质量达到合格工程标准。"],
          ["1.3.4", "安全要求", "确保不发生安全生产事故，杜绝人身伤亡事故。"],
          ["1.3.6", "工程概况", "新建一批新能源汽车充电桩，并完善变压器、排水、供配电、场地整理等附属设施工程。本项目合同估算价约3700万元。"],
          ["1.11.2", "投标人拟分包的工作", "不允许"],
          ["3.2.4", "最高投标限价（招标控制价）", "2.工程费最高投标限价：36570000.00元"]])
    heading("第三章 评标办法（综合评估法）")
    rows([["条款号", "条款内容", "", "评分标准"],
          ["2.2.4（1）", "承包人建议书评分标准（2分）", "项目详细说明和总体管理方案、目标 0-2分", ""],
          ["2.2.4（3）", "实施方案评分标准（9分）", "设备安装方案与技术措施", "安装方法合理、可行。0-6分"],
          ["", "", "工程进度计划与措施", "计划明确。0-3分"],
          ["2.2.4 (5)", "其他评分因素评分标准（2分）", "服务承诺评分标准（2分）",
           "（1）在施工期间不拖欠农民工工资，在农忙季节及业主资金不到位时，连续施工的承诺。0-1分"],
          ["", "", "", "（2）质保期内、质保期外的服务承诺。0-1分"]])
    heading("第四章 合同条款及格式")
    document.add_paragraph("关于农民工工资支付严格按照郑东新区“一金三制”等相关管理制度执行。")
    document.add_paragraph(DUST)
    document.add_paragraph("工程总承包项目经理每月在现场的时间要求：项目经理必须常驻施工现场，每月在现场工作时间不少于24天。")
    heading("第五章 发包人要求")
    document.add_paragraph("1.充电桩设备")
    rows([["1", "分体式直流充电机", "1、装机功率:240kW 2、4台250A直流充电终端 3、枪线长度:直流充电终端枪线长度不少于4米", "套", "品牌：华为、星星充电、特来电"],
          ["2", "分体式直流充电机", "1、装机功率:1200kW 2、含4台600A直流充电终端，12台250A直流充电终端", "套", "品牌：华为、星星充电、特来电"],
          ["3", "交流充电桩", "1、规格型号：7kW交流充电桩", "台", "品牌：华为、星星充电、特来电"]])
    document.add_paragraph("4.箱变")
    rows([["序号", "名称", "备注"], ["1", "变压器", "金盘科技、江苏华辰、正泰"]])
    document.add_paragraph("设计依据：《电动汽车分散充电设施工程技术标准》（GB51313-2018）；《电动汽车充电站设计标准》（GB/T50966-2024）、"
                           "《电动汽车分散充电设施工程技术标准》（GB/T51313-2018）；《河南省通用安装工程预算定额》（HA02-31-2016）。")
    heading("第七章 投标文件格式")
    heading("一、承包人建议书")
    document.add_paragraph("根据承包人建议书评分标准进行编制")
    heading("二、承包人实施计划")
    document.add_paragraph("根据实施方案评分标准进行编制")
    heading("三、服务承诺")
    heading("四、资格审查资料")
    heading("（一）投标人基本情况表")
    rows([["投标人名称", "", "", ""], ["注册地址", "", "邮政编码", ""], ["法定代表人", "姓名", "", ""], ["传 真", "", "成立时间", ""]])
    document.add_paragraph("投标人名称：")
    heading("第八章 其他")
    path = tmp_path / "招标文件正文.docx"
    document.save(path)
    entry = ArchiveEntry(path.name, path.name, [], 0, ".docx", path.stat().st_size, path.name)
    return parse_tender_package([entry], tmp_path, path.name)


def compose(info):
    profile = deepcopy(DEFAULT_PROFILE)
    profile["company"].update(name="测试建设有限公司", address="郑州市金水路1号", postcode="450000")
    profile["legalRep"]["name"] = "王某"
    ctx = ComposeContext(tender=info.to_dict(), profile=profile, lots=[], options={"includeScoreIndex": True})
    return ctx, compose_document(ctx)


def all_text(section):
    return section.plain_text()


def test_project_facts_come_from_tender_text(tmp_path):
    facts = build(tmp_path).project_facts
    assert facts["domain"] == "charging"
    assert facts["schedule_days"] == 150 and facts["schedule_unit"] == "单个场站"
    assert facts["funding"] == "专项债券资金+企业自筹资金" and facts["funding_ratio"] == "100%"
    assert facts["estimated_price"] == "3700万元"
    assert facts["engineering_price_cap"] == "3657万元"
    assert facts["subcontract_allowed"] is False
    assert facts["dc_powers"] == [240, 1200]
    assert facts["terminal_currents"] == [250, 600]
    assert facts["gun_length"] == "4米" and facts["ac_power"] == "7kW"
    assert len(facts["dust_standard"]) == 8 and facts["dust_standard"][0] == "工地周边100%围挡"
    assert facts["wage_system"] == "郑东新区“一金三制”"
    assert facts["attendance_days"] == "24"
    assert len(facts["special_requirements"]) == 3
    assert {"transformer", "dc_charger", "ac_charger", "platform", "power_supply", "drainage", "site_work"} <= set(facts["features"])
    codes = [item["code"] for item in facts["standards"]]
    assert codes == ["GB/T51313-2018", "GB/T50966-2024"]  # 同号 GB 与 GB/T 合并；预算定额不算技术标准


def test_scoring_points_get_written_content_from_facts(tmp_path):
    _ctx, roots = compose(build(tmp_path))
    proposal, plan, service = roots[0], roots[1], roots[2]
    overview = proposal.children[0]
    assert [child.heading for child in overview.children] == [
        "1.1.1 项目概况与建设内容理解", "1.1.2 项目特点、难点与建设条件分析", "1.1.3 总体管理方案", "1.1.4 总体管理目标"]
    text = all_text(overview)
    assert "单个场站150日历天" in text and "3700万元" in text and "专项债券资金+企业自筹资金" in text
    assert "250A／600A直流充电终端" in text and "240kW～1200kW" in text
    assert text.count("3700万元") == 1  # 工程概况引文不重复估算价
    install = all_text(plan.children[0])
    assert "7kW交流充电桩" in install and "枪线长度不少于4米" in install
    progress = plan.children[1]
    schedule = next(block for block in progress.blocks if block["type"] == "table" and "完成标志" in block["header"])
    assert schedule["rows"][-1][2] == "第150日"
    assert [child.heading for child in service.children] == ["3.1 农民工工资与连续施工承诺", "3.2 质保期内、质保期外的服务承诺"]
    assert "郑东新区“一金三制”" in all_text(service)
    assert all(block["type"] != "placeholder" for root in roots[:3] for node in root.walk() for block in node.blocks)


def test_form_table_and_label_lines_are_filled_from_profile(tmp_path):
    _ctx, roots = compose(build(tmp_path))
    basic = roots[3].children[0]
    grid = next(block for block in basic.blocks if block["type"] == "table")
    assert grid["rows"][0][:2] == ["投标人名称", "测试建设有限公司"]
    assert grid["rows"][1][:4] == ["注册地址", "郑州市金水路1号", "邮政编码", "450000"]
    assert grid["rows"][2][:3] == ["法定代表人", "姓名", "王某"]
    assert grid["rows"][3][1] == "—"  # 传真缺失按“—”填
    assert grid["rows"][3][3].startswith("【待填")  # 成立时间缺失留待填标记
    assert any(block.get("text") == "投标人名称：测试建设有限公司" for block in basic.blocks)


def test_split_days_keeps_total():
    assert split_days(150, [8, 32, 20, 22, 20, 26]) == [9, 38, 23, 26, 23, 31]
    assert sum(split_days(37, [5, 7, 11, 12, 4, 6])) == 37
