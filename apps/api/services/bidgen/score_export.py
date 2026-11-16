"""Export the generated score self-check as a readable Excel workbook."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

_STATUS_LABELS = {
    "full": "满足",
    "partial": "可提升",
    "missing": "未响应 / 缺资料",
    "manual": "需人工确认",
    "external": "招标人评定",
}
_STATUS_COLORS = {
    "full": "E2F0D9",
    "partial": "FFF2CC",
    "missing": "FCE4D6",
    "manual": "DDEBF7",
    "external": "E7E6E6",
}
_BORDER = Border(
    left=Side(style="thin", color="D9E1F2"),
    right=Side(style="thin", color="D9E1F2"),
    top=Side(style="thin", color="D9E1F2"),
    bottom=Side(style="thin", color="D9E1F2"),
)
_HEADER_FILL = PatternFill("solid", fgColor="2F75B5")
_SUBHEADER_FILL = PatternFill("solid", fgColor="D9EAF7")
_TITLE_FONT = Font(name="微软雅黑", size=16, bold=True, color="1F1F1F")
_BODY_FONT = Font(name="微软雅黑", size=10, color="1F1F1F")
_HEADER_FONT = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
_MUTED_FONT = Font(name="微软雅黑", size=9, color="666666")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _safe_name(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", value).strip("_")
    return cleaned[:80] or "评分自查"


def _score_range(item: dict[str, Any]) -> str:
    minimum, maximum = item.get("minScore"), item.get("maxScore")
    if minimum is None or maximum is None:
        return "—"
    return f"{minimum} ~ {maximum}"


def _volume_title(item: dict[str, Any]) -> str:
    volume = item.get("volume")
    if volume == "price":
        return "报价评分"
    if volume == "business":
        return "商务 / 其他评分"
    category = _text(item.get("category")).strip()
    return f"技术评分（{category}）" if category else "技术评分"


def _set_cell(cell, value: Any, *, header: bool = False, muted: bool = False) -> None:
    cell.value = value
    cell.border = _BORDER
    cell.alignment = Alignment(vertical="top", wrap_text=True)
    cell.font = _HEADER_FONT if header else _MUTED_FONT if muted else _BODY_FONT
    if header:
        cell.fill = _HEADER_FILL


def _write_title(ws, title: str, subtitle: str, columns: int) -> None:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=columns)
    cell = ws.cell(1, 1, title)
    cell.font = _TITLE_FONT
    cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=columns)
    cell = ws.cell(2, 1, subtitle)
    cell.font = _MUTED_FONT
    cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.row_dimensions[2].height = 24


def _write_overview(ws, report: dict[str, Any], project_name: str, generated_at: str) -> None:
    columns = 5
    _write_title(
        ws,
        f"{project_name} · 评分标准自查",
        f"生成时间：{generated_at or '—'}；导出时间以文件属性为准。",
        columns,
    )
    headers = ["指标", "结果", "指标", "结果", "说明"]
    for index, value in enumerate(headers, start=1):
        _set_cell(ws.cell(4, index), value, header=True)

    items = report.get("items") or []
    summary_rows = [
        (
            "自查状态",
            {"ready": "可继续核对", "warning": "需要补充或人工确认", "blocked": "暂不可评分"}.get(
                report.get("status"), "—"
            ),
            "评分项数量",
            len(items),
            report.get("message") or "",
        ),
        (
            "响应覆盖率",
            f"{report['coverage']}%" if report.get("coverage") is not None else "暂不可评",
            "检查时间",
            _text(report.get("checkedAt")),
            "覆盖率按已响应且可核验的评分项计算",
        ),
        (
            "预计可得分",
            _score_total(report),
            "过期证件",
            len(report.get("expiredCertificates") or []),
            "预计得分不包含招标人评定项",
        ),
    ]
    for row_index, row in enumerate(summary_rows, start=5):
        for column_index, value in enumerate(row, start=1):
            _set_cell(ws.cell(row_index, column_index), value, muted=column_index in {1, 3})
        ws.row_dimensions[row_index].height = 28

    start = 10
    headers = [
        "评分范围",
        "评分项数",
        "满足",
        "可提升",
        "未响应 / 缺资料",
        "需人工确认",
        "招标人评定",
        "预计得分",
        "满分",
    ]
    for index, value in enumerate(headers, start=1):
        _set_cell(ws.cell(start, index), value, header=True)
    for row_index, volume in enumerate(report.get("volumes") or [], start=start + 1):
        counts = volume.get("counts") or {}
        values = [
            volume.get("label") or volume.get("volume") or "—",
            volume.get("total", 0),
            counts.get("full", 0),
            counts.get("partial", 0),
            counts.get("missing", 0),
            counts.get("manual", 0),
            counts.get("external", 0),
            volume.get("estimatedScore")
            if volume.get("estimatedScore") is not None
            else "暂不可评",
            volume.get("maxScore", 0),
        ]
        for column_index, value in enumerate(values, start=1):
            _set_cell(ws.cell(row_index, column_index), value)
        ws.row_dimensions[row_index].height = 24

    widths = [22, 18, 12, 12, 18, 14, 14, 14, 12]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.freeze_panes = "A11"


def _score_total(report: dict[str, Any]) -> str:
    values = [volume.get("estimatedScore") for volume in report.get("volumes") or []]
    if not any(value is not None for value in values):
        return "暂不可评"
    return f"{sum(value for value in values if value is not None):g} 分"


def _write_details(ws, report: dict[str, Any]) -> None:
    headers = [
        "序号", "评分范围", "评分组", "评审因素", "分值范围", "评审细则", "自查状态",
        "预估得分", "响应章节", "判定依据", "改进建议",
    ]
    _write_title(
        ws,
        "评分项明细",
        "每行对应一个评分项，可按状态筛选；响应章节和判定依据保留完整文本。",
        len(headers),
    )
    header_row = 4
    for index, value in enumerate(headers, start=1):
        _set_cell(ws.cell(header_row, index), value, header=True)

    items = report.get("items") or []
    for row_index, item in enumerate(items, start=header_row + 1):
        sections = (
            "；".join(_text(section.get("heading")) for section in item.get("sections") or [])
            or "未找到响应章节"
        )
        advice = "；".join(_text(value) for value in item.get("advice") or [])
        status = _text(item.get("status"))
        values = [
            row_index - header_row,
            _volume_title(item),
            _text(item.get("group")),
            _text(item.get("factor")),
            _score_range(item),
            _text(item.get("rule")),
            _STATUS_LABELS.get(status, _text(item.get("statusLabel"))),
            item.get("estimatedScore") if item.get("estimatedScore") is not None else "—",
            sections,
            _text(item.get("basis")),
            advice,
        ]
        for column_index, value in enumerate(values, start=1):
            cell = ws.cell(row_index, column_index)
            _set_cell(cell, value)
            if column_index == 7:
                cell.fill = PatternFill("solid", fgColor=_STATUS_COLORS.get(status, "FFFFFF"))
                cell.font = Font(name="微软雅黑", size=10, bold=True, color="1F1F1F")
        row_height = 18 + max(len(_text(value)) for value in values) / 2.5
        ws.row_dimensions[row_index].height = max(32, min(100, row_height))

    last_row = header_row + max(1, len(items))
    for index, width in enumerate([8, 18, 24, 24, 12, 48, 16, 12, 34, 42, 34], start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:K{last_row}"
    if items:
        table = Table(displayName="ScoreCheckDetails", ref=f"A4:K{last_row}")
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2", showRowStripes=True, showColumnStripes=False
        )
        ws.add_table(table)
    for status, color in _STATUS_COLORS.items():
        label = _STATUS_LABELS[status]
        ws.conditional_formatting.add(
            f"G5:G{last_row}",
            FormulaRule(formula=[f'G5="{label}"'], fill=PatternFill("solid", fgColor=color)),
        )


def export_score_check(
    destination: Path,
    *,
    project_name: str,
    generated_at: str,
    report: dict[str, Any],
) -> Path:
    """Write a deterministic, styled workbook for one generated task."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    overview = workbook.active
    overview.title = "总览"
    details = workbook.create_sheet("评分项明细")
    _write_overview(overview, report, project_name, generated_at)
    _write_details(details, report)
    workbook.properties.title = f"{project_name} 评分标准自查"
    workbook.properties.subject = "投标文件评分标准自查"
    workbook.save(destination)
    return destination


def score_check_filename(project_name: str) -> str:
    return f"{_safe_name(project_name)}_评分标准自查.xlsx"
