import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from docx import Document

from api.services.cablepkg import word_materials
from api.services.tenders import (
    categories,
    company_materials,
    docx_materials,
    expiry_check,
    library_kb,
    match,
    material_identity,
    material_intents,
    placeholders,
    quote,
    records,
    requirement_bid,
    section_materials,
)
from api.services.tenders import (
    slots as slot_files,
)
from api.services.tenders.schema import BidBrief, PlaceholderItem

_MISSING = '资料库缺少营业执照，请在资料库对应项上传'


def _resolver_report(monkeypatch, *, rejected=False, empty=False):
    """Exercise the real resolver with in-memory library parents and file lists."""
    parent = SimpleNamespace(public_id='parent', name='营业执照', tags=['slot:license'])
    paths = [Path('first.docx'), Path('second.docx')]
    monkeypatch.setattr(library_kb, 'get_base_by_public_id', AsyncMock(return_value=parent))
    parent.id = 1
    monkeypatch.setattr(library_kb, '_list_docs', AsyncMock(return_value=[] if empty else [parent]))
    monkeypatch.setattr(library_kb, '_is_material_parent', lambda doc: True)
    monkeypatch.setattr(library_kb, '_group_children', lambda docs: {})
    monkeypatch.setattr(library_kb, '_files_of', lambda *args: list(paths))
    monkeypatch.setattr(library_kb, 'list_slot_files', lambda key: [])
    monkeypatch.setattr(
        material_identity, 'unique_material_files',
        lambda files, **kw: list(dict.fromkeys(files)),
    )
    if rejected:
        monkeypatch.setattr(match, 'material_match_rejection', lambda *args: '来源不符')
    slot = PlaceholderItem(key='license', title='营业执照')
    report = {}
    with_report = asyncio.run(library_kb.resolve_attachments(None, [slot], report=report))
    without_report = asyncio.run(library_kb.resolve_attachments(None, [slot]))
    assert with_report == without_report
    assert set(report['license']) == {'candidates', 'accepted', 'method', 'reasons'}
    assert report['license']['candidates'] == (0 if empty else 2)
    assert report['license']['accepted'] == (0 if empty or rejected else 2)
    assert report['license']['method'] == ('未命中' if empty or rejected else '标准 key')
    return report


def _warnings(monkeypatch, report, *, output_media=None, word_reports=None, generated=None):
    """Run record warning aggregation without a database, converter, or Word renderer."""
    slot = PlaceholderItem(key='license', title='营业执照')
    monkeypatch.setattr(records, '_brief_generate_issues', lambda brief: [])
    monkeypatch.setattr(records, '_word_library_inputs', AsyncMock(return_value=([], {})))
    monkeypatch.setattr(records, '_prepare_scoring_drafts', AsyncMock(return_value={}))
    monkeypatch.setattr(records, '_prepare_other_pledges', AsyncMock(return_value=[]))
    monkeypatch.setattr(quote, 'validate_quote_source', lambda brief: [])
    monkeypatch.setattr(requirement_bid, 'prepare_requirements_bid', lambda brief: [])
    monkeypatch.setattr(material_intents, 'recognize_material_demands', AsyncMock(return_value=[]))
    for name, value in [
        ('catalog_placeholders', [slot]), ('performance_from_library', []),
        ('count_uncached_performance_files', 0), ('legal_persons_from_library', []),
        ('library_facts', []), ('list_library_items', []),
    ]:
        monkeypatch.setattr(library_kb, name, AsyncMock(return_value=value))

    async def resolve(db, slots, *, report=None):
        report.update(match_report)
        return {}

    match_report = report
    monkeypatch.setattr(library_kb, 'resolve_attachments', resolve)
    monkeypatch.setattr(placeholders, 'this_bid_keys', lambda **kw: (['license'], ['license']))
    monkeypatch.setattr(placeholders, 'collect_slots', lambda *args, **kw: [slot])
    monkeypatch.setattr(slot_files, 'attach_invitation_drawings', lambda media, ident: media)

    async def scoped(db, brief, media):
        return media, set(), set(), []

    monkeypatch.setattr(company_materials, 'scoped_library_media', scoped)
    monkeypatch.setattr(docx_materials, 'hydrate_docx_media', lambda media: (
        output_media or {}, word_reports or [],
    ))
    # The three generic exits all feed this final aggregation. Include a different
    # title to ensure filtering uses the complete title, not a prefix or guessed key.
    monkeypatch.setattr(records, 'generate_bid', lambda brief, **kw: {
        'docxFile': 'test.docx', 'warnings': generated or [_MISSING],
    })
    monkeypatch.setattr(categories, 'technical_soft_issues', lambda *args, **kw: [_MISSING])
    monkeypatch.setattr(match, 'build_attachment_match', lambda *args, **kw: {})
    monkeypatch.setattr(match, 'attachment_match_notes', lambda report: [_MISSING])
    monkeypatch.setattr(word_materials, 'reconcile_match', lambda report, *args: report)
    monkeypatch.setattr(expiry_check, 'generation_expiry_warnings', AsyncMock(return_value=[]))
    brief = BidBrief(bidderName='构造数据公司', formatConfirmed=True, generateVolume='business')
    result, _ = asyncio.run(records._prepare_and_generate(None, brief))
    return result['warnings']


def test_existing_rejected_files_are_counted_and_explained(monkeypatch):
    report = _resolver_report(monkeypatch, rejected=True)
    assert report['license']['reasons'] == ['来源不符', '字面不符']
    report['license']['reasons'] = ['来源不符', '类别不符', '来源不符', '状态不符', '第四原因']
    warnings = _warnings(monkeypatch, report)
    assert '资料库《营业执照》有 2 个文件，本次未采用：来源不符、类别不符、状态不符' in warnings
    assert _MISSING not in warnings
    assert not any('第四原因' in note for note in warnings)


def test_truly_missing_keeps_original_warning_and_boxes_are_visible(monkeypatch):
    report = _resolver_report(monkeypatch, empty=True)
    assert report['license']['reasons'] == ['字面不符']
    warnings = _warnings(monkeypatch, report)
    assert warnings.count(_MISSING) == 1
    assert not any('本次未采用' in note for note in warnings)
    document = Document()
    document.add_paragraph('资格要求', style='Heading 1')
    document.add_paragraph('A.请提供营业执照')
    notes, _ = section_materials.append_section_materials(
        document, [PlaceholderItem(key='license', title='营业执照', attachToSection='资格要求')],
        {}, ['license'],
    )
    assert '资料落位核对：1 项，实件 0 项，占位 1 项，未满足 0' in notes
    assert '以下资料仍为占位，需人工核对：license' in notes


def test_word_conversion_failure_replaces_generic_missing_warning(monkeypatch):
    report = _resolver_report(monkeypatch)
    word_report = dict(key='license', title='original', mode='failed', reason='soffice 退出码 2')
    warnings = _warnings(monkeypatch, report, word_reports=[word_report])
    diagnostic = [note for note in warnings if '资料库《营业执照》' in note]
    assert diagnostic == [
        '资料库《营业执照》有 2 个文件，本次未采用：本次范围核验后无可用文件'
        '；Word 转换失败：soffice 退出码 2',
    ]
    assert _MISSING not in warnings
    assert sum('Word 转换失败' in note for note in warnings) == 1
    assert not any('已保留占位' in note for note in warnings)


def test_hydrated_material_removes_generic_warning_once_per_key(monkeypatch):
    report = _resolver_report(monkeypatch)
    success = dict(key='license', title='original', mode='cached-pdf', reason='')
    other_title = '资料库缺少营业执照副本，请在资料库对应项上传'
    warnings = _warnings(
        monkeypatch, report, output_media={'license': [Path('converted.pdf')]},
        word_reports=[success, dict(success)], generated=[_MISSING, other_title],
    )
    assert _MISSING not in warnings and other_title in warnings
    assert warnings.count('资料《营业执照》已转换为可插入文件并落位') == 1
    assert not any('资料库《营业执照》' in note for note in warnings)
