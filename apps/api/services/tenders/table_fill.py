"""Fill empty label/value cells from stored, unambiguous company facts only."""

from __future__ import annotations

import re

from docx.oxml.ns import qn

LABEL_ALIASES = {
    '投标人名称': ('投标人', '投标人名称（联合体牵头人）', '单位名称', '企业名称'),
    '注册地址': ('注册地', '住所', '地址'),
    '法定代表人': ('法定代表人姓名', '法人', '法人代表'),
    '成立时间': ('成立日期', '注册时间'),
    '注册资金': ('注册资本', '注册资本金'),
    '营业执照号': ('统一社会信用代码', '营业执照注册号', '注册号'),
    '经营范围': ('经营范围',),
    '技术负责人': ('技术负责人姓名',),
    '员工总人数': ('员工总数', '职工人数'),
}
_FORM_LABELS = {
    '邮政编码', '联系人', '电话', '传真', '网址', '组织结构', '企业资质等级',
    '开户银行', '账号', '技术职称', '项目经理', '高级职称人员', '中级职称人员',
    '初级职称人员', '技工', '岗位名称', '姓名', '年龄', '性别', '学历', '职务',
    '职称', '学历和专业', '毕业学校', '毕业时间', '拥有的执业资格',
    '执业资格证书编号', '工作年限', '注册建造师资格等级', '建造师专业',
    '安全生产考核合格证书', '拟在本工程任职', '注册证书、等级、专业(如有)',
}
_LIST_HEADERS = {
    '序号', '设备名称', '仪器设备名称', '型号规格', '数量', '姓名', '性别',
    '年龄', '职称', '专业', '资格证书名称及编号', '拟在本项目中担任的工作或岗位',
    '办公室电话', '手机', '邮箱',
}


def _norm_label(text) -> str:
    return re.sub(r'\s+', '', str(text or '')).replace('（', '(').replace('）', ')').rstrip(':：')


def _alias_map() -> dict[str, str]:
    return {_norm_label(name): label for label, aliases in LABEL_ALIASES.items()
            for name in (label, *aliases)}


def fact_values(facts: list[dict]) -> tuple[dict[str, str], list[dict]]:
    """Resolve aliases; reject conflicts and low confidence, retaining source IDs."""
    aliases = _alias_map()
    grouped: dict[str, dict[str, dict]] = {}
    diagnostics: list[dict] = []
    for fact in facts:
        for field in fact.get('fields') or []:
            label = aliases.get(_norm_label(field.get('name')))
            if label is None:
                continue
            raw_value = field.get('value')
            value = re.sub(r'\s+', ' ', '' if raw_value is None else str(raw_value))
            value = value.strip().rstrip(':：')
            value = value.replace('（', '(').replace('）', ')')
            if not value or len(value) > 300:
                continue
            source = {'fileId': str(fact.get('fileId') or ''), 'name': field.get('name', '')}
            if 'conf' in field:
                try:
                    confidence = float(field['conf'])
                except (ValueError, TypeError):
                    confidence = 0.0
                if not confidence >= 0.9:
                    diagnostics.append(dict(type='lowConfidence', lowConfidence=True,
                                            label=label, value=value, conf=field['conf'], **source))
                    continue
            candidate = grouped.setdefault(label, {}).setdefault(
                _norm_label(value), {'value': value, 'sources': []},
            )
            if source not in candidate['sources']:
                candidate['sources'].append(source)
    values = {}
    for label, candidates in grouped.items():
        if len(candidates) == 1:
            values[label] = next(iter(candidates.values()))['value']
        else:
            diagnostics.append(dict(type='conflict', label=label, values=list(candidates.values())))
    return values, diagnostics


def _title(table, index) -> str:
    node = table._tbl.getprevious()
    for _ in range(16):
        if node is None:
            break
        if node.tag == qn('w:p'):
            text = ''.join(t.text or '' for t in node.iter(qn('w:t'))).strip()
            if text and len(text) < 80 and not text.startswith(('注', '说明')):
                return text
        node = node.getprevious()
    return f'表 {index}'


def _cells(row):
    seen = set()
    result = []
    for cell in row.cells:
        if cell._tc not in seen:
            seen.add(cell._tc)
            result.append(cell)
    return result


def _empty(cell) -> bool:
    return (not cell.text.strip() and not cell.tables
            and not cell._tc.xpath('.//w:drawing | .//w:pict | .//w:object'))


def fill_label_tables(
    document, values: dict[str, str], *, skip_labels: set[str] | None = None,
    bidder_name: str = '',
) -> dict:
    """Write text only; table shape, cell properties and existing run styles stay intact.

    bidder_name is verification context, never a source of a filled value.
    """
    from api.services.tenders.company_identity import COMPANY_RE
    from api.services.tenders.company_materials import same_company

    report = {key: [] for key in ('filled', 'kept', 'pending', 'skippedTables',
                                  'conflicts', 'wrongCompany')}
    aliases = _alias_map()
    known = set(aliases) | {_norm_label(label) for label in _FORM_LABELS}
    skip = {aliases.get(_norm_label(label), _norm_label(label)) for label in skip_labels or set()}
    values = {aliases.get(_norm_label(label), _norm_label(label)): value
              for label, value in values.items()}
    seen_tables, seen_labels, written, reported = set(), set(), set(), set()

    def add(kind, index, title, label, **details):
        identity = (kind, index, label)
        if identity not in reported:
            reported.add(identity)
            report[kind].append(dict(table=index, tableTitle=title, label=label, **details))

    def visit(table, index, excluded=False):
        if table._tbl in seen_tables:
            return
        seen_tables.add(table._tbl)
        rows = [_cells(row) for row in table.rows]
        title = _title(table, index)
        header = [_norm_label(cell.text) for cell in rows[0]] if rows else []
        labels = [_norm_label(cell.text) for row in rows for cell in row]
        appendix = '投标函附录' in _norm_label(title) or len(set(labels) & {
            '投标范围', '投标总报价', '质量标准', '投标有效期', '权利义务',
        }) >= 2
        ledger = bool(header and all(header) and len(set(header) & _LIST_HEADERS) >= 2)
        form = any(_norm_label(cell.text) in known and i + 1 < len(row)
                   for row in rows for i, cell in enumerate(row))
        blocked = excluded or appendix or ledger
        if blocked or not form:
            report['skippedTables'].append(dict(
                table=index,
                summary=' | '.join(cell.text for cell in rows[0])[:120] if rows else '',
                reason='投标函附录' if appendix else '多行清单' if ledger else
                       '父表禁止填写' if excluded else '非标签—值表',
            ))
        else:
            for row in rows:
                person_owner = ''
                for i, cell in enumerate(row):
                    raw = _norm_label(cell.text)
                    if raw not in known or cell._tc in seen_labels:
                        continue
                    seen_labels.add(cell._tc)
                    label = aliases.get(raw, raw)
                    if label in {'法定代表人', '技术负责人'}:
                        person_owner = label
                    if label == '姓名' and person_owner:
                        continue  # A person's generic name does not identify their role.
                    if label in skip:
                        add('kept', index, title, label, reason='由 brief 维护，跳过')
                        continue
                    target_index = i + 1
                    if (label in {'法定代表人', '技术负责人'} and target_index < len(row)
                            and _norm_label(row[target_index].text) == '姓名'):
                        target_index += 1
                    if target_index >= len(row) or _norm_label(row[target_index].text) in known:
                        add('pending', index, title, label, reason='未定位到值单元格')
                        continue
                    target = row[target_index]
                    if target._tc in written:
                        continue
                    if not _empty(target):
                        add('kept', index, title, label, value=target.text)
                        continue
                    value = values.get(label)
                    if not value:
                        add('pending', index, title, label, reason='库内无可用事实')
                        continue
                    companies = COMPANY_RE.findall(value)
                    if any(not same_company(company, bidder_name) for company in companies):
                        add('wrongCompany', index, title, label,
                            value=value, bidderName=bidder_name)
                        add('pending', index, title, label, reason='事实中的公司名与本次投标人不符')
                        continue
                    paragraph = target.paragraphs[0]
                    run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
                    run.add_text(value)
                    written.add(target._tc)
                    add('filled', index, title, label, value=value)
        child_cells = set()
        child_index = 0
        for row in rows:
            for cell in row:
                if cell._tc in child_cells:
                    continue
                child_cells.add(cell._tc)
                for child in cell.tables:
                    visit(child, f'{index}.{child_index}', blocked)
                    child_index += 1

    for index, table in enumerate(document.tables):
        visit(table, index)
    return report


def report_lines(report) -> list[str]:
    """Summarize the primary table and other pending/diagnostic items in at most two lines."""
    def names(rows):
        labels = list(dict.fromkeys(row['label'] for row in rows))
        return '、'.join(labels[:8]) + (f'等 {len(labels)} 项' if len(labels) > 8 else '')

    filled, pending = report['filled'], report['pending']
    primary = next(iter(filled or pending), None)
    if primary is None:
        lines = ['表格自动填写：已填 0 项，无可填写的空白标签字段']
    else:
        table = primary['table']
        done = [row for row in filled if row['table'] == table]
        missing = [row for row in pending if row['table'] == table]
        line = f"表格自动填写：{primary['tableTitle']} 已填 {len(done)} 项"
        if done:
            line += '（' + names(done) + '）'
        if missing:
            line += f'，另有 {len(missing)} 项库内无可用数据待补：' + names(missing)
        lines = [line]
    rest = [row for row in pending if primary and row['table'] != primary['table']]
    details = []
    if rest:
        fields = len({row['label'] for row in rest})
        details.append(f'其他标签表仍有 {len(rest)} 处待补，涉及 {fields} 个字段：' + names(rest))
    for kind, title in [('conflict', '事实冲突'), ('lowConfidence', '低置信度')]:
        rows = [row for row in report['conflicts'] if row.get('type') == kind]
        if rows:
            details.append(f'{title} {len(rows)} 项：' + names(rows))
    if report['wrongCompany']:
        details.append('公司不符：' + names(report['wrongCompany']))
    if details:
        lines.append('表格自动填写：' + '；'.join(details))
    return lines
