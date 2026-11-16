"""清单式目录编写器：功能响应表写实现方式、授权委托书填写值带下划线、代理人身份证正反面都贴。

期望值逐字段手写（使用虚构资料），不由被测实现生成。
"""

from copy import deepcopy

from api.services.bidgen.document_model import UNDERLINE_END, UNDERLINE_START, ComposeContext
from api.services.bidgen.profile import DEFAULT_PROFILE
from api.services.bidgen.topic_writer import ListedWriter

MES_ITEMS = [
    ("生产订单/工单管理", "ERP生产订单双向实时对接：订单下发接收、完工/报废数量实时回传，工单状态跟踪（下达/投产/完工/关闭）"),
    ("生产派工与排产", "按产线/班组/人员派工，派工任务下发到工位终端"),
    ("电子SOP", "工位终端按产品/工序自动展示作业指导书、图纸、参数标准"),
    ("条码/标签管理", "物料码、批次码、托盘码、成品码模板配置，与追溯编码绑定"),
    ("原辅材投料及防错", "扫码校验批次、数量、效期、状态，错误批次禁止投料"),
    ("工序流转报工", "开工/完工/暂停/报废报工，记录人员、设备、时间、数量"),
    ("生产订单领退料", "与WMS联动过账，实时回传ERP"),
]


def context() -> ComposeContext:
    profile = deepcopy(DEFAULT_PROFILE)
    profile["company"]["name"] = "测试投标企业"
    profile["legalRep"].update({"name": "法定代表人甲"})
    profile["agent"].update({"name": "代理人甲", "idNumber": "999999199001010001", "title": "项目经理"})
    return ComposeContext(tender={"project_name": "示例生产运营管理平台", "purchasers": ["测试采购单位"]},
                          profile=profile, lots=[], options={})


def writer(erp_text: str = "金蝶云星空ERP系统", items=MES_ITEMS) -> ListedWriter:
    module = {"title": "MES生产执行", "level": 2, "path": [], "texts": [erp_text], "tables": [],
              "items": [{"label": label, "text": text, "attrs": {"上线阶段": "一阶段"}} for label, text in items]}
    return ListedWriter(context(), {"requirements": [module]})


def function_rows(listed: ListedWriter) -> list[list[str]]:
    draft = listed.functions({"title": "功能响应"}, [])
    blocks = dict(draft.children)["MES生产执行功能"]
    return next(block for block in blocks if block["type"] == "table")["rows"]


def test_function_response_describes_implementation_not_just_satisfied():
    rows = function_rows(writer())
    responses = {row[1]: row[4] for row in rows}
    assert all(response not in {"满足", "满足要求", "响应", "完全响应"} for response in responses.values())
    assert responses["生产订单/工单管理"] == "订单下发、接收、拆分与状态流转（下达/投产/完工/关闭）全流程管理，完工与报废数量实时回传金蝶云星空"
    assert responses["生产派工与排产"] == "支持按产线、班组、人员三级派工，任务推送至工位一体机与PDA，支持优先级调整与临时插单"
    assert responses["电子SOP"] == "按工单与工序自动调取SOP、图纸与参数标准，版本随工艺变更更新，可记录查阅与确认"
    assert responses["条码/标签管理"] == "统一标签模板中心，各类编码规则集中维护，打印记录可追溯，模板变更留痕"
    assert responses["原辅材投料及防错"] == "投料前强制校验物料、批次、效期、库存与配方用量，不符合即阻断并提示原因"
    assert responses["工序流转报工"] == "工位扫码报工，自动记录人员、设备、工时与数量，报工数据实时回传金蝶云星空核算"
    assert responses["生产订单领退料"] == "工单领料、退料、补料与超额领用审批联动库存过账，同步回传金蝶云星空"
    assert [row[3] for row in rows] == ["一阶段"] * len(MES_ITEMS)


def test_function_response_drops_erp_clause_when_tender_has_no_erp():
    no_erp_items = [("工序流转报工", "开工/完工/暂停/报废报工，记录人员、设备、时间、数量")]
    responses = {row[1]: row[4] for row in function_rows(writer(erp_text="生产执行系统建设", items=no_erp_items))}
    assert responses["工序流转报工"] == "工位扫码报工，自动记录人员、设备、工时与数量"
    assert "{erp}" not in "".join(responses.values())


def test_unmatched_function_gets_generic_text_and_pending_confirmation():
    listed = writer()
    response = listed.function_response("自定义功能甲", "按甲方习惯实现")
    assert response == "在系统中按招标要求实现自定义功能甲，相关规则与参数可配置，业务数据实时记录、可查询可追溯"
    assert any("自定义功能甲" in entry["hint"] for entry in listed.ctx.pending)


def test_authorization_letter_body_values_are_underlined():
    listed = writer()
    letter = dict(listed.legal_forms({"title": "法定代表人身份证明及授权委托书"}, []).children)["授权委托书"]
    addressee, body = letter[1], letter[2]
    assert addressee["form"] == f"致：{UNDERLINE_START}测试采购单位{UNDERLINE_END}"
    for value in ("法定代表人甲", "测试投标企业", "代理人甲", "999999199001010001", "项目经理", "示例生产运营管理平台"):
        assert f"{UNDERLINE_START}{value}{UNDERLINE_END}" in body["form"], value
    assert body["text"].startswith("本人法定代表人甲系测试投标企业的法定代表人，现授权代理人甲（身份证号：999999199001010001，职务：项目经理）")
