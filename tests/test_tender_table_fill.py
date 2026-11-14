from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt

from api.services.tenders.table_fill import fact_values, fill_label_tables, report_lines


def test_fact_values_normalizes_aliases_and_records_low_confidence():
    values, diagnostics = fact_values([{'fileId': 'license-a', 'fields': [
        {'name': ' 注 册 资 本：', 'value': ' 30000 万元人民币 ', 'conf': 0.99},
        {'name': '统一社会信用代码', 'value': '91410100MA9G7K8X2Q', 'conf': 0.99},
        {'name': '技术负责人姓名', 'value': '待核对人员', 'conf': 0.89},
        {'name': '员工总数', 'value': 0, 'conf': 0.9},
        {'name': '经营范围', 'value': '长' * 301},
        {'name': '开户银行', 'value': ''},
    ]}])
    assert values == {'注册资金': '30000 万元人民币', '营业执照号': '91410100MA9G7K8X2Q',
                      '员工总人数': '0'}
    assert diagnostics == [{'type': 'lowConfidence', 'lowConfidence': True,
                            'label': '技术负责人', 'value': '待核对人员', 'conf': 0.89,
                            'fileId': 'license-a', 'name': '技术负责人姓名'}]


def test_conflicting_values_are_excluded_with_every_source_id():
    values, diagnostics = fact_values([
        {'fileId': 'a', 'fields': [{'name': '注册资本', 'value': '100 万元'}]},
        {'fileId': 'b', 'fields': [{'name': '注册资本金', 'value': '200 万元'}]},
        {'fileId': 'c', 'fields': [{'name': '注册资金', 'value': '100万元'}]},
    ])
    assert values == {}
    assert diagnostics == [{'type': 'conflict', 'label': '注册资金', 'values': [
        {'value': '100 万元', 'sources': [{'fileId': 'a', 'name': '注册资本'},
                                       {'fileId': 'c', 'name': '注册资金'}]},
        {'value': '200 万元', 'sources': [{'fileId': 'b', 'name': '注册资本金'}]},
    ]}]


def test_existing_values_and_brief_owned_fields_are_never_overwritten():
    document = Document()
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = '注册资金'
    table.cell(0, 1).text = '已确认 100 万元'
    original = document.element.xml
    report = fill_label_tables(document, {'注册资金': '30000 万元人民币'})
    assert document.element.xml == original and report['filled'] == []
    assert report['kept'][0]['label'] == '注册资金'
    assert report['kept'][0]['value'] == '已确认 100 万元'
    assert report['pending'] == []
    protected = {'投标人名称', '注册地址', '法定代表人', '成立时间'}
    for label in protected:
        cells = table.add_row().cells
        cells[0].text, cells[1].text = label, 'brief 原文'
    original = document.element.xml
    fill_label_tables(document, {label: '不同的事实值' for label in protected},
                      skip_labels=protected)
    assert document.element.xml == original
    wrong = Document()
    cells = wrong.add_table(rows=1, cols=2).rows[0].cells
    cells[0].text = '投标人名称'
    report = fill_label_tables(wrong, {'投标人名称': '乙方测试有限公司'},
                               bidder_name='甲方测试有限公司')
    assert cells[1].text == '' and len(report['wrongCompany']) == 1
    assert report['pending'][0]['reason'] == '事实中的公司名与本次投标人不符'


def test_equipment_lists_and_bid_letter_appendix_are_skipped_wholesale():
    document = Document()
    table = document.add_table(rows=2, cols=3)
    for cell, text in zip(table.rows[0].cells, ['设备名称', '型号规格', '数量'], strict=True):
        cell.text = text
    table.cell(1, 0).text = '注册资金'
    original = document.element.xml
    report = fill_label_tables(document, {'注册资金': '30000 万元人民币'})
    assert document.element.xml == original
    assert report['skippedTables'] == [{'table': 0, 'summary': '设备名称 | 型号规格 | 数量',
                                        'reason': '多行清单'}]
    appendix = Document()
    appendix.add_paragraph('投标函附录')
    cells = appendix.add_table(rows=1, cols=2).rows[0].cells
    cells[0].text = '注册资金'
    original = appendix.element.xml
    report = fill_label_tables(appendix, {'注册资金': '30000 万元人民币'})
    assert appendix.element.xml == original and report['filled'] == []
    assert report['skippedTables'][0]['reason'] == '投标函附录'


def test_only_blank_cells_fill_without_changing_shape_merges_or_run_styles():
    document = Document()
    table = document.add_table(rows=3, cols=2)
    for row, label in zip(table.rows, ['注册资金', '营业执照号', '经营范围'], strict=True):
        row.cells[0].text = label
    table.cell(1, 1).text = '已有号码'
    run = table.cell(0, 1).paragraphs[0].add_run()
    run.font.name, run.font.size, run.bold = '宋体', Pt(10), True
    shape = (len(table.rows), len(table.columns), [len(row.cells) for row in table.rows])
    properties = [cell._tc.tcPr.xml for row in table.rows for cell in row.cells]
    style = run._r.rPr.xml
    report = fill_label_tables(document, {'注册资金': '30000 万元人民币',
                                         '营业执照号': '新号码', '经营范围': '电线、电缆、光缆'})
    assert [row.cells[1].text for row in table.rows] == [
        '30000 万元人民币', '已有号码', '电线、电缆、光缆',
    ]
    assert shape == (len(table.rows), len(table.columns), [len(row.cells) for row in table.rows])
    assert properties == [cell._tc.tcPr.xml for row in table.rows for cell in row.cells]
    assert run._r.rPr.xml == style
    assert [row['label'] for row in report['filled']] == ['注册资金', '经营范围']
    assert len(report_lines(report)) <= 2
    once = document.element.xml
    assert fill_label_tables(document, {'注册资金': '其他数字'})['filled'] == []
    assert document.element.xml == once
    nested = Document()
    outer = nested.add_table(rows=1, cols=1)
    inner = outer.cell(0, 0).add_table(rows=2, cols=2)
    inner.cell(0, 0).text = inner.cell(1, 0).text = '注册资金'
    inner.cell(0, 1).merge(inner.cell(1, 1))
    merge_before = [node.xml for node in inner._tbl.iter(qn('w:tcPr'))]
    report = fill_label_tables(nested, {'注册资金': '30000 万元人民币'})
    assert len(report['filled']) == 1 and inner.cell(1, 1).text == '30000 万元人民币'
    assert merge_before == [node.xml for node in inner._tbl.iter(qn('w:tcPr'))]
    report['pending'] = [{'table': 1, 'label': '电话'}, {'table': 2, 'label': '电话'}]
    assert report_lines(report)[1] == '表格自动填写：其他标签表仍有 2 处待补，涉及 1 个字段：电话'
