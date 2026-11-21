"""导出的商务标、技术标不插入给评委看的评分细则复述。"""

from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

import pytest
from docx import Document

from api.services.bidgen.composer import compose_document
from api.services.bidgen.document_model import ComposeContext, Section
from api.services.bidgen.profile import DEFAULT_PROFILE
from api.services.bidgen.score_check import run_score_check
from api.services.bidgen.service import BidGenService
from api.services.bidgen.word_writer import render_document


def make_context():
    profile = deepcopy(DEFAULT_PROFILE)
    profile["company"]["name"] = "测试投标企业"
    profile["service"]["provinceCount"] = "20"
    profile["performances"] = [{"project": "实际供货项目", "buyer": "采购单位", "product": "电缆", "quantity": "1000", "unit": "千米", "year": "2025"}]
    factors = [("technical", "供货业绩")] * 3 + [
        ("technical", "生产试验设备"), ("technical", "人力资源"), ("technical", "创新能力"),
        ("technical", "工艺管理"), ("technical", "产品制造质量控制"), ("technical", "绿色低碳"),
        ("business", "售后服务"), ("business", "经营状况"),
    ]
    criteria = [{"criterion_id": f"criterion-{index}", "volume": volume, "category": "电缆", "group": "履约能力", "factor": factor,
                 "min_score": 8, "max_score": 17, "rule": "业绩满足公告要求得【8分】；超过公告要求50%及以上得【11分】。"}
                for index, (volume, factor) in enumerate(factors)]
    directory = [("商务文件", "商务支撑文件"), ("商务文件", "其他"),
                 ("技术文件", "业绩文件"), ("技术文件", "技术支撑文件"),
                 ("技术文件", "其他应提交的技术文件"), ("技术文件", "质量保证措施方案")]
    return ComposeContext(
        tender={"project_name": "回归验证项目", "tender_no": "TEST-001", "criteria": criteria,
                "format_items": [{"volume": volume, "number": str(index + 1), "title": title, "level": 1} for index, (volume, title) in enumerate(directory)],
                "supplements": [{"volume": volume, "project": "资质", "item": title, "note": "提供相应证明材料", "scope": ""}
                                for volume, title in [("business", "售后服务"), ("technical", "产品制造质量控制"), ("technical", "绿色低碳")]]},
        profile=profile, lots=[], options={"includeScoreIndex": True},
    )


@pytest.mark.parametrize("volume", ["business", "technical", "full"])
def test_word_omits_scoring_paragraphs_and_retains_response_content(tmp_path, volume):
    context = make_context()
    roots = compose_document(context)
    selected = roots if volume == "full" else [root for root in roots if root.key == volume]
    path = render_document(selected, context, tmp_path / f"{volume}.docx", volume)
    with ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    assert "评审要素：" not in xml
    assert "评审细则：" not in xml
    assert "超过公告要求50%及以上得【11分】" not in xml
    assert "测试投标企业" in xml
    if volume in {"technical", "full"}:
        assert "实际供货项目" in xml
        assert "生产设备与试验设备" in xml
    else:
        assert "投标人响应：" in xml
    assert len(Document(path).paragraphs) > 10
    report = run_score_check(roots, context.tender["criteria"], context.profile, [], "")
    assert len(report["items"]) == 11
    assert all(item["sections"] for item in report["items"] if item["factor"] in {"供货业绩", "售后服务"})
    assert report["items"][0]["rule"] == context.tender["criteria"][0]["rule"]


def test_scoring_attachments_preserve_source_conditions_and_score_ranges(tmp_path):
    # 按 SL2612 两份评分附件人工摘录。档位和条件由原表核对。
    def word(rows):
        document = Document()
        table = document.add_table(rows=0, cols=len(rows[0]))
        for values in rows:
            for cell, value in zip(table.add_row().cells, values):
                cell.text = value
        buffer = BytesIO()
        document.save(buffer)
        return buffer.getvalue()

    business = word([
        ["项目", "评审要素", "评审细则"],
        ["二、高质量发展评价", "创新激励机制、供应链保障措施（3-6）", "制定相应保障措施并提供证据材料【6分】，其他情况【3分】。"],
        ["三、诚信评价", "负面事件影响评价（-50-0）",
         "投标人发生重大负面事件或其他情形造成不良社会影响；以及不当行为可能带来破坏市场公平竞争秩序风险，根据严重程度【扣10-50分】；【无上述情形的，不扣分】。"],
    ])
    technical = word([
        ["项目", "评审要素", "评审内容"],
        ["五、绿色制造", "产品碳足迹证书（0-1）", "有【1分】 ，无【0分】"],
        ["二、资源实力", "创新能力（1-12）", "企业参与制定与投标产品相关的行业及以上标准1项得【4分】；不符合以上情形的得【0分】。"],
        ["", "", "企业具有类似投标产品的发明专利，满足上述任意一项，得【4分】；不符合以上情形的得【1分】。"],
        ["六、绩效评价", "运行绩效（0-20）", "运行绩效为A和B的按运行绩效评价得分*0.2的系数计算。"],
    ])
    upload = tmp_path / "招标资料.zip"
    with ZipFile(upload, "w") as package:
        package.writestr("招标文件.docx", word([["物资类别", "权重比例（技：商：价）"], ["配电箱", "50:10:40"]]))
        package.writestr("华北配电箱商务评分标准.docx", business)
        package.writestr("华北配电箱技术评分标准.docx", technical)
        package.writestr("包23/华北配电箱技术评分标准.docx", technical)
        package.writestr("其他物资技术评分标准.docx", word([["索引", "说明"], ["A", "格式待核对"]]))

    service = BidGenService(tmp_path, 1)
    task = service.create_task(upload.name, upload)
    tender = task["tender"]
    criteria = {c["factor"]: c for c in tender["criteria"]}
    assert tender["scoringWeights"] == {"配电箱": {"technical": 50, "business": 10, "price": 40}}
    assert len(tender["criteria"]) == 5 and len(criteria) == 5
    assert {f["role"] for f in tender["files"] if "评分标准" in f["name"]} == {"scoring"}
    assert any("其他物资技术评分标准.docx" in warning and "可编辑的 .docx" in warning for warning in tender["warnings"])
    incentive = criteria["创新激励机制、供应链保障措施"]
    assert (incentive["volume"], incentive["sourcePath"], incentive["minScore"], incentive["maxScore"]) == (
        "business", "华北配电箱商务评分标准.docx", 3, 6,
    )
    assert incentive["tiers"] == [
        {"score": 6, "condition": "制定相应保障措施并提供证据材料"}, {"score": 3, "condition": "其他情况"},
    ]
    assert criteria["负面事件影响评价"]["tiers"] == [
        {"min_score": -50, "max_score": -10,
         "condition": "投标人发生重大负面事件或其他情形造成不良社会影响；以及不当行为可能带来破坏市场公平竞争秩序风险，根据严重程度"},
        {"score": 0, "condition": "无上述情形的"},
    ]
    assert criteria["产品碳足迹证书"]["tiers"] == [{"score": 1, "condition": "有"}, {"score": 0, "condition": "无"}]
    innovation = criteria["创新能力"]
    assert (innovation["category"], innovation["sourcePath"], innovation["minScore"], innovation["maxScore"]) == (
        "配电箱", "华北配电箱技术评分标准.docx", 1, 12,
    )
    assert innovation["tiers"][0] == {"score": 4, "condition": "企业参与制定与投标产品相关的行业及以上标准1项"}
    assert innovation["tiers"][-1] == {"score": 1, "condition": "不符合以上情形的"}
    assert "\n企业具有类似投标产品的发明专利" in innovation["rule"]

    state = service._load(task["id"])
    root = Section(key="technical.1", title="创新能力", level=1, criteria=[innovation["criterionId"]])
    result = run_score_check([root], state["tender"]["criteria"], DEFAULT_PROFILE, [], "")
    items = {item["factor"]: item for item in result["items"]}
    assert (items["创新能力"]["status"], items["创新能力"]["estimatedScore"]) == ("manual", None)
    assert items["运行绩效"]["status"] == "external"
    assert result["available"] and not result["passed"]
    assert all(volume["estimatedCount"] == 0 for volume in result["volumes"])

    state["result"] = {"scoreCheck": {"items": [], "passed": True, "readBack": True}}
    assert service._public(state)["result"]["scoreCheck"] == {
        "items": [], "available": False, "passed": False, "readBack": False,
    }
    assert state["result"]["scoreCheck"]["passed"] is True


def test_empty_scoring_check_is_unavailable():
    result = run_score_check([], [], DEFAULT_PROFILE, [], "")
    assert not result["available"] and not result["passed"] and not result["readBack"]
    assert result["items"] == result["volumes"] == []
