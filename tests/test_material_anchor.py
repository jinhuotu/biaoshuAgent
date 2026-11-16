from pathlib import Path
from types import SimpleNamespace

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image

from api.services.tenders.placeholders import (
    attach_media_at_anchor,
    drop_trailing_orphan_images,
    find_material_anchor,
)


def _image(path: Path, color='red'):
    Image.new('RGB', (900, 600), color).save(path)
    return path


def test_legal_id_attachment_note_is_an_anchor():
    doc = Document()
    paragraph = doc.add_paragraph('附：法定代表人身份证复印件或扫描件')
    anchor = find_material_anchor(doc, 'id_legal')
    assert anchor[0]._p is paragraph._p and anchor[1] == 'attachment-note'


def test_short_license_title_with_empty_following_paragraph_is_an_anchor():
    doc = Document()
    paragraph = doc.add_paragraph('营业执照')
    doc.add_paragraph()
    anchor = find_material_anchor(doc, 'license')
    assert anchor[0]._p is paragraph._p and anchor[1] == 'keyword-placeholder'


def test_matching_directory_text_is_skipped():
    doc = Document()
    doc.add_paragraph('目 录')
    doc.add_paragraph('附：法定代表人身份证复印件或扫描件')
    doc.add_paragraph('二、法定代表人身份证明', style='Heading 1')
    paragraph = doc.add_paragraph('附：法定代表人身份证复印件或扫描件')
    anchor = find_material_anchor(doc, 'id_legal')
    assert anchor[0]._p is paragraph._p


def test_no_anchor_returns_none_and_legal_id_cannot_use_agent_note():
    doc = Document()
    doc.add_paragraph('无相关资料的正文')
    assert find_material_anchor(doc, 'license') is None
    assert find_material_anchor(doc, 'id_legal') is None
    doc.add_paragraph('附：授权代理人身份证复印件')
    assert find_material_anchor(doc, 'id_legal') is None
    assert find_material_anchor(doc, 'id_agent')[1] == 'attachment-note'


def test_images_are_inserted_after_note_before_the_next_chapter(tmp_path):
    doc = Document()
    note = doc.add_paragraph('附：法定代表人身份证复印件或扫描件')
    following = doc.add_paragraph('三、授权委托书', style='Heading 1')
    files = [_image(tmp_path / 'front.png'), _image(tmp_path / 'back.png', 'blue')]
    anchor = find_material_anchor(doc, 'id_legal')
    assert attach_media_at_anchor(doc, anchor[0], files) == 2
    paragraphs = doc.paragraphs
    assert paragraphs[0]._p is note._p and paragraphs[3]._p is following._p
    assert [len(list(p._p.iter(qn('w:drawing')))) for p in paragraphs] == [0, 1, 1, 0]
    for paragraph in paragraphs[1:3]:
        assert paragraph.alignment == 1
        extent = next(paragraph._p.iter(qn('wp:extent')))
        assert int(extent.get('cx')) <= 15.5 * 360000
        assert abs(int(extent.get('cx')) / int(extent.get('cy')) - 1.5) < 0.001
    assert attach_media_at_anchor(doc, anchor[0], files) == 2
    assert len(doc.paragraphs) == 4


def test_placeholder_text_and_border_are_preserved_until_success(tmp_path):
    doc = Document()
    placeholder = doc.add_paragraph('此处粘贴营业执照扫描件')
    border = OxmlElement('w:pBdr')
    placeholder._p.get_or_add_pPr().append(border)
    anchor = find_material_anchor(doc, 'license')
    assert attach_media_at_anchor(doc, anchor[0], [tmp_path / 'missing.png']) == 0
    assert placeholder.text == '此处粘贴营业执照扫描件'
    assert placeholder._p.pPr.find(qn('w:pBdr')) is border
    assert attach_media_at_anchor(doc, anchor[0], [_image(tmp_path / 'license.png')]) == 1
    assert placeholder.text == ''
    assert placeholder._p.pPr.find(qn('w:pBdr')) is border
    assert len(list(placeholder._p.iter(qn('w:drawing')))) == 1


def test_cleanup_deletes_only_a_successfully_replaced_tail_duplicate(tmp_path):
    file = _image(tmp_path / 'id.png')
    unrelated = _image(tmp_path / 'other.png', 'blue')
    doc = Document()
    doc.add_paragraph('附：法定代表人身份证复印件或扫描件')
    doc.add_paragraph('下一章节', style='Heading 1')
    orphan = doc.add_paragraph()
    orphan.add_run().add_picture(str(file))
    other = doc.add_paragraph()
    other.add_run().add_picture(str(unrelated))
    anchor = find_material_anchor(doc, 'id_legal')
    assert attach_media_at_anchor(doc, anchor[0], [file]) == 1
    assert drop_trailing_orphan_images(doc, set()) == 0
    assert drop_trailing_orphan_images(doc, {'id_legal'}) == 1
    assert orphan._p.getparent() is None and other._p.getparent() is doc.element.body
    assert drop_trailing_orphan_images(doc, {'id_legal'}) == 0


def test_cleanup_survives_reload_and_retains_new_images_at_the_end(tmp_path):
    file = _image(tmp_path / 'id.png')
    doc = Document()
    doc.add_paragraph('附：法定代表人身份证复印件或扫描件')
    anchor = find_material_anchor(doc, 'id_legal')
    assert attach_media_at_anchor(doc, anchor[0], [file]) == 1
    path = tmp_path / 'saved.docx'
    doc.save(path)
    restored = Document(path)
    assert drop_trailing_orphan_images(restored, {'id_legal'}) == 0
    assert len(list(restored.element.body.iter(qn('w:drawing')))) == 1


def test_library_no_anchor_warns_without_appending_and_skips_legacy_writer(tmp_path, monkeypatch):
    from api.services.tenders import pdf_format, scan_placement

    received = {}

    def legacy(doc, brief, media, slots, **kwargs):
        received.update(kwargs)
        return [], set()

    monkeypatch.setattr(scan_placement, 'place_library_scans', legacy)
    doc = Document()
    doc.add_paragraph('正文')
    media = {'license': [_image(tmp_path / 'license.png')]}
    notes = pdf_format._attach_library(doc, SimpleNamespace(authEnabled=False), media)
    assert notes == ['资料「license」未找到落位锚点，已保留占位框，请人工粘贴']
    assert received['skip_keys'] == {'license'}
    assert len(doc.paragraphs) == 1 and not list(doc.element.body.iter(qn('w:drawing')))
    assert len(media['license']) == 1


def test_partial_failure_keeps_placeholder_and_reports_the_file(tmp_path, monkeypatch):
    from api.services.tenders import pdf_format, scan_placement

    monkeypatch.setattr(scan_placement, 'place_library_scans', lambda *a, **kw: ([], set()))
    doc = Document()
    placeholder = doc.add_paragraph('此处粘贴营业执照扫描件')
    notes = pdf_format._attach_library(doc, SimpleNamespace(authEnabled=False), {
        'license': [_image(tmp_path / 'license.png'), tmp_path / 'bad.png'],
    })
    assert placeholder.text == '此处粘贴营业执照扫描件'
    assert any('未能全部插入' in note and 'bad.png' in note for note in notes)
    assert len(list(doc.element.body.iter(qn('w:drawing')))) == 1


def test_late_material_writer_cannot_fall_back_to_the_document_end(tmp_path):
    from api.services.tenders.placeholders import _drop_after
    from api.services.tenders.schema import PlaceholderItem

    doc = Document()
    end = doc.add_paragraph('普通正文')
    file = _image(tmp_path / 'license.png')
    assert not _drop_after(doc, end._p, PlaceholderItem(key='license', title='营业执照'), [file])
    assert end.text == '普通正文' and not list(doc.element.body.iter(qn('w:drawing')))


def test_cleanup_preserves_unplaced_owner_even_if_image_relation_is_shared(tmp_path):
    import hashlib

    file = _image(tmp_path / 'shared.png')
    doc = Document()
    doc.add_paragraph('附：法定代表人身份证复印件或扫描件')
    original = doc.add_paragraph()
    original.add_run().add_picture(str(file))
    mark = OxmlElement('w:bookmarkStart')
    mark.set(qn('w:name'), 'result_' + hashlib.sha256(b'license').hexdigest()[:20] + '_img')
    mark.set(qn('w:id'), '10')
    original._p.append(mark)
    anchor = find_material_anchor(doc, 'id_legal')
    assert attach_media_at_anchor(doc, anchor[0], [file]) == 1
    assert drop_trailing_orphan_images(doc, {'id_legal'}) == 0
    assert original._p.getparent() is doc.element.body


def test_cleanup_does_not_touch_images_outside_the_last_twenty_paragraphs(tmp_path):
    file = _image(tmp_path / 'id.png')
    doc = Document()
    original = doc.add_paragraph()
    original.add_run().add_picture(str(file))
    for _ in range(21):
        doc.add_paragraph('正文')
    doc.add_paragraph('附：法定代表人身份证复印件或扫描件')
    anchor = find_material_anchor(doc, 'id_legal')
    assert attach_media_at_anchor(doc, anchor[0], [file]) == 1
    assert drop_trailing_orphan_images(doc, {'id_legal'}) == 0
    assert original._p.getparent() is doc.element.body


def test_portrait_identity_photo_uses_existing_rotation_without_crop_or_stretch(tmp_path):
    import io

    file = tmp_path / 'portrait.png'
    Image.new('RGB', (600, 900), 'red').save(file)
    doc = Document()
    doc.add_paragraph('附：法定代表人身份证复印件或扫描件')
    old = doc.add_paragraph()
    old.add_run().add_picture(str(file))
    anchor = find_material_anchor(doc, 'id_legal')
    assert attach_media_at_anchor(doc, anchor[0], [file]) == 1
    host = doc.paragraphs[1]
    rid = next(host._p.iter(qn('a:blip'))).get(qn('r:embed'))
    with Image.open(io.BytesIO(doc.part.related_parts[rid].blob)) as image:
        assert image.size == (900, 600)
        assert image.getpixel((0, 0)) == (255, 0, 0)
    extent = next(host._p.iter(qn('wp:extent')))
    assert abs(int(extent.get('cx')) / int(extent.get('cy')) - 1.5) < 0.001
    assert drop_trailing_orphan_images(doc, {'id_legal'}) == 1
