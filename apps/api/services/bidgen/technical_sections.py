"""技术文件各章节的内容生成规则。"""

from __future__ import annotations

import re

from api.services.bidgen.business_sections import (
    NO_DEVIATION,
    _applicable_supplements,
    _child,
    _find_criteria,
    _has_certificate,
    _validity_text,
    certificate_blocks,
)
from api.services.bidgen.cable_knowledge import (
    BASIS_COPY,
    BASIS_LABELS,
    BASIS_BOUND,
    derive_component_response,
    derive_guarantee,
    parse_cable_features,
)
from api.services.bidgen.document_model import ComposeContext, Section, paragraph, placeholder, table, title_line

# 电缆主要工序：工序 → (工艺文件, 关键控制参数, 检验项目)
_CABLE_PROCESSES = (
    ("拉丝", "《铜（铝）大拉、中拉工艺卡》", "单丝直径及公差、退火电压与电流、拉丝液浓度与温度、收线张力", "单丝直径、伸长率、电阻率、表面质量"),
    ("绞线", "《导体绞制工艺卡》", "绞合节径比、紧压系数、紧压外径、绞向、模具配置", "导体外径、20℃直流电阻、结构根数、表面质量"),
    ("交联", "《三层共挤交联生产工艺卡》", "挤出各区温度、交联管温度分布、线速度、氮气压力、冷却水温", "绝缘及屏蔽厚度、偏心度、热延伸、微孔杂质、界面突起"),
    ("成缆", "《成缆工艺卡》", "成缆节距与绞向、填充方式、绕包带搭盖率、成缆外径", "成缆外径、不圆度、相序标识、绕包质量"),
    ("装铠", "《铠装工艺卡》", "钢带规格与层数、绕包间隙、绕包节距与张力", "钢带间隙率、铠装外径、搭盖情况、表面质量"),
    ("挤塑", "《内衬层 / 外护套挤出工艺卡》", "塑化温度、挤出速度、模芯模套配置、冷却分段、火花试验电压", "护套厚度及最薄点、外径、火花试验、印字标识"),
)
_MATERIAL_DOCUMENTS = (
    ("供应商筛选制度", "《供应商筛选与评价管理制度》", "规定供方准入条件、现场审核、样品验证、年度业绩评价及退出机制"),
    ("进厂检验文件", "《原材料及组部件进厂检验规程》", "规定各类原材料的抽样方案、检验项目、判定标准及不合格处置"),
    ("标识与存放管理文件", "《物料标识与存放管理规定》", "规定物料状态标识、批次标识、分区存放、防潮防尘及先进先出要求"),
    ("出入库管理文件", "《仓库出入库管理制度》", "规定入库验收、领用发放、台账记录、库存盘点及可追溯要求"),
)
_QUALITY_CHECKPOINTS = (
    ("原材料进厂", "铜（铝）杆、绝缘料、屏蔽料、铜带、钢带、护套料", "外观、尺寸、电阻率、机械物理性能、热延伸、杂质含量", "批批检验，合格后入库"),
    ("拉丝退火", "单丝", "直径、伸长率、电阻率、表面氧化", "首检 + 每盘巡检"),
    ("导体绞制", "紧压导体", "外径、直流电阻、节距、紧压系数", "首检 + 每盘检验"),
    ("三层共挤交联", "绝缘线芯", "绝缘厚度、偏心度、屏蔽厚度、热延伸、微孔杂质", "在线测偏 + 每盘取样"),
    ("金属屏蔽", "屏蔽线芯", "铜带厚度、搭盖率、屏蔽直流电阻", "首检 + 巡检"),
    ("成缆", "缆芯", "成缆节距、外径、不圆度、绕包质量", "首检 + 每盘检验"),
    ("内衬 / 铠装", "铠装缆芯", "内衬层厚度、钢带间隙、铠装外径", "首检 + 巡检"),
    ("外护套挤出", "成品电缆", "护套厚度、外径、火花试验、印字", "在线火花 + 每盘检验"),
    ("成品出厂", "成品电缆", "导体直流电阻、局部放电、工频电压试验、结构尺寸", "逐盘例行试验"),
)
_ROUTINE_TESTS = (
    ("导体直流电阻试验", "例行试验", "GB/T 3048.4", "逐盘", "不大于 GB/T 3956 规定值"),
    ("局部放电试验", "例行试验", "GB/T 3048.12", "逐盘", "1.73U0 下无任何可检测到的放电"),
    ("工频电压试验", "例行试验", "GB/T 3048.8", "逐盘", "按技术规范书规定电压和时间，绝缘不击穿"),
    ("结构尺寸检查", "抽样试验", "GB/T 2951.11", "按批抽样", "绝缘、护套厚度及外径符合标准和技术规范"),
    ("绝缘热延伸试验", "抽样试验", "GB/T 2951.21", "按批抽样", "负荷下伸长率 ≤125%，冷却后永久伸长率 ≤10%"),
    ("4h 电压试验", "抽样试验", "GB/T 12706.2", "按批抽样", "4U0 电压 4h 不击穿"),
    ("绝缘、护套机械性能", "型式试验", "GB/T 2951.11", "按型式试验周期", "老化前后抗张强度、断裂伸长率符合标准"),
    ("燃烧性能试验", "型式试验", "GB/T 18380", "按型式试验周期", "满足规定的阻燃类别要求"),
)
_TRAINING_PLAN = (
    ("产品结构与性能", "电缆结构、材料特性、主要技术参数及执行标准", "运行维护人员、物资管理人员", "2 学时"),
    ("到货验收要点", "外观检查、盘具及端头密封检查、随货资料核对、抽检取样方法", "物资验收人员", "2 学时"),
    ("敷设与安装工艺", "最小弯曲半径、牵引方式与牵引力、敷设温度、固定间距、附件安装配合", "施工及监理人员", "4 学时"),
    ("运行维护与故障判别", "日常巡视要点、预防性试验、常见故障现象及处理", "运行维护人员", "2 学时"),
    ("实物 ID 应用", "二维码标签与 RFID 标签的识读、信息核对及维护", "物资管理及运维人员", "1 学时"),
)


def _spec_features(spec: dict):
    return parse_cable_features(spec.get("model", ""), spec.get("conductor", ""), spec.get("core_section", ""), spec.get("voltage", ""))


def _spec_label(spec: dict) -> str:
    return _spec_features(spec).label or spec.get("title") or spec["fixed_id"]


def build_technical_deviation(ctx: ComposeContext, section: Section) -> None:
    specs = ctx.tender.get("tech_specs", [])
    rows = [
        ["1", "执行标准", "技术规范书及其引用的国家、行业及企业标准", "完全按技术规范书及最新有效版本标准执行", NO_DEVIATION],
        ["2", "技术参数特性", "技术参数特性表“项目需求值或表述”", "投标人保证值逐项满足或优于项目需求值", NO_DEVIATION],
        ["3", "使用环境条件", "使用环境条件表各项要求", "完全满足使用环境条件要求", NO_DEVIATION],
        ["4", "组件材料配置", "组件材料配置表各项要求", "按要求配置，组件材料均来自合格供方并可追溯", NO_DEVIATION],
    ]
    if any("实物ID" in c["content"] or "实物ID" in c["name"] for c in ctx.tender.get("key_clauses", [])) or "实物ID" in str(ctx.tender.get("general_qualifications")):
        rows.append([str(len(rows) + 1), "实物 ID", "随物资提供并安装二维码标签和 RFID 电子标签", "完全按国家电网公司实物 ID 管理要求执行", NO_DEVIATION])
    rows.append([str(len(rows) + 1), "其他", "招标文件其余技术条款及各省公司补充技术要求", "全部响应", NO_DEVIATION])
    section.blocks += [
        title_line("技术偏差表"),
        paragraph(f"项目名称：{ctx.project_name}", indent=False),
        paragraph(f"招标编号：{ctx.tender_no}　　分标名称：{ctx.lot_names}", indent=False),
        paragraph(f"分标编号：{ctx.lot_codes}　　包号：{ctx.package_no}", indent=False),
        table(["序号", "偏差事项", "招标文件要求", "投标文件响应", "偏差说明"], rows, widths=[1.2, 2.8, 5.0, 5.0, 2.4]),
        paragraph("投标人声明：针对本招标标的，除本表已列明偏差外，我们接受招标文件规定的其余全部技术条件，并承诺按照招标文件规定的技术条件提供对应服务。"),
        paragraph(f"说明：我方已对技术规范书（共 {len(specs)} 份固化 ID 技术规范）列明的全部技术参数逐项响应，无任何负偏差。" if specs else "说明：我方对招标文件全部技术条款完全响应，无任何技术偏差。"),
        ctx.signature_block(),
    ]


def build_performance(ctx: ComposeContext, section: Section) -> None:
    performances = ctx.profile["performances"]
    criteria = _find_criteria(ctx, "technical", "供货业绩")
    section.criteria += [c["criterion_id"] for c in criteria]
    section.blocks.append(paragraph("根据招标文件第一章投标邀请书“投标人在资格预审项目中提交的资格预审申请文件将作为投标文件的一部分，本次投标无需重复提交”之规定，且第六章投标文件格式目录未要求提交业绩文件，本项按招标文件要求不再重复提交业绩证明原件。"))
    requirement = "；".join(dict.fromkeys(f"{lot['lot_name']}：{lot['performance_requirement']}" for lot in ctx.lots if lot.get("performance_requirement")))
    if requirement:
        section.blocks.append(paragraph(f"资格预审公告合同业绩要求：{requirement}。"))
    if performances:
        section.blocks += [
            paragraph("为便于评标委员会了解我公司供货能力，现将近年主要同类产品供货业绩汇总如下（证明材料已在资格预审申请文件中提交）："),
            table(
                ["序号", "项目 / 合同名称", "买方", "产品", "供货数量", "签订年份"],
                [[str(i), p.get("project", ""), p.get("buyer", ""), p.get("product", ""), f"{p.get('quantity', '')}{p.get('unit', '')}", str(p.get("year", ""))] for i, p in enumerate(performances, start=1)],
            ),
        ]
    else:
        section.not_applicable = True


def build_parameter_tables(ctx: ComposeContext, section: Section) -> None:
    specs = ctx.tender.get("tech_specs", [])
    section.blocks.append(paragraph(f"项目名称：{ctx.project_name}；招标编号：{ctx.tender_no}；分标名称：{ctx.lot_names}；包号：{ctx.package_no}。"))
    if not specs:
        section.blocks += [
            paragraph("招标压缩包中未识别到技术规范书，请按技术规范书《技术特性参数表》抄录项目需求值并逐项填写投标人保证值。"),
            table(["序号", "名称", "单位", "招标人要求值", "投标人保证值"], [["1", ctx.need("技术特性参数"), "", "", ""]]),
        ]
        return
    section.blocks += [
        paragraph(f"我方已抄录招标文件技术规范书（共 {len(specs)} 份，按固化 ID 编制）《技术参数特性表》《使用环境条件表》的全部数据，并在“投标人保证值”栏逐条逐项作出实质性响应。标“※”为关键参数，标“△”为需报告支撑参数。"),
        table(
            ["序号", "固化 ID", "产品型号", "导体材料", "芯数×截面（mm²）", "电压等级"],
            [[str(i), s["fixed_id"], s.get("model", ""), s.get("conductor", ""), s.get("core_section", ""), s.get("voltage", "")] for i, s in enumerate(specs, start=1)],
            caption="本包技术规范书清单",
        ),
    ]
    for index, spec in enumerate(specs, start=1):
        features = _spec_features(spec)
        child = _child(section, f"spec{index}", f"{_spec_label(spec)}（固化 ID：{spec['fixed_id']}）")
        ctx.within_section(child.key)
        for spec_table in spec["tables"]:
            if "组件" in spec_table["title"] or len(spec_table["header"]) < 5:
                continue
            rows = []
            for row in spec_table["rows"]:
                cells = (row + [""] * 6)[:6]
                value, basis = derive_guarantee(cells[1], cells[3], features)
                if basis not in (BASIS_COPY, BASIS_BOUND):
                    ctx.review_items.append(
                        {
                            "section": child.key,
                            "spec": _spec_label(spec),
                            "fixedId": spec["fixed_id"],
                            "number": cells[0],
                            "name": cells[1],
                            "required": cells[3],
                            "value": value,
                            "basis": basis,
                            "basisLabel": BASIS_LABELS[basis],
                        }
                    )
                rows.append([cells[0], cells[1], cells[2], cells[3], value, cells[5]])
            child.blocks.append(
                table(
                    ["序号", "参数名称", "单位", "项目需求值或表述", "投标人保证值", "备注"],
                    rows,
                    caption=spec_table["title"],
                    widths=[1.3, 5.6, 1.8, 3.8, 3.0, 1.2],
                )
            )
    section.blocks.append(paragraph("编制说明：上表“项目需求值或表述”抄录自招标文件技术规范书，“投标人保证值”为我方对应产品的保证值，与投标工具中填报的技术特性参数保持一致。"))


def build_component_tables(ctx: ComposeContext, section: Section) -> None:
    specs = ctx.tender.get("tech_specs", [])
    section.blocks.append(paragraph(f"项目名称：{ctx.project_name}，招标编号：{ctx.tender_no}，分标名称：{ctx.lot_names}，分标编号：{ctx.lot_codes}，包号：{ctx.package_no}。"))
    component_specs = [(s, t) for s in specs for t in s["tables"] if "组件" in t["title"]]
    if not component_specs:
        section.blocks.append(table(["序号", "元件名称", "型式规格、参数", "单位", "数量", "投标人响应（型式、规格）", "制造商", "原产地"], [["1.1", ctx.need("组件材料配置"), "", "", "", "", "", ""]]))
        return
    manufacturer = ctx.need("主要原材料制造商")
    for index, (spec, spec_table) in enumerate(component_specs, start=1):
        child = _child(section, f"spec{index}", f"{_spec_label(spec)}（固化 ID：{spec['fixed_id']}）")
        features = _spec_features(spec)
        rows = [["1、本体", f"{_spec_label(spec)} 交联聚乙烯绝缘电力电缆", "—", "—", "—", "—", "—", "—"]]
        for number, row in enumerate(spec_table["rows"], start=1):
            cells = (row + [""] * 7)[:7]
            name = cells[1]
            if ("PE" in name and "PVC" not in name and "聚乙烯" not in features.model and not re.search(r"6[23]$|Y$", features.model)) and "外护套" in name:
                response = "不适用（本规格采用 PVC 外护套）"
            elif "PVC" in name and "外护套" in name and re.search(r"6[23]$", features.model):
                response = "不适用（本规格采用聚乙烯外护套）"
            else:
                response = derive_component_response(name)
            rows.append([f"1.{number}", name, cells[3], cells[2] if cells[2] != "无" else "—", cells[4], response, ctx.company_name if "电缆" in name and "料" not in name else manufacturer, "中国"])
        rows += [
            ["2、必备的备品备件", "无（电缆产品不涉及）", "—", "—", "—", "—", "—", "—"],
            ["3、专用工具和仪器仪表", "无（电缆产品不涉及）", "—", "—", "—", "—", "—", "—"],
        ]
        child.blocks.append(
            table(["序号", "元件名称", "型式规格、参数", "单位", "数量", "投标人响应（型式、规格）", "制造商", "原产地"], rows, widths=[1.5, 3.2, 2.2, 1.0, 1.0, 5.0, 2.2, 1.2])
        )
    section.blocks.append(paragraph("编制说明：以上组件材料均选自我公司合格供方名录，具有完整的进厂检验记录和可追溯性；实际供货时原材料制造商如有调整，将事先征得买方书面同意。"))


def build_technical_qualification(ctx: ComposeContext, section: Section) -> None:
    held = [c for c in ctx.profile["certificates"] if c["category"] in ("type_test", "ccc", "production_license", "quality_system", "environment_system", "safety_system", "energy_system")]
    section.blocks.append(paragraph("按第一章投标人资格要求，我方已取得下列报告及证书。资格预审申请文件中已提交且仍在有效期内的不再重复提供，过期需更新的证书复印件附后（加盖单位公章）。"))
    if held:
        section.blocks.append(
            table(
                ["序号", "报告 / 证书名称", "编号", "出具 / 发证机构", "有效期", "投标截止日状态"],
                [[str(i), c["name"], c["number"] or "见证书", c["issuer"] or "见证书", _validity_text(c), _status_at_deadline(ctx, c)] for i, c in enumerate(held, start=1)],
                widths=[1.2, 5.6, 2.6, 3.0, 2.4, 2.2],
            )
        )
    section.blocks += certificate_blocks(ctx, ("type_test", "ccc", "production_license"), "所投产品有效型式试验报告、认证证书复印件")


def _status_at_deadline(ctx: ComposeContext, certificate: dict) -> str:
    item = next((i for i in ctx.certificate_check.get("items", []) if i["id"] == certificate["id"]), None)
    return item["statusLabel"] if item else "—"


def build_technical_supplements(ctx: ComposeContext, section: Section) -> None:
    supplements = _applicable_supplements(ctx, "technical")
    section.blocks.append(paragraph("按照招标文件第一章投标邀请书关于“投标人须在技术投标文件中提供补充支持证明材料”的要求，我方逐项提交如下："))
    for index, item in enumerate(supplements, start=1):
        if "质量控制" in item["item"]:
            criteria = _find_criteria(ctx, "technical", "产品制造质量控制")
            child = _child(section, f"s{index}", item["item"], criteria=[c["criterion_id"] for c in criteria])
            child.blocks.append(paragraph(f"招标文件要求：{item['note']}"))
            _quality_control_evidence(ctx, child)
        elif "绿色" in item["item"]:
            criteria = _find_criteria(ctx, "technical", "绿色低碳", "碳足迹")
            child = _child(section, f"s{index}", item["item"], criteria=[c["criterion_id"] for c in criteria])
            child.blocks.append(paragraph(f"招标文件要求：{item['note']}"))
            _green_manufacturing(ctx, child)
        else:
            child = _child(section, f"s{index}", item["item"])
            child.blocks += [paragraph(f"招标文件要求：{item['note']}"), paragraph("我方满足该项要求，证明材料如下。"), placeholder(f"（此处粘贴：{item['item']}相关证明材料）")]
    if not supplements:
        section.blocks.append(paragraph("招标文件未对所投分标提出需补充的技术支撑证明材料，本项无补充内容。"))


def _quality_control_evidence(ctx: ComposeContext, section: Section) -> None:
    systems = ctx.profile["systems"]
    _child(
        section,
        "incoming",
        "原材料组部件进厂检验检测措施",
        paragraph(f"{ctx.company_name}制定了《原材料及组部件进厂检验规程》《供应商筛选与评价管理制度》《物料标识与存放管理规定》《仓库出入库管理制度》，对导体、绝缘料、半导电屏蔽料、铜带、钢带、护套料等关键原材料实行“批批检验、批批建档”。"),
        table(
            ["原材料", "检验项目", "检验方式", "判定依据"],
            [
                ["铜杆 / 铝杆", "外观、直径、电阻率、抗拉强度、伸长率", "每批抽检", "GB/T 3952、GB/T 3954"],
                ["XLPE 绝缘料", "外观洁净度、熔融指数、热延伸、机械性能、介电性能", "每批抽检 + 供方合格证", "JB/T 10437"],
                ["半导电屏蔽料", "体积电阻率、热延伸、剥离力、机械性能", "每批抽检 + 供方合格证", "JB/T 10738"],
                ["铜带", "厚度、宽度、电阻率、抗拉强度、伸长率", "每批抽检", "GB/T 11091"],
                ["镀锌钢带", "厚度、宽度、镀锌层重量、抗拉强度", "每批抽检", "YB/T 024"],
                ["护套料", "机械性能、热老化、热失重、氧指数", "每批抽检 + 供方合格证", "GB/T 8815、GB/T 15065"],
            ],
            widths=[3.0, 7.2, 3.4, 2.8],
        ),
        paragraph("检验合格的原材料方可办理入库并粘贴合格标识；不合格原材料隔离存放并按《不合格品控制程序》退货或让步处理。检验记录与供应商检测合格证明一并归档，实现原材料组部件全过程可追溯。"),
        placeholder("（此处粘贴：进厂检验规程、检验记录及供应商检测合格证明样例）", height=4.5),
    )
    _child(
        section,
        "process",
        "生产工艺与质量管控体系",
        paragraph("公司通过质量管理体系认证，工艺文件覆盖" + "、".join(p[0] for p in _CABLE_PROCESSES) + "等全部主要工序，各工序严格按工艺文件和作业指导书执行，设置工序质量控制点，实行“自检、互检、专检”三检制，上道工序不合格不得流入下道工序。"),
        paragraph("生产过程中每盘半成品均附流转卡，记录操作者、设备、工艺参数、检验结果及原材料批号，成品可逆向追溯至原材料批次与生产班组。"),
    )
    _child(
        section,
        "environment",
        "生产制造环境与试组装环境",
        paragraph("绝缘线芯三层共挤交联工序设置洁净上料间，绝缘料采用封闭式自动上料系统，净化等级满足中压电缆生产要求；车间实行 6S 管理，温湿度、粉尘受控。"),
        paragraph("公司设有独立的试制 / 试组装区域和成品检测中心，新产品及工艺变更产品先行试制并经型式试验验证后方可批量生产。"),
        placeholder("（此处粘贴：生产车间、洁净上料间、检测中心现场照片）", height=5),
    )
    _child(
        section,
        "digital",
        "智能制造与数字化质量管控",
        paragraph("公司应用" + ("、".join(systems) if systems else ctx.need("MES / 智能仓储等系统软件")) + f"等系统软件（共 {len(systems)} 项），对生产制造的订单排产、原材料检测、工艺控制、出厂试验、质量评价等环节进行物联采集及自动监控，工艺参数超限自动报警，质量数据全程留存、可追溯。"),
        placeholder("（此处粘贴：系统界面截图及应用说明）", height=5),
    )


def _green_manufacturing(ctx: ComposeContext, section: Section) -> None:
    factory = _has_certificate(ctx, "green_factory")
    supply_chain = _has_certificate(ctx, "green_supply_chain")
    footprint = _has_certificate(ctx, "carbon_footprint")
    held = factory + supply_chain
    section.blocks.append(
        paragraph(f"我公司已取得国家工信部认定的{'、'.join(c['name'] for c in held)}（共 {len(held)} 项），证书附后。" if held else "我公司已建立绿色生产和绿色回收制度（如已取得工信部绿色工厂、绿色供应链管理企业认定，请在企业资料中登记）。")
    )
    section.blocks += [
        paragraph("绿色低碳生产：（1）采用节能型拉丝、交联、挤出设备及变频控制技术，冷却水全部循环利用；（2）推行清洁生产审核，单位产品综合能耗逐年下降；（3）厂区建设分布式光伏、余热回收等节能项目；（4）优先选用无卤低烟、可回收的环保型原材料与包装材料。"),
        paragraph("绿色回收：制定《绿色回收管理制度》，废铜、废铝、废塑料、废电缆盘等实行分类收集、定点存放、台账管理，交由有资质的单位回收再生利用；对电缆盘具推行循环使用与回收服务。" if ctx.profile["declarations"]["greenRecycling"] else f"绿色回收制度：{ctx.need('绿色回收制度文件')}"),
        paragraph(f"产品碳足迹：我公司已取得{footprint[0]['name']}，证书附后。" if footprint else "产品碳足迹：我公司正按 ISO 14067 开展投标产品碳足迹核算（如已取得产品碳足迹证书，请在企业资料中登记并附证书）。"),
        placeholder("（此处粘贴：绿色工厂 / 绿色供应链认定文件、绿色低碳生产及绿色回收制度文件）", height=5),
    ]


def build_other_technical_documents(ctx: ComposeContext, section: Section) -> None:
    """工艺管理体系、生产 / 试验设备、人力资源、创新能力。"""
    section.blocks.append(paragraph("我方按招标文件规定及技术评分标准，提交工艺管理体系、生产试验设备、人力资源与创新能力说明如下。"))
    process_criteria = _find_criteria(ctx, "technical", "设备工艺")
    process = _child(section, "process", "工艺文件与原材料 / 组部件管理文件", criteria=[c["criterion_id"] for c in process_criteria])
    complete = ctx.profile["declarations"]["processDocsComplete"]
    process.blocks += [
        paragraph("我公司具有生产招标产品所需全部工序的工艺文件和作业指导书，能保证产品生产需要，各工序按工艺文件执行并具有可追溯性。" if complete else f"工艺文件齐全情况：{ctx.need('工艺文件是否齐全')}"),
        table(["工序", "工艺文件", "关键控制参数", "检验项目"], [list(p) for p in _CABLE_PROCESSES], caption="主要工序工艺文件一览表", widths=[1.6, 4.4, 5.6, 4.8]),
        table(["管理文件类别", "文件名称", "主要内容"], [list(d) for d in _MATERIAL_DOCUMENTS], caption="原材料 / 组部件管理文件一览表", widths=[3.6, 5.4, 7.4]),
        placeholder("（此处粘贴：工艺文件、原材料 / 组部件管理文件封面及目录页）", height=5),
    ]
    equipment_criteria = _find_criteria(ctx, "technical", "生产试验设备")
    equipment = _child(section, "equipment", "生产设备与试验设备", criteria=[c["criterion_id"] for c in equipment_criteria])
    ctx.within_section(equipment.key)
    owned = ctx.profile["equipment"]
    equipment.blocks += [
        paragraph("我公司拥有与投标产品相适应的成套生产设备，主要生产设备配置如下："),
        table(
            ["序号", "设备类别", "数量", "单位", "招标文件评分门槛"],
            [[str(i), e.get("name", ""), ctx.value(e.get("count"), f"{e.get('name', '设备')}数量"), e.get("unit", "套"), _equipment_threshold(equipment_criteria, e.get("name", ""))] for i, e in enumerate(owned, start=1)]
            or [["1", ctx.need("主要生产设备清单"), "", "", ""]],
            caption="主要生产设备一览表",
        ),
    ]
    tests = ctx.profile["testEquipment"]
    equipment.blocks.append(
        table(
            ["序号", "试验设备名称", "数量", "用途"],
            [[str(i), t.get("name", ""), str(t.get("count", "")), t.get("usage", "")] for i, t in enumerate(tests, start=1)]
            or [
                ["1", "工频串联谐振耐压试验系统", ctx.need("试验设备数量"), "成品工频电压试验"],
                ["2", "局部放电测试系统（屏蔽室）", "—", "局部放电试验"],
                ["3", "直流电阻电桥", "—", "导体及屏蔽直流电阻测量"],
                ["4", "热延伸试验装置", "—", "绝缘交联度（热延伸）试验"],
                ["5", "电子拉力试验机", "—", "绝缘、护套机械性能试验"],
                ["6", "老化试验箱", "—", "热老化试验"],
                ["7", "投影仪 / 测厚仪", "—", "绝缘、护套厚度及偏心度测量"],
            ],
            caption="主要试验检测设备一览表",
        )
    )
    equipment.blocks.append(placeholder("（此处粘贴：设备购置发票、设备照片等证明材料）", height=5))
    people_criteria = _find_criteria(ctx, "technical", "人力资源")
    staff = ctx.profile["staff"]
    _child(
        section,
        "people",
        "人力资源",
        paragraph(f"我公司现有员工 {ctx.value(staff['total'], '员工总人数')} 人，其中与投标产品相关的高级工程师（含高级技师）{ctx.value(staff['seniorEngineers'], '与投标产品相关的高级工程师（含高级技师）人数')} 人，高级职称 {staff['senior'] or '—'} 人、中级职称 {staff['middle'] or '—'} 人，技术、质量、生产、检验岗位人员配置齐全，均经培训考核后持证上岗。"),
        placeholder("（此处粘贴：高级工程师、高级技师职称证书及社保缴纳证明）", height=5),
        criteria=[c["criterion_id"] for c in people_criteria],
    )
    innovation_criteria = _find_criteria(ctx, "technical", "创新能力")
    innovation = _child(section, "innovation", "创新能力", criteria=[c["criterion_id"] for c in innovation_criteria])
    ctx.within_section(innovation.key)
    standards, patents, awards = ctx.profile["standards"], ctx.profile["patents"], ctx.profile["awards"]
    innovation.blocks += [
        paragraph("我公司坚持自主创新，积极参与标准制修订并持续取得专利与科技成果。"),
        table(["序号", "标准名称 / 编号", "标准级别"], [[str(i), s.get("name", ""), s.get("level", "")] for i, s in enumerate(standards, start=1)] or [["1", ctx.need("参与制定的与投标产品相关的标准"), ""]], caption="参与制定的标准"),
        table(["序号", "专利名称", "专利号", "专利类型"], [[str(i), p.get("name", ""), p.get("number", ""), p.get("kind", "")] for i, p in enumerate(patents, start=1)] or [["1", ctx.need("与所投产品相关的发明专利"), "", ""]], caption="与所投产品相关的专利"),
        table(["序号", "科技奖励名称", "级别", "年份"], [[str(i), a.get("name", ""), a.get("level", ""), str(a.get("year", ""))] for i, a in enumerate(awards, start=1)] or [["1", ctx.need("与投标产品相关的科技奖励"), "", ""]], caption="获得的科技奖励"),
        placeholder("（此处粘贴：标准封面及起草单位页、专利证书、科技奖励证书）", height=5),
    ]


def _equipment_threshold(criteria: list[dict], name: str) -> str:
    key = name.replace("生产线", "").replace("设备", "")
    for criterion in criteria:
        for line in criterion["rule"].splitlines():
            if key and key in line:
                matched = re.search(r"≥\s*\d+\s*[套条台]", line)
                if matched:
                    return matched.group(0)
    return "—"


def build_technical_service(ctx: ComposeContext, section: Section) -> None:
    service = ctx.profile["service"]
    section.blocks.append(paragraph(f"{ctx.company_name}将为本项目提供覆盖供货前、供货中、供货后全过程的技术服务，服务内容包括技术交底、监造配合、到货验收、安装指导、培训及运行技术支持。"))
    _child(
        section,
        "team",
        "技术服务组织机构",
        paragraph("我公司成立本项目专项服务组，由分管副总经理任组长，下设技术支持、生产协调、质量检验、物流交付和现场服务五个专业小组，实行项目经理负责制，对项目单位实行“一个窗口对接、全过程闭环管理”。"),
        table(
            ["岗位", "职责", "响应要求"],
            [
                ["项目经理", "统筹合同履约、对接项目单位、协调内部资源", "7×24 小时保持通讯畅通"],
                ["技术支持工程师", "技术澄清、图纸资料确认、技术问题答复", f"{service['responseHours'] or '2'} 小时内答复"],
                ["生产协调员", "排产计划、生产进度跟踪与通报", "每周通报生产进度"],
                ["质量检验员", "过程检验、出厂试验、配合监造与抽检", "按节点提交检验报告"],
                ["现场服务工程师", "到货验收配合、安装指导、现场培训", f"{service['arrivalHours'] or '24'} 小时内到达现场"],
            ],
            widths=[3.4, 8.6, 4.4],
        ),
    )
    _child(
        section,
        "before",
        "供货前技术服务",
        paragraph("（1）技术交底：合同签订后及时与项目单位确认供货单的型号规格、交货长度、盘具要求、实物 ID 赋码规则及交货计划。"),
        paragraph("（2）监造与抽检配合：按项目单位要求提供生产计划，主动通知关键工序与出厂试验节点，为驻厂监造、巡检及抽检取样提供便利条件，如实提供生产进度与质量信息。"),
        paragraph("（3）资料提交：按供货单要求提交产品合格证、出厂检验报告、型式试验报告、使用说明书等技术资料。"),
    )
    _child(
        section,
        "delivery",
        "到货验收服务",
        paragraph("货物运抵指定交货地点后，我公司派专人到场配合清点卸货、开箱验收，核对装箱单、盘号、长度和实物 ID 信息，配合完成外观检查与到货抽检取样；验收中发现问题的，现场确认后立即启动处理流程。"),
    )
    _child(
        section,
        "installation",
        "安装指导",
        paragraph("根据项目单位需要，免费提供电缆敷设、终端及中间接头安装的现场技术指导，主要内容包括："),
        table(
            ["指导事项", "技术要求"],
            [
                ["敷设温度", "电缆敷设时环境温度不宜低于 0℃，低于时应采取预热措施"],
                ["最小弯曲半径", "三芯铠装电缆不小于 12 倍电缆外径，单芯铠装电缆不小于 15 倍电缆外径"],
                ["牵引方式与牵引力", "优先采用牵引头牵引导体，牵引力铜芯不大于 70N/mm²、铝芯不大于 40N/mm²"],
                ["侧压力", "滑动敷设时侧压力不大于 3kN/m"],
                ["固定与防护", "按设计间距固定，穿管、过路及进出建筑物处做好机械防护和封堵"],
                ["敷设后试验", "配合完成外护套绝缘电阻测试和交流耐压试验"],
            ],
            widths=[4.0, 12.4],
        ),
    )
    _child(
        section,
        "training",
        "培训服务",
        paragraph("我公司免费为项目单位运行维护、物资管理及施工人员提供培训，可采用现场授课、工厂实训或视频会议方式，并提供培训教材。培训计划如下："),
        table(["培训课程", "培训内容", "培训对象", "学时"], [list(t) for t in _TRAINING_PLAN], widths=[3.4, 7.6, 3.8, 1.6]),
    )
    _child(
        section,
        "support",
        "技术响应机制",
        paragraph(f"设立专职服务工程师和服务热线（{service['hotline'] or ctx.need('售后服务热线')}），7×24 小时响应项目单位技术咨询：一般问题 {service['responseHours'] or '2'} 小时内答复；需现场处理的问题 {service['arrivalHours'] or '24'} 小时内到达现场；重大问题由公司技术负责人牵头成立专项小组处理并提交书面报告。"),
    )


def build_after_sales(ctx: ComposeContext, section: Section) -> None:
    service = ctx.profile["service"]
    criteria = _find_criteria(ctx, "business", "售后服务")
    section.criteria += [c["criterion_id"] for c in criteria]
    section.blocks.append(paragraph(f"{ctx.company_name}郑重承诺对本项目所供产品提供全面、及时、专业的售后服务，具体保障措施及承诺如下。"))
    _child(section, "warranty", "质量保证期承诺", paragraph("质量保证期按合同约定执行。质保期内因产品设计、材料、制造工艺等质量原因造成的损坏或不能正常使用，我公司负责免费修复或更换，并承担由此产生的直接费用；质保期满后继续提供终身有偿技术服务。"))
    _child(
        section,
        "network",
        "售后服务网点",
        paragraph(f"我公司售后服务网点覆盖 {ctx.value(service['provinceCount'], '售后服务网点覆盖省份数量')} 个省（自治区、直辖市）" + (f"：{service['provinces']}。" if service["provinces"] else "。") + f"在{'、'.join(dict.fromkeys(re.sub(r'^.*-', '', lot['lot_name']) for lot in ctx.lots)) or '项目所在地'}等本次所投区域均可就近组织技术服务与应急供货。"),
        placeholder("（此处粘贴：售后服务网点一览表及证明材料）", height=4.5),
    )
    _child(
        section,
        "response",
        "服务响应机制",
        table(
            ["问题类别", "响应时间", "处理方式", "完成时限"],
            [
                ["一般技术咨询", f"{service['responseHours'] or '2'} 小时内", "电话、视频远程答复", "当日闭环"],
                ["到货质量异议", f"{service['responseHours'] or '2'} 小时内", "现场确认，属质量问题的免费更换", f"{service['arrivalHours'] or '24'} 小时内到场，按约定期限完成更换"],
                ["运行故障", "立即响应", "派技术人员赴现场，协助查明原因并抢修", f"{service['arrivalHours'] or '24'} 小时内到场"],
                ["应急抢险需求", "立即响应", "启动应急保供预案，优先排产、专车直发", "按项目单位要求时限"],
            ],
        ),
    )
    _child(section, "emergency", "应急保供承诺", paragraph("建立应急保供预案和 7×24 小时值班制度，对电网抢险、突发故障、重大保电等紧急需求优先排产、专车直发，全力保障电网建设与安全运行。"))
    _child(section, "trace", "质量追溯", paragraph("所供产品全部按国家电网公司实物 ID 管理要求赋码，随物资提供并安装二维码标签和 RFID 电子标签，实现“一物一码”全寿命周期质量追溯；产品质量档案保存期限不少于产品设计寿命。"))
    _child(
        section,
        "commitment",
        "履约承诺",
        paragraph("（1）严格履行合同约定的交货期、交货数量和交货地点，接受买方监督考核；（2）不转包、不违规分包、不贴牌代工；（3）发生质量问题时积极配合调查处理并承担相应责任；（4）接受买方按抽检规范开展的各类抽检，抽检不合格的按合同约定承担违约责任。"),
        ctx.signature_block(),
    )


def build_quality_assurance(ctx: ComposeContext, section: Section) -> None:
    systems = _has_certificate(ctx, "quality_system", "environment_system", "safety_system")
    criteria = _find_criteria(ctx, "technical", "履约风险")
    section.criteria += [c["criterion_id"] for c in criteria]
    section.blocks.append(paragraph("为确保本项目供货产品质量持续满足招标文件及技术规范书要求，我公司制定如下质量保证措施方案。"))
    _child(
        section,
        "system",
        "质量管理体系",
        paragraph(f"{ctx.company_name}已建立覆盖设计开发、原材料采购、生产制造、出厂检验、交付服务全过程的质量管理体系" + (f"，持有{'、'.join(c['name'] for c in systems)}" if systems else "") + "，配备专职质量管理人员和检验人员，质量管理部门独立行使质量否决权。"),
        paragraph("质量方针：质量第一、用户至上、持续改进。质量目标：出厂产品合格率 100%，用户到货抽检合格率 100%，合同履约率 100%，用户投诉处理及时率 100%。"),
    )
    _child(
        section,
        "organization",
        "质量保证组织与职责",
        table(
            ["部门 / 岗位", "质量职责"],
            [
                ["总经理", "质量第一责任人，批准质量方针与目标，提供资源保障"],
                ["质量管理部", "体系运行监督、进厂 / 过程 / 出厂检验、不合格品控制、质量数据分析"],
                ["技术研发部", "产品设计、工艺文件编制与验证、技术标准管理"],
                ["生产制造部", "按工艺文件组织生产，落实工序自检互检与设备维护"],
                ["采购供应部", "合格供方管理、原材料采购与质量协议签订"],
                ["销售与服务部", "合同评审、交付管理、用户服务与质量信息反馈"],
            ],
            widths=[4.0, 12.4],
        ),
    )
    _child(section, "material", "原材料质量控制", paragraph("建立合格供方名录与动态评价机制，关键原材料只从合格供方采购并签订质量保证协议；所有原材料进厂后按检验规程“批批检验、批批建档”，检验合格方可入库投产；原材料按批次标识、分区存放、先进先出，保证全过程可追溯。"))
    _child(
        section,
        "process",
        "生产过程质量控制",
        paragraph("各工序严格按工艺文件与作业指导书执行，设置下列工序质量控制点，上道工序不合格不得流入下道工序："),
        table(["控制环节", "控制对象", "控制 / 检验项目", "检验频次"], [list(row) for row in _QUALITY_CHECKPOINTS], widths=[2.8, 3.6, 6.8, 3.2]),
        paragraph("关键工序实行首件检验确认制度，在线测偏仪、火花试验机等在线监测装置与设备联锁，工艺参数由系统自动采集、超限报警并留存记录。"),
    )
    _child(
        section,
        "test",
        "出厂检验与型式试验",
        paragraph("成品电缆按 GB/T 12706 及技术规范书要求逐盘进行例行试验，按批进行抽样试验，试验项目如下："),
        table(["试验项目", "试验类别", "试验方法标准", "频次", "合格判据"], [list(row) for row in _ROUTINE_TESTS], widths=[3.4, 2.2, 3.0, 2.6, 5.2]),
        paragraph("投标产品型式试验报告由国家授权的专业检测机构出具；供货前按承诺函向买方提交与所供产品型号一致的有效型式试验报告。"),
    )
    _child(section, "package", "包装、运输与交付质量控制", paragraph("电缆采用符合 JB/T 8137 要求的电缆盘交货，电缆两端可靠密封防潮，盘面清晰标注型号规格、长度、毛重、制造日期、旋转方向及实物 ID 标识；装卸采用吊装方式，严禁平放和抛滚；运输途中采取防雨、防碰撞、防滚动措施；随货提供出厂检验报告、合格证、装箱单等技术文件。"))
    _child(section, "nonconformity", "不合格品控制与质量问题处理", paragraph("不合格品严格执行“标识、隔离、评审、处置”程序，杜绝不合格品出厂。供货后如发生质量问题，我公司在接到通知后立即响应，无偿修复或更换不合格产品并承担相应损失，同时开展原因分析，制定纠正和预防措施，向买方提交书面整改报告。"))
    _child(section, "improvement", "持续改进", paragraph("公司定期开展内部审核、管理评审和质量分析会，运用 QC 小组、统计过程控制（SPC）等方法持续改进产品质量与过程能力；主动收集用户意见和运行反馈，纳入产品与工艺改进计划。"))


def build_test_reports(ctx: ComposeContext, section: Section) -> None:
    specs = ctx.tender.get("tech_specs", [])
    section.blocks.append(paragraph("我方所投产品均已取得国家授权的专业检测机构出具的有效型式试验报告，报告结论数据满足本次招标技术规范要求。资格预审申请文件中已提交且在有效期内的报告不再重复提交，过期需更新的报告如下。"))
    models = list(dict.fromkeys(f"{s.get('model', '')} {s.get('voltage', '')}".strip() for s in specs if s.get("model")))
    if models:
        section.blocks.append(table(["序号", "产品型号", "报告类别", "备注"], [[str(i), m, "型式试验报告", "报告覆盖所投规格"] for i, m in enumerate(models, start=1)], caption="所投产品检测检验报告清单"))
    section.blocks += certificate_blocks(ctx, ("type_test",), "所投产品型式试验报告（封面、结论页及主要数据页）")


def build_commitment_letter(ctx: ComposeContext, section: Section) -> None:
    section.blocks += [
        title_line("供货前提供与所供产品型号一致的有效型式试验报告承诺函"),
        paragraph("致招标人：", indent=False),
        paragraph(f"我方已完全理解{ctx.project_name}货物招标（招标编号：{ctx.tender_no}）招标文件的全部内容，并承诺按照招标文件的约定。"),
        paragraph(f"对于{ctx.lot_names}（分标编号：{ctx.lot_codes}）{ctx.package_no}，在供货前以书面形式向买方提交与所供产品型号一致的有效型式试验报告。"),
        paragraph("如我方未按照招标文件要求和投标文件承诺，在约定的时间内向买方提交符合要求的相应产品有效的型式试验报告，买方有权解除合同，并立即退还买方已支付的合同价款，由此造成的经济损失由投标人承担。"),
        paragraph("本承诺函在本次招投标及合同授予、履行的过程中保持有效，并作为合同的附件。"),
        ctx.signature_block(),
    ]


def build_prequalification_notice(ctx: ComposeContext, section: Section) -> None:
    rows = [[lot["lot_code"], lot["lot_name"], lot.get("prequalification") or "见招标文件第一章附件2"] for lot in ctx.lots]
    section.blocks.append(paragraph("我方所投标包对应的资格预审标段均已取得资格预审合格通知书，通知书涵盖全部所投分标对应的预审标段。"))
    if rows:
        section.blocks.append(table(["分标编号", "分标名称", "对应资格预审标段要求"], rows, widths=[3.8, 3.6, 9.0]))
    section.blocks += certificate_blocks(ctx, ("prequalification",), "资格预审结果通知书复印件")


def build_technical_other(ctx: ComposeContext, section: Section) -> None:
    section.blocks.append(paragraph("为进一步说明我方履约保障能力，补充提交供货组织与交付保障方案、实物 ID 实施方案及风险应对措施。"))
    _child(
        section,
        "supply",
        "供货组织与交付保障方案",
        paragraph("（1）订单响应：接到供货单后 24 小时内完成订单评审并反馈交货计划；（2）生产排产：框架协议产品纳入公司重点订单管理，优先保障原材料与设备产能；（3）进度管控：实行生产进度周报制度，关键节点主动向项目单位通报；（4）物流交付：与多家具备大件运输资质的物流单位长期合作，按供货单约定的时间、地点交货并配合卸货验收。"),
        table(
            ["阶段", "工作内容", "责任部门", "控制要点"],
            [
                ["订单确认", "供货单评审、技术确认、交货计划反馈", "销售与服务部", "24 小时内反馈"],
                ["原材料准备", "按订单核算用量并组织采购、进厂检验", "采购供应部、质量管理部", "关键材料保持安全库存"],
                ["生产制造", "按工艺文件组织生产，过程检验", "生产制造部", "关键工序首检确认"],
                ["出厂检验", "逐盘例行试验、抽样试验、实物 ID 赋码", "质量管理部", "合格后方可包装发运"],
                ["包装发运", "盘具包装、装车加固、在途跟踪", "物流部门", "防潮、防碰撞、准时到货"],
                ["到货验收", "配合清点验收、抽检取样、资料移交", "现场服务工程师", "问题当场确认并闭环"],
            ],
        ),
    )
    _child(section, "identity", "实物 ID 实施方案", paragraph("我公司已建立实物 ID 管理流程：依据供货单在电子商务平台申请实物 ID 编码，生产完成后在产品本体及盘具指定位置安装二维码标签和 RFID 电子标签，标签材质、尺寸、内容及安装位置满足国家电网公司相关技术规范及安装指导手册要求，出厂前逐一扫码校验，确保“账、卡、物”一致。"))
    _child(section, "risk", "履约风险识别与应对措施", table(
        ["风险类别", "风险描述", "应对措施"],
        [
            ["原材料价格波动", "铜、铝等大宗原材料价格大幅波动", "执行合同价格联动条款；通过期货套期保值、战略采购协议锁定成本"],
            ["产能冲突", "多项目集中交货导致产能紧张", "框架协议订单优先排产；提前预排产计划并预留机动产能"],
            ["物流受阻", "恶劣天气、交通管制等影响运输", "提前发运并预留缓冲期；启用备用运输线路和承运单位"],
            ["质量异常", "过程或出厂检验发现质量异常", "立即隔离并启动不合格品控制程序，必要时重新投产并通报买方"],
            ["需求变更", "供货单数量、规格或交期调整", "建立快速变更评审机制，24 小时内反馈调整方案"],
        ],
        widths=[3.2, 5.6, 7.6],
    ))
    deductions = [c for c in ctx.criteria_for("technical") if (c["max_score"] or 0) <= 0 and (c["min_score"] or 0) < 0]
    if deductions:
        clean = ctx.profile["declarations"]["noAdverseRecord"]
        statement = _child(section, "quality_record", "抽检质量问题自查声明", criteria=[c["criterion_id"] for c in deductions])
        statement.blocks += [
            paragraph(f"{ctx.company_name}对照招标文件技术评分标准中的质量问题扣分情形进行了自查：" + ("截至投标截止日，我公司不存在所列抽检质量问题扣分情形。" if clean else ctx.need("抽检质量问题自查情况"))),
            ctx.signature_block(),
        ]
    section.blocks.append(paragraph("除上述内容外，我方无其他需要说明的技术文件。评标过程中如需澄清或补充，我方将按评标委员会要求及时提供。"))
