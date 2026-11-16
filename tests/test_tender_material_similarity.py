from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.tenders import library_kb
from api.services.tenders.material_identity import (
    material_title_fingerprint,
    material_title_similarity,
    same_material_title,
)
from api.services.tenders.schema import PlaceholderItem

ONGOING = '正在实施和新承接的项目情况表及所附合同协议书'
SUPPLYING = '正在供货和新承接的项目情况表及中标通知书、合同协议书复印件'


@pytest.mark.parametrize(('left', 'right', 'left_tokens', 'right_tokens', 'score'), [
    (ONGOING, SUPPLYING,
     ('正在履行', '新承接', '项目', '合同协议书'),
     ('正在履行', '新承接', '项目', '中标通知书', '合同协议书'), 0.8),
    ('附：营业执照复印件', '此处粘贴营业执照（加盖公章）',
     ('营业执照',), ('营业执照',), 1.0),
    ('1.近年财务审计报告扫描件', '近年财务审计报告原件',
     ('近年', '财务审计', '报告'), ('近年', '财务审计', '报告'), 1.0),
    ('在此粘贴信用中国查询页', '信用中国查询页相关资料',
     ('信用中国', '查询页'), ('信用中国', '查询页'), 1.0),
])
def test_positive_titles_have_independently_annotated_tokens(
    left, right, left_tokens, right_tokens, score,
):
    assert material_title_fingerprint(left) == left_tokens
    assert material_title_fingerprint(right) == right_tokens
    assert material_title_similarity(left, right) == pytest.approx(score)
    assert same_material_title(left, right)
    assert same_material_title(right, left)


@pytest.mark.parametrize(('left', 'right'), [
    ('法定代表人身份证正反面', '授权代理人身份证'),
    ('营业执照', '资质证书'),
    ('近年完成的类似项目情况表及所附合同协议书', ONGOING),
])
def test_distinct_subjects_people_and_project_periods_never_match(left, right):
    assert not same_material_title(left, right)
    assert not same_material_title(right, left)


def test_empty_negative_and_unclassified_titles_are_not_equivalent():
    assert material_title_fingerprint('扫描件、相关资料') == ()
    assert material_title_similarity('', '') == 0.0
    assert not same_material_title('营业执照', '无需营业执照')
    assert not same_material_title('营业执照', '营业执照（不适用）')
    assert not same_material_title('自定义检查记录', '自定义检查记录')
    # A two-token subset is allowed even below 0.7, after the subject/scope guards.
    assert material_title_similarity('合同协议书中标通知书', '合同协议书中标通知书其他附件') < 0.7
    assert same_material_title('合同协议书中标通知书', '合同协议书中标通知书其他附件')
    assert material_title_similarity('正在实施和新承接的项目情况表', SUPPLYING) == 0.6
    assert same_material_title('正在实施和新承接的项目情况表', SUPPLYING)


@pytest.mark.asyncio
@pytest.mark.parametrize('container', [False, True])
async def test_resolver_takes_only_highest_scoring_title_and_preserves_request_key(
    monkeypatch, caplog, container,
):
    caplog.set_level('INFO', logger=library_kb.logger.name)
    key = 'material_independent_request'
    parent = SimpleNamespace(
        public_id='parent', parent_id=None, name='自定义附件',
        tags=['slot:other_materials' if container else 'slot:m_library_parent'],
    )
    labels = [SUPPLYING, ONGOING, ONGOING]
    paths = [Path('low.docx'), Path('z-best.docx'), Path('a-best.docx')]
    children = [SimpleNamespace(
        public_id=f'child_{index}', parent_id='parent', name=f'child_{index}.docx',
        tags=['material-title:' + label],
    ) for index, label in enumerate(labels)]
    by_id = {child.public_id: path for child, path in zip(children, paths, strict=True)}
    monkeypatch.setattr(
        library_kb, 'get_base_by_public_id', AsyncMock(return_value=SimpleNamespace(id=1)),
    )
    monkeypatch.setattr(library_kb, '_list_docs', AsyncMock(return_value=[parent, *children]))
    monkeypatch.setattr(library_kb, '_doc_file_path', lambda doc: by_id.get(doc.public_id))
    monkeypatch.setattr(library_kb, '_files_of', lambda parent, children: list(paths))
    monkeypatch.setattr(library_kb, 'list_slot_files', lambda key: [])
    slots = [PlaceholderItem(key=key, title=ONGOING)]
    report = {}
    output = await library_kb.resolve_attachments(None, slots, report=report)
    assert output == {key: [Path('a-best.docx')]}
    assert report[key]['accepted'] == 1
    assert report[key]['candidates'] == 3
    assert report[key]['method'] == '标题别名'
    # The winner and original demand key do not depend on library enumeration order.
    monkeypatch.setattr(
        library_kb, '_list_docs', AsyncMock(return_value=[parent, *reversed(children)]),
    )
    assert await library_kb.resolve_attachments(None, slots) == output
    # Also exercise a genuinely different title as the only usable candidate.
    monkeypatch.setattr(library_kb, '_list_docs', AsyncMock(return_value=[parent, children[0]]))
    assert await library_kb.resolve_attachments(None, slots) == {key: [Path('low.docx')]}
    assert any('score=0.800' in record.getMessage() for record in caplog.records)
    short = [PlaceholderItem(key=key, title='正在实施和新承接的项目情况表')]
    assert await library_kb.resolve_attachments(None, short) == {key: [Path('low.docx')]}
