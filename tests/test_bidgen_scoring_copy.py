"""导出的商务标、技术标不插入给评委看的评分细则复述。"""

from copy import deepcopy
from zipfile import ZipFile

import pytest
from docx import Document

from api.services.bidgen.composer import compose_document
from api.services.bidgen.document_model import ComposeContext
from api.services.bidgen.profile import DEFAULT_PROFILE
from api.services.bidgen.score_check import run_score_check
from api.services.bidgen.word_writer import render_document


def make_context():
    profile = deepcopy(DEFAULT_PROFILE)
    profile["company"]["name"] = "测试投标企业"
    profile["service"]["provinceCount"] = "20"
    profile["performances"] = [{"project": "实际供货项目", "buyer": "采购单位", "product": "电缆", "quantity": "1000", "unit": "千米", "year": "2025"}]
    factors = [("technical", "供货业绩")] * 3 + [
        ("technical", "生产试验设备"), ("technical", "人力资源"), ("technical", "创新能力"),
        ("technical", "工艺管理"), ("technical", "产品制造质量控制"), ("technical", "绿色低碳"),
        ("business", "售后服务"), ("business", "经营状况"),
    ]
    criteria = [{"criterion_id": f"criterion-{index}", "volume": volume, "category": "电缆", "group": "履约能力", "factor": factor,
                 "min_score": 8, "max_score": 17, "rule": "业绩满足公告要求得【8分】；超过公告要求50%及以上得【11分】。"}
                for index, (volume, factor) in enumerate(factors)]
    directory = [("商务文件", "商务支撑文件"), ("商务文件", "其他"),
                 ("技术文件", "业绩文件"), ("技术文件", "技术支撑文件"),
                 ("技术文件", "其他应提交的技术文件"), ("技术文件", "质量保证措施方案")]
    return ComposeContext(
        tender={"project_name": "回归验证项目", "tender_no": "TEST-001", "criteria": criteria,
                "format_items": [{"volume": volume, "number": str(index + 1), "title": title, "level": 1} for index, (volume, title) in enumerate(directory)],
                "supplements": [{"volume": volume, "project": "资质", "item": title, "note": "提供相应证明材料", "scope": ""}
                                for volume, title in [("business", "售后服务"), ("technical", "产品制造质量控制"), ("technical", "绿色低碳")]]},
        profile=profile, lots=[], options={"includeScoreIndex": True},
    )


@pytest.mark.parametrize("volume", ["business", "technical", "full"])
def test_word_omits_scoring_paragraphs_and_retains_response_content(tmp_path, volume):
    context = make_context()
    roots = compose_document(context)
    selected = roots if volume == "full" else [root for root in roots if root.key == volume]
    path = render_document(selected, context, tmp_path / f"{volume}.docx", volume)
    with ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    assert "评审要素：" not in xml
    assert "评审细则：" not in xml
    assert "超过公告要求50%及以上得【11分】" not in xml
    assert "测试投标企业" in xml
    if volume in {"technical", "full"}:
        assert "实际供货项目" in xml
        assert "生产设备与试验设备" in xml
    else:
        assert "投标人响应：" in xml
    assert len(Document(path).paragraphs) > 10
    report = run_score_check(roots, context.tender["criteria"], context.profile, [], "")
    assert len(report["items"]) == 11
    assert all(item["sections"] for item in report["items"] if item["factor"] in {"供货业绩", "售后服务"})
    assert report["items"][0]["rule"] == context.tender["criteria"][0]["rule"]
