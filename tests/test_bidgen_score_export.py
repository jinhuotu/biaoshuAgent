from pathlib import Path

from openpyxl import load_workbook

from api.services.bidgen.score_export import export_score_check, score_check_filename


def test_score_check_export_contains_styled_overview_and_detail_rows():
    destination = Path(".pytest-tmp-score-export.xlsx")
    report = {
        "status": "warning",
        "coverage": 66.7,
        "checkedAt": "2026-11-13T12:00:00",
        "message": "需要补充或人工确认",
        "expiredCertificates": ["证书A"],
        "items": [
            {
                "volume": "technical",
                "category": "10kV",
                "group": "质量控制",
                "factor": "履约风险",
                "minScore": 0,
                "maxScore": 2,
                "rule": "对历史履约供货情况进行综合评价",
                "status": "missing",
                "statusLabel": "未响应 / 缺资料",
                "estimatedScore": 0,
                "sections": [],
                "basis": "未找到响应章节",
                "advice": ["补充证明"],
            }
        ],
        "volumes": [
            {
                "volume": "technical",
                "label": "技术评分",
                "total": 1,
                "counts": {"full": 0, "partial": 0, "missing": 1, "manual": 0, "external": 0},
                "estimatedScore": 0,
                "maxScore": 2,
                "externalMaxScore": 0,
            }
        ],
    }
    try:
        export_score_check(
            destination,
            project_name="测试项目",
            generated_at="2026-11-13T12:00:00",
            report=report,
        )
        workbook = load_workbook(destination)
        assert workbook.sheetnames == ["总览", "评分项明细"]
        assert workbook["总览"]["A1"].value == "测试项目 · 评分标准自查"
        assert workbook["评分项明细"]["G5"].value == "未响应 / 缺资料"
        assert workbook["评分项明细"]["G5"].fill.fgColor.rgb.endswith("FCE4D6")
        assert workbook["评分项明细"].freeze_panes == "A5"
    finally:
        destination.unlink(missing_ok=True)


def test_score_check_filename_is_safe():
    assert score_check_filename("项目/名称") == "项目_名称_评分标准自查.xlsx"
