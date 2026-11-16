"""通用写作、评分点覆盖、分析与风险自查、配图、防雷同、表格预填、历史标书库。期望值均为人工给定。"""

import random
from copy import deepcopy

import pytest
from docx import Document

from api.services.bidgen.analysis import risk_check
from api.services.bidgen.charts import add_charts, font_path
from api.services.bidgen.corpus import CorpusIndex, CorpusStore, adapt
from api.services.bidgen.document_model import ComposeContext, Section, paragraph, table
from api.services.bidgen.epc_writer import Draft
from api.services.bidgen.generic_writer import GenericWriter, ensure_coverage, scoring_aspects
from api.services.bidgen.profile import DEFAULT_PROFILE
from api.services.bidgen.variation import _reorder_clauses, _shuffle_lists


def context(**tender):
    profile = deepcopy(DEFAULT_PROFILE)
    profile["company"]["name"] = "测试服务有限公司"
    base = {"project_name": "某物业服务项目", "purchasers": ["某采购单位"], "criteria": [], "format_items": [], "key_clauses": []}
    return ComposeContext(tender={**base, **tender}, profile=profile, lots=[], options={})


def test_scoring_aspects_read_listed_points_and_ignore_score_tiers():
    rule = ("供应商制定详细的项目实施管理与计划，包含但不限于进度计划、组织人员保障、部署实施、质量保证措施等内容。"
            "（1）实施计划清晰，人岗适配度高的，4分≤得分≤5分；（2）实施计划基本可行的，2分≤得分＜4分。")
    assert scoring_aspects(rule) == ["进度计划", "组织人员保障", "部署实施", "质量保证措施"]
    assert scoring_aspects("针对响应流程、服务人员数量及能力水平、其他优惠服务措施等方面综合评定。") == [
        "响应流程", "服务人员数量及能力水平", "其他优惠服务措施"]


def test_coverage_check_adds_missing_scoring_points():
    ctx = context()
    draft = Draft(children=[("进度计划", [paragraph("按周跟踪进度。")])])
    record = ensure_coverage(ctx, {}, draft, "包括但不限于进度计划、应急预案、培训方案等内容。")
    assert record["added"] == ["应急预案", "培训方案"]
    assert [title for title, _blocks in draft.children] == ["进度计划", "应急预案", "培训方案"]


def test_generic_writer_answers_requirements_of_any_industry():
    facts = {"name": "某物业服务项目", "owner": "某采购单位", "requirements": [
        {"title": "保洁服务要求", "level": 3, "path": [], "texts": ["公共区域每日清扫两次，垃圾日产日清。"], "items": [], "tables": []},
        {"title": "安保服务要求", "level": 3, "path": [], "texts": ["实行24小时值班巡逻制度。"], "items": [], "tables": []},
    ]}
    draft = GenericWriter(context(), facts).write("服务方案", "根据服务方案的完整性、针对性进行评分。")
    titles = [title for title, _blocks in draft.children]
    assert titles == ["保洁服务要求的实施措施", "安保服务要求的实施措施"]
    text = " ".join(block["text"] for _title, blocks in draft.children for block in blocks)
    assert "公共区域每日清扫两次" in text and "我方响应" in text


def test_risk_check_flags_deadline_price_and_pending_items():
    ctx = context(bid_deadline="2020-01-01T09:00", bid_bond_required=True,
                  format_items=[{"title": "报价表", "number": "3"}], project_facts={"veto_clauses": ["未按要求提供保证金的，其投标将被否决。"]})
    root = Section(key="business.1", title="一、响应函", level=1)
    root.blocks.append(paragraph(f"本项目为某物业服务项目，联系人：{ctx.need('联系人')}"))
    risks = risk_check(ctx.tender, ctx.profile, [root], ctx, {"items": [], "missing": []})
    items = [risk["item"] for risk in risks]
    assert items[:5] == ["截止时间", "法定代表人", "保证金", "报价", "待填内容"]
    assert risks[-1]["item"] == "否决条款"


@pytest.mark.skipif(font_path() is None, reason="本机没有中文字体")
def test_charts_are_inserted_after_schedule_and_architecture_tables(tmp_path):
    section = Section(key="t", title="计划", level=1)
    section.blocks.append(table(["阶段", "开始", "完成", "主要工作"], [["调研", "第1日", "第10日", ""], ["开发", "第11日", "第40日", ""],
                                                                    ["测试", "第41日", "第50日", ""]], caption="项目实施进度计划"))
    section.blocks.append(table(["层级", "采购要求", "我方设计"], [["基础层", "", ""], ["数据层", "", ""], ["应用层", "", ""]], caption="系统分层架构"))
    assert add_charts([section], tmp_path) == 2
    images = [block for block in section.blocks if block["type"] == "image"]
    assert [block["caption"] for block in images] == ["项目实施进度计划（横道图）", "系统分层架构图"]


def test_variation_keeps_facts_and_order_sensitive_text():
    rng = random.Random(7)
    shuffled = _shuffle_lists("培训内容包括操作使用、日常维护、常见问题处理等。", rng)
    assert sorted(shuffled.removeprefix("培训内容包括").removesuffix("等。").split("、")) == sorted(["操作使用", "日常维护", "常见问题处理"])
    steps = "实施分为准备、实施、检查验收三个阶段。"
    assert _shuffle_lists(steps, rng) == steps
    ordered = "进度保证：第一，资源保证；第二，技术保证；第三，协调保证。"
    assert _reorder_clauses(ordered, rng) == ordered


def test_deviation_table_is_prefilled_from_requirements():
    from api.services.bidgen.composer import _prefill_rows

    ctx = context(project_facts={"requirements": [
        {"title": "技术要求", "level": 2, "path": [], "texts": ["x"], "items": [], "tables": []},
        {"title": "服务要求", "level": 2, "path": [], "texts": ["y"], "items": [], "tables": []}]})
    rows = _prefill_rows(ctx, ["序号", "采购文件章节及条款号", "响应文件章节及条款号", "偏差说明"], [["", "", "", ""]] * 3)
    assert rows == [["1", "采购需求“技术要求”", "技术响应文件相应章节", "无偏差"],
                    ["2", "采购需求“服务要求”", "技术响应文件相应章节", "无偏差"]]


def test_corpus_reuses_matching_section_with_current_project_names(tmp_path):
    document = Document()
    document.add_paragraph("旧城区道路保洁服务项目")
    document.add_paragraph("投标人：甲环卫有限公司（盖章）")
    document.add_heading("一、应急预案", 1)
    document.add_paragraph("旧城区道路保洁服务项目设立应急小组，由甲环卫有限公司项目经理任组长，遇暴雨、冰雪天气两小时内完成主干道清理，"
                           "并配备应急车辆三台、应急人员二十名，保障道路通行安全。")
    document.add_heading("二、培训方案", 1)
    document.add_paragraph("新入职人员岗前培训不少于八学时，内容包括安全操作、作业标准与文明服务，考核合格后上岗；每季度组织一次复训，" * 2)
    source = tmp_path / "往期标书.docx"
    document.save(source)
    store = CorpusStore(tmp_path, 1)
    info = store.import_file(source, "往期标书.docx")
    assert info["facts"]["project"] == "旧城区道路保洁服务项目" and info["facts"]["company"] == "甲环卫有限公司"
    index = CorpusIndex(store.load())
    section = index.best_for("应急预案", "突发事件处置")
    assert section["title"] == "应急预案"
    text = "".join(adapt(section, index.sources[section["source"]], {"project": "新区环卫一体化项目", "company": "乙公司"}))
    assert "新区环卫一体化项目设立应急小组" in text and "乙公司项目经理" in text and "甲环卫" not in text
    assert index.best_for("网络安全设计") is None  # 不相关的评分点不硬套历史内容
