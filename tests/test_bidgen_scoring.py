"""人工摘录工程招标原表的独立评分用例，不使用 seed 推导期望值。"""

from copy import deepcopy
from dataclasses import asdict
from zipfile import ZipFile

from api.services.bidgen.docx_reader import DocBlock
from api.services.bidgen.profile import DEFAULT_PROFILE
from api.services.bidgen.score_check import run_score_check
from api.services.bidgen.tender_parser import TenderInfo, _parse_scoring


def table(rows):
    return DocBlock(kind="table", rows=rows)


def engineering_blocks():
    # 原表：评标办法前附表 2.2.4，摘录首组、跨页子项、报价和服务承诺。
    return [table([
        ["条款号", "评审因素", "", "评审标准"],
        ["2.1.2", "资格评审标准", "营业执照", "具备有效的营业执照"],
        ["2.2.1", "分值组成（总分100分）", "", "投标报价40分；其他评分因素6分"],
        ["条款号", "条款内容", "", "评分标准"],
        ["2.2.4（1）", "承包人建议 书评分标准（8分）", "项目详细说明和总体管理方案、目标 0-2分", ""],
        ["2.2.4（2）", "设计组织技 术措施评分标准（14分）", "设计组织技术措施评分标准（6分）", "设计质量保证体系及措施 0-2分"],
        ["", "", "", "风险防范措施合理可行 0-2分"],
    ]), table([
        ["", "", "2、设计进度安排（2分）", "有设计进度计划表及确保进度的技术组织措施 0-2分"],
        ["2.2.4（3）", "实施方案评 分标准（32分）", "施工方案与技术措施", "有施工方案和技术措施；施工方法和技术措施合理、可行。 0-6分"],
        ["注：以上内容根据相关内容是否科学、可行、针对性强范围内酌情打分，缺项不得分。", "", "", ""],
        ["2.2.4（4）", "投标报价评分标准（40分）", "设计费投标报价评分标准（10分）", "各有效投标报价在限价96%（含）至100%之间参与计算。"],
    ]), table([
        ["", "", "", "当报价等于评标基准价时得满分10分；每高于1%扣0.5分。"],
        ["", "", "工程费投标报价评分标准（30分）", "每低于1%扣0.5分，扣完为止。"],
        ["2.2.4（5）", "其他评分因素评分标准（6分）", "服务承诺评分标准（6分）", "质保期内、质保期外的服务承诺。0-1分"],
        ["", "", "", "其他实质性承诺及合理化建议。0-2分"],
    ])]


def parsed(blocks):
    info = TenderInfo()
    _parse_scoring(blocks, info)
    return [asdict(c) for c in info.criteria]


def test_engineering_original_ranges_groups_and_cross_table_continuation():
    items = parsed(engineering_blocks())
    assert len(items) == 9
    assert [(i["volume"], i["min_score"], i["max_score"]) for i in items] == [
        ("technical", 0, 2), ("technical", 0, 2), ("technical", 0, 2),
        ("technical", 0, 2), ("technical", 0, 6), ("price", 0, 10),
        ("price", 0, 30), ("business", 0, 1), ("business", 0, 2),
    ]
    assert items[0]["factor"] == "项目详细说明和总体管理方案、目标"
    assert items[2]["factor"] == "风险防范措施合理可行"
    assert items[3]["group"] == "设计组织技术措施评分标准（14分）"
    assert "条款 2.2.4（2）" in items[3]["rule"]
    assert "缺项不得分" in items[4]["rule"]
    assert "96%（含）至100%" in items[5]["rule"]
    assert "每高于1%扣0.5分" in items[5]["rule"]
    assert items[5]["evaluation_mode"] == "price"
    assert items[8]["evaluation_mode"] == "manual"
    assert len({i["criterion_id"] for i in items}) == 9


def test_non_scoring_tables_and_unscoped_continuations_are_not_scored():
    assert parsed([table([
        ["条款号", "评审因素", "", "评审标准"],
        ["2.1.2", "资格评审", "注册资本", "不得低于1000万元"],
    ])]) == []
    assert parsed([table([["", "", "施工方案", "合理可行0-6分"]])]) == []
    blocks = engineering_blocks()[:1] + [DocBlock(kind="paragraph", text="第四章 合同")]
    blocks += [table([["", "", "服务", "合同考核0-9分"]])]
    assert len(parsed(blocks)) == 3


def test_legacy_three_column_rules_keep_exact_fields():
    items = parsed([DocBlock(kind="paragraph", text="商务评分标准"), table([
        ["评分项目", "评审要素", "评审细则"],
        ["企业能力（0-10分）", "售后服务（1-5分）", "覆盖20省得【5】分；覆盖10省得【3】分。"],
    ])])
    assert items == [{"criterion_id": "B-01", "volume": "business", "category": "", "group": "企业能力",
                      "factor": "售后服务", "min_score": 1, "max_score": 5,
                      "rule": "覆盖20省得【5】分；覆盖10省得【3】分。",
                      "tiers": [{"score": 5, "condition": "覆盖20省"}, {"score": 3, "condition": "覆盖10省"}],
                      "evaluation_mode": ""}]


def test_empty_report_is_blocked_not_zero_or_passed():
    report = run_score_check([], [], deepcopy(DEFAULT_PROFILE), [], "")
    assert report["status"] == "blocked"
    assert report["passed"] is False
    assert report["coverage"] is None
    assert report["volumes"] == []
    assert "暂不可评分" in report["message"]


def test_subjective_and_price_requirements_never_become_zero_or_awarded_points():
    report = run_score_check([], parsed(engineering_blocks()), deepcopy(DEFAULT_PROFILE), [], "")
    assert report["status"] == "warning"
    assert report["passed"] is False
    assert report["coverage"] == 0
    assert [(v["volume"], v["maxScore"], v["estimatedScore"], v["unestimated"]) for v in report["volumes"]] == [
        ("business", 3, None, 2), ("technical", 14, None, 5), ("price", 40, None, 2),
    ]
    assert all(i["statusLabel"] == "待评估" and i["estimatedScore"] is None and not i["responseVerified"] for i in report["items"])


def test_old_task_reparses_scoring_without_changing_generated_word(tmp_path, monkeypatch):
    from docx import Document
    from api.services.bidgen.service import BidGenService
    from common.config import get_settings
    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))
    document = Document()
    for block in engineering_blocks():
        word_table = document.add_table(rows=len(block.rows), cols=4)
        for i, row in enumerate(block.rows):
            for j, text in enumerate(row):
                word_table.cell(i, j).text = text
    source = tmp_path / "source.docx"
    document.save(source)
    service = BidGenService(tmp_path, 1)
    task = service.create_task("招标文件正文.docx", source)
    # 模拟尚未支持该表格式的历史任务：只重新解析评分，不重新解析项目/分包。
    state = service._load(task["id"])
    original_tree = deepcopy(state["tree"])
    state["tender"]["criteria"] = []
    service._save(state)
    with monkeypatch.context() as patch:
        patch.setattr("api.services.bidgen.service.read_scoring_criteria", lambda paths: [])
        before = service.generate(task["id"], {})
    def xmls(result):
        return {f["key"]: ZipFile(service.output_file(task["id"], f["name"])).read("word/document.xml")
                for f in result["result"]["files"] if f["name"].endswith(".docx")}
    old_xml = xmls(before)
    after = service.generate(task["id"], {})
    assert len(after["tender"]["criteria"]) == 9
    assert xmls(after) == old_xml
    assert after["tree"] == original_tree
    assert after["selection"] == before["selection"]
    assert [f["name"] for f in after["result"]["files"]] == [f["name"] for f in before["result"]["files"]]
    state = service._load(task["id"])
    state["result"]["scoreCheck"].update(items=[], volumes=[], passed=True, coverage=0)
    saved = deepcopy(state)
    public = service._public(state)
    assert public["result"]["scoreCheck"]["passed"] is False
    assert public["result"]["scoreCheck"]["coverage"] is None
    assert state == saved  # 历史快照只规范返回值，不原地篡改。
