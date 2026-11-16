from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from PIL import Image

from api.services.tenders.placeholders import attach_media_at_anchor


def _image(path: Path, size):
    Image.new('RGB', size, 'red').save(path)
    return path


def _cm(value):
    return value / 360000


def _test_file(name):
    return Path('storage/tenders') / f'.{name}'


def test_identity_pair_is_one_centered_paragraph():
    document = Document()
    anchor = document.add_paragraph('附：法定代表人身份证复印件或扫描件')
    files = [_test_file('media-layout-front.png'), _test_file('media-layout-back.png')]
    try:
        for file in files:
            _image(file, (900, 600))
        assert attach_media_at_anchor(document, anchor, files, key='id_legal') == 2
        placed = document.paragraphs[1]
        drawings = list(placed._p.iter(qn('w:drawing')))
        assert len(drawings) == 2
        assert placed.alignment == 1
        widths = [_cm(int(node.get('cx')))
                  for drawing in drawings for node in drawing.iter(qn('wp:extent'))]
        assert all(7.3 <= value <= 7.5 for value in widths)
        assert '　' in placed.text
    finally:
        for file in files:
            file.unlink(missing_ok=True)


def test_other_media_is_limited_by_width_and_height():
    document = Document()
    anchor = document.add_paragraph('此处粘贴营业执照扫描件')
    file = _test_file('media-layout-license.png')
    try:
        _image(file, (100, 400))
        assert attach_media_at_anchor(document, anchor, [file], key='license') == 1
        extent = next(anchor._p.iter(qn('wp:extent')))
        assert _cm(int(extent.get('cx'))) <= 14.0
        assert _cm(int(extent.get('cy'))) <= 20.0
        assert anchor.text == ''
    finally:
        file.unlink(missing_ok=True)

