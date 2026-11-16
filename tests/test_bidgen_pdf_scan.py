"""扫描版 PDF：按框线还原表格、跨页表格合并。用程序画出的表格图片与人工给定的文字框验证，不依赖 OCR 模型。"""

import cv2
import numpy

from api.services.bidgen.pdf_scan import _join_lines, _tables, continue_table


def draw_table(row_lines, column_lines, missing=()):
    """白底黑线表格；``missing`` 为不画的线段 (方向, 线序号, 起, 止)，用来表示合并单元格。"""
    image = numpy.full((900, 1600, 3), 255, dtype=numpy.uint8)
    for y in row_lines:
        cv2.line(image, (column_lines[0], y), (column_lines[-1], y), (0, 0, 0), 3)
    for x in column_lines:
        cv2.line(image, (x, row_lines[0]), (x, row_lines[-1]), (0, 0, 0), 3)
    for direction, position, start, end in missing:
        if direction == "row":
            cv2.line(image, (start + 3, position), (end - 3, position), (255, 255, 255), 5)
        else:
            cv2.line(image, (position, start + 3), (position, end - 3), (255, 255, 255), 5)
    return image


def box(x, y, text, width=120, height=36):
    return (x, y, x + width, y + height, text)


def test_grid_lines_give_rows_and_columns_with_vertical_merge():
    rows, columns = [100, 200, 300, 400], [100, 500, 900, 1300]
    # 第一列第 2、3 行合并（中间那条横线在第一列缺失），文字竖向居中落在两行之间偏下
    image = draw_table(rows, columns, missing=[("row", 300, 100, 500)])
    boxes = [box(150, 130, "评审项目"), box(550, 130, "评审内容"), box(950, 130, "分值"),
             box(150, 310, "技术部分"), box(550, 230, "架构合理"), box(950, 230, "5"),
             box(550, 330, "功能丰富"), box(950, 330, "3"),
             box(150, 600, "表格外的正文")]

    tables = _tables(image, boxes)

    assert len(tables) == 1
    assert tables[0][1] == [["评审项目", "评审内容", "分值"], ["技术部分", "架构合理", "5"], ["", "功能丰富", "3"]]


def test_text_wrapped_inside_one_cell_is_joined_in_reading_order():
    image = draw_table([100, 300], [100, 700, 1300])
    boxes = [box(120, 200, "合同首页"), box(120, 130, "必须提供"), box(260, 130, "与用户签订的"), box(750, 160, "3")]

    assert _tables(image, boxes)[0][1] == [["必须提供与用户签订的合同首页", "3"]]


def test_underline_is_not_a_table():
    image = numpy.full((900, 1600, 3), 255, dtype=numpy.uint8)
    cv2.line(image, (200, 400), (1200, 400), (0, 0, 0), 3)

    assert _tables(image, [box(220, 350, "致：某某公司")]) == []


def test_boxes_on_one_line_are_joined_left_to_right():
    assert _join_lines([box(700, 102, "及报价说明"), box(100, 100, "5. 投标报价表"), box(100, 200, "6. 承诺")]) == [
        (100, "5. 投标报价表及报价说明"), (200, "6. 承诺")]


def test_continued_table_drops_repeated_header_and_rejoins_split_row():
    header = ["评审项目", "评审分类", "评审内容", "分配分数"]
    first = [header, ["商务部分", "业绩", "每提供一个自投标截止日", ""]]
    second = [list(header), ["", "", "起三年内签署的合同加1分。", "3"], ["", "资质", "证明文件是否齐全有效。", "4"]]

    assert continue_table(first, second)
    assert first == [header, ["商务部分", "业绩", "每提供一个自投标截止日起三年内签署的合同加1分。", "3"],
                     ["", "资质", "证明文件是否齐全有效。", "4"]]


def test_continued_table_keeps_complete_rows_separate():
    first = [["板块", "功能模块", "功能说明"], ["MES", "报工", "报工数据实时回传"]]
    second = [["", "领退料", "与仓储联动过账"]]

    assert continue_table(first, second)
    assert first[1:] == [["MES", "报工", "报工数据实时回传"], ["", "领退料", "与仓储联动过账"]]


def test_table_with_other_columns_is_not_a_continuation():
    first = [["序号", "项目内容", "金额"], ["1", "软件费用", ""]]

    assert not continue_table(first, [["序号", "项目内容", "品牌", "型号", "单价"]])
    assert len(first) == 2
