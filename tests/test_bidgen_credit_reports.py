"""信用查询附件从资料库匹配到生成文件，图片/PDF 原页不再退回空粘贴框。"""

import asyncio
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

import fitz
import pytest
from docx import Document
from PIL import Image

from api.services.bidgen.business_sections import build_credit_reports
from api.services.bidgen.document_model import ComposeContext, Section
from api.services.bidgen.library import LibraryDocument, match_library_documents
from api.services.bidgen.library_fields import certificate_category
from api.services.bidgen.profile import DEFAULT_PROFILE, ProfileStore, normalize_certificate
from api.services.bidgen.word_writer import render_document


def context_with_reports(reports):
    profile = deepcopy(DEFAULT_PROFILE)
    profile['company']['name'] = '测试企业'
    profile['certificates'] = [normalize_certificate({'id': str(index), 'category': category, 'name': path.name})
                               for index, (category, path) in enumerate(reports)]
    return ComposeContext(tender={'project_name': '附件回归项目', 'bid_deadline': '2026-05-17T09:00'},
                          profile=profile, lots=[], options={},
                          certificate_files={str(index): str(path) for index, (_category, path) in enumerate(reports)})


def credit_section(context):
    section = Section(key='credit', title='查询报告及截图', level=2)
    build_credit_reports(context, section)
    return section


def make_pdf(path):
    with fitz.open() as document:
        for label, color in [('FIRST PAGE', (1, 0, 0)), ('SECOND PAGE', (0, 0, 1))]:
            page = document.new_page(width=300, height=400)
            page.draw_rect(fitz.Rect(0, 0, 300, 400), color=color, fill=color)
            page.insert_text((40, 80), label, fontsize=18, color=(1, 1, 1))
        document.save(path)


@pytest.mark.parametrize(('title', 'slot', 'category'), [
    ('国家企业信用信息公示系统查询报告.pdf', '', 'credit_gsxt'),
    ('信用中国信用报告.pdf', '', 'credit_china'),
    ('信用中国查询页及国家企业信用信息公示（股东）', 'credit', 'credit_report'),
    ('查询截图.png', 'credit', 'credit_report'),
])
def test_report_categories_follow_specific_filename_or_library_group(title, slot, category):
    assert certificate_category(slot, title) == category


def test_generic_library_credit_attachment_is_copied_and_inserted_once(tmp_path):
    scan = tmp_path / '原件.png'
    Image.new('RGB', (80, 120), 'green').save(scan)
    source = LibraryDocument(public_id='credit-page', name='查询截图',
                             title='信用中国查询页及国家企业信用信息公示（股东）', slot='credit', path=scan)
    store = ProfileStore(tmp_path, 1)
    result = match_library_documents(store, [source])
    assert len(result['profile']['certificates']) == 1
    certificate = result['profile']['certificates'][0]
    assert certificate['category'] == 'credit_report'
    assert certificate['libraryDocumentId'] == 'credit-page'
    assert store.file_path(certificate).read_bytes() == scan.read_bytes()
    context = context_with_reports([('credit_report', scan)])
    section = credit_section(context)
    assert len([block for block in section.blocks if block['type'] == 'image']) == 1
    assert not any(block['type'] == 'placeholder' for block in section.blocks)
    assert '查询日期' in section.plain_text()
    assert any('国家企业信用信息公示' in item['hint'] and '信用中国' in item['hint']
               and '查询日期' in item['hint'] for item in context.pending)
    assert '证明我公司未被列入' not in section.plain_text()


def test_only_missing_report_keeps_manual_placeholder(tmp_path):
    scan = tmp_path / '公示报告.png'
    Image.new('RGB', (80, 120), 'green').save(scan)
    section = credit_section(context_with_reports([('credit_gsxt', scan)]))
    assert len([b for b in section.blocks if b['type'] == 'image']) == 1
    missing = [b['text'] for b in section.blocks if b['type'] == 'placeholder']
    assert len(missing) == 1 and '信用中国' in missing[0]


def test_all_pdf_pages_are_embedded_in_order_and_fit_page(tmp_path):
    pdf = tmp_path / '公示报告.pdf'
    make_pdf(pdf)
    context = context_with_reports([('credit_report', pdf)])
    output = render_document([credit_section(context)], context, tmp_path / 'reports.docx', '商务文件')
    document = Document(output)
    assert len(document.inline_shapes) == 2
    colors = []
    for shape in document.inline_shapes:
        relation = shape._inline.graphic.graphicData.pic.blipFill.blip.embed
        with Image.open(BytesIO(document.part.related_parts[relation].blob)) as picture:
            colors.append(picture.convert('RGB').getpixel((5, 5)))
        assert shape.width.cm <= 14.01 and shape.height.cm <= 19.01
    assert colors == [(255, 0, 0), (0, 0, 255)]
    text = '\n'.join(p.text for p in document.paragraphs)
    assert '第 1/2 页' in text and '第 2/2 页' in text


def test_broken_pdf_has_visible_failure_instead_of_disappearing(tmp_path):
    pdf = tmp_path / '损坏报告.pdf'
    pdf.write_bytes(b'broken pdf')
    context = context_with_reports([('credit_report', pdf)])
    output = render_document([credit_section(context)], context, tmp_path / 'broken.docx', '商务文件')
    document = Document(output)
    assert not document.inline_shapes
    assert any('PDF 无法读取' in cell.text for table in document.tables for row in table.rows for cell in row.cells)


def test_generate_refreshes_actual_library_without_opening_profile_first(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from api.deps import get_current_user
    from api.routers.bid_gen import router
    from api.services.bidgen.service import BidGenService
    from common.config import get_settings
    from db.base import Base
    from db.models.knowledge import KnowledgeBase, KnowledgeDocument
    from db.session import get_db

    monkeypatch.setattr(get_settings(), 'storage_root', str(tmp_path))
    report = tmp_path / 'credit.pdf'
    make_pdf(report)
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "library.sqlite3"}')
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def seed():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            base = KnowledgeBase(public_id='tenderlib01', name='投标资料库', purpose='asset', status='active')
            session.add(base)
            await session.flush()
            session.add(KnowledgeDocument(public_id='actual-credit', base_id=base.id, name='信用查询附件',
                                          file_key='credit.pdf', storage_path='credit.pdf', status='ready',
                                          review_status='approved', tags=['slot:credit']))
            await session.commit()

    asyncio.run(seed())
    store = ProfileStore(tmp_path, 1)
    profile = store.load()
    profile['company'].update(name='测试企业', contact='手工联系人')
    store.save(profile)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_superuser=True, roles=[])

    async def session_override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = session_override
    source = BytesIO()
    document = Document()
    document.add_paragraph('附件回归项目投标邀请书')
    directory = document.add_table(rows=3, cols=6)
    for row, values in zip(directory.rows, [
        ['序号', '文件名称', '提交方式', '', '格式要求', '端口'],
        ['一', '商务文件', '', '', '', ''],
        ['1', '查询报告及截图', '√', '', '√', ''],
    ]):
        for cell, value in zip(row.cells, values):
            cell.text = value
    document.save(source)
    try:
        with TestClient(app) as client:
            created = client.post('/bid-gen/tasks', files={'file': ('招标文件正文.docx', source.getvalue())})
            assert created.status_code == 200
            task_id = created.json()['data']['id']
            response = client.post(f'/bid-gen/tasks/{task_id}/generate', json={})
            assert response.status_code == 200
            business = next(file for file in response.json()['data']['result']['files'] if file['key'] == 'business')
            output = BidGenService(tmp_path, 1).output_file(task_id, business['name'])
            assert len(Document(output).inline_shapes) == 2
            assert any('信用查询附件' in item['hint'] for item in response.json()['data']['result']['pending'])
            saved = store.load()
            assert saved['company']['contact'] == '手工联系人'
            assert saved['certificates'][0]['libraryDocumentId'] == 'actual-credit'
    finally:
        asyncio.run(engine.dispose())
