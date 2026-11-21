from io import BytesIO
from types import SimpleNamespace

import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image

from api.deps import get_current_user
from api.routers.tender_workbench import router
from api.services.tenders.assets import save_invitation_text
from api.services.tenders.schema import BidBrief, OutlineItem
from common.config import get_settings
from common.errors import AppError
from db.session import get_db

ROOT = "/tenders/workbench"
PROJECT = "虹桥园区设备更新采购项目"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from api.services.tenders import extract, library_kb

    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))
    user = SimpleNamespace(id=37, is_superuser=True)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: None

    @app.exception_handler(AppError)
    async def error_handler(_request, exc):
        return JSONResponse(status_code=exc.status_code, content={"msg": exc.msg})

    async def parse(_db, upload, **_opts):
        content = Document(BytesIO(await upload.read())).paragraphs[0].text
        brief = BidBrief(
            projectName=PROJECT,
            tenderer="园区采购中心",
            bidderName="示例设备公司",
            tenderNo="HQ-26-01",
            invitationId=save_invitation_text(content),
            layoutMode="outline",
            bidContent="采购两台 SC-26 型试验柜",
            outlineItems=[
                OutlineItem(id="scheme", title="设备实施方案", kind="tech_chapter"),
                OutlineItem(id="services", title="售后服务方案", kind="tech_chapter"),
                OutlineItem(id="training", title="培训方案", kind="tech_chapter"),
                OutlineItem(id="report", title="产品检测报告", kind="scan"),
            ],
        )
        return {
            "brief": brief.model_dump(mode="json"),
            "ocrCapped": content.startswith("缺页"),
            "attachmentMatch": {
                "matched": [],
                "missingFiles": [{"key": "product", "title": "产品检测报告", "required": True}],
            },
        }

    monkeypatch.setattr(extract, "parse_invitation", parse)

    async def no_people(_db, **_options):
        return []

    monkeypatch.setattr(library_kb, "legal_persons_from_library", no_people)
    with TestClient(app) as test_client:
        yield test_client, user


def word_upload(title="招标文件"):
    doc = Document()
    doc.add_paragraph(
        f"{title}。{PROJECT}，采购两台 SC-26 型试验柜，须提供实施方案和产品检测报告。"
    )
    stream = BytesIO()
    doc.save(stream)
    return {"files": (title + ".docx", stream.getvalue(), "application/octet-stream")}


def data(response):
    assert response.status_code == 200, response.text
    return response.json()["data"]


def create(client, *, identify=True, title="招标文件"):
    draft = data(client.post(ROOT, files=word_upload(title)))
    path = f"{ROOT}/{draft['id']}"
    if identify:
        draft = data(client.post(path + "/identify", json={"revision": draft["revision"]}))
    return path, draft


def texts(project):
    return {
        row["id"]: row["draftBody"]
        for row in project["brief"]["outlineItems"]
        if row["kind"] == "tech_chapter"
    }


def test_projects_survive_switching_and_enforce_ownership_and_revisions(client):
    http, user = client
    first, draft = create(http)
    other, pending = create(http, identify=False, title="第二份招标文件")

    saved = data(
        http.put(
            first + "/chapters/scheme",
            json={"revision": draft["revision"], "text": "设备进场后逐台验收。"},
        )
    )
    assert saved["lastChapterId"] == "scheme"
    assert data(http.get(first))["brief"]["outlineItems"] == saved["brief"]["outlineItems"]
    renamed = data(
        http.patch(
            first + "/fields",
            json={"revision": saved["revision"], "tenderNo": "HQ-26-01-B"},
        )
    )
    assert renamed["brief"]["tenderNo"] == "HQ-26-01-B"
    assert renamed["brief"]["outlineItems"] == saved["brief"]["outlineItems"]
    assert data(http.get(other))["sources"] == pending["sources"]
    assert {item["id"] for item in data(http.get(ROOT))} == {draft["id"], pending["id"]}
    assert (
        http.put(
            first + "/chapters/services", json={"revision": draft["revision"], "text": "过期页面"}
        ).status_code
        == 409
    )
    assert texts(data(http.get(first)))["services"] == ""

    user.id = 12
    assert data(http.get(ROOT)) == []
    assert http.get(first).status_code == 404
    assert http.get(first + "/files/output").status_code == 404


def test_invalid_and_incomplete_sources_are_actionable(client):
    http, _ = client
    bad = http.post(ROOT, files={"files": ("要求.pdf", b"pdf bytes")})
    assert bad.status_code == 422
    assert "要求.pdf" in bad.json()["msg"]
    assert ".docx" in bad.json()["msg"]

    path, draft = create(http, title="缺页扫描件")
    assert "缺页扫描件.docx" in draft["inputIssue"]
    assert draft["brief"] is None
    assert http.post(path + "/generate", json={"revision": draft["revision"]}).status_code == 422
    replaced = data(
        http.put(path + "/sources", data={"revision": draft["revision"]}, files=word_upload())
    )
    identified = data(http.post(path + "/identify", json={"revision": replaced["revision"]}))
    assert identified["brief"]["projectName"] == PROJECT
    assert identified["inputIssue"] == ""


def test_local_quote_attachment_and_ai_only_change_selected_data(client, monkeypatch):
    from api.services.models import runtime
    from api.services.tenders import (
        company_materials,
        expiry_check,
        gen_tech_plan,
        library_kb,
        material_intents,
        records,
        tech_draft,
        technical_package,
    )
    from api.services.tenders.placeholders import DEFAULT_SLOTS

    http, _ = client
    path, draft = create(http)
    draft = data(
        http.put(
            path + "/chapters/scheme",
            json={"revision": draft["revision"], "text": "设备进场后逐台验收。"},
        )
    )
    draft = data(
        http.put(
            path + "/chapters/services",
            json={"revision": draft["revision"], "text": "服务台每天登记报修记录。"},
        )
    )

    def async_value(value):
        async def resolve(*_args, **_kwargs):
            return value

        return resolve

    async def scoped(_db, _brief, media):
        return media, set(), set(), []

    async def no_full_redraft(*_args, **_kwargs):
        pytest.fail("Updating the Word draft must use saved chapters")

    async def model(_db, _speed):
        return object()

    async def propose(brief, *, item_id, **_opts):
        assert item_id == "scheme"
        brief.outlineItems[0].draftBody = "设备上线前完成接口联调。"
        brief.outlineItems[1].draftBody = "这段不应写入其他章节。"
        return brief, ["已起草设备实施方案"]

    monkeypatch.setattr(library_kb, "catalog_placeholders", async_value(list(DEFAULT_SLOTS)))
    monkeypatch.setattr(library_kb, "performance_from_library", async_value([]))
    monkeypatch.setattr(library_kb, "count_uncached_performance_files", async_value(0))
    monkeypatch.setattr(library_kb, "resolve_attachments", async_value({}))
    monkeypatch.setattr(library_kb, "library_facts", async_value([]))
    monkeypatch.setattr(records, "_word_library_inputs", async_value(([], {})))
    monkeypatch.setattr(company_materials, "scoped_library_media", scoped)
    monkeypatch.setattr(material_intents, "recognize_material_demands", async_value([]))
    monkeypatch.setattr(expiry_check, "generation_expiry_warnings", async_value([]))
    monkeypatch.setattr(technical_package, "draft_package", no_full_redraft)
    monkeypatch.setattr(gen_tech_plan, "draft_technical_plan", no_full_redraft)
    monkeypatch.setattr(runtime, "build_llm_client", model)
    monkeypatch.setattr(tech_draft, "draft_tech", propose)

    draft = data(http.post(path + "/format", json={"revision": draft["revision"]}))
    assert http.get(path + "/files/format").status_code == 200
    draft = data(http.post(path + "/format/confirm", json={"revision": draft["revision"]}))
    draft = data(http.post(path + "/generate", json={"revision": draft["revision"]}))
    assert draft["output"]["stale"] is False
    initial_word = http.get(path + "/files/output")
    assert initial_word.status_code == 200, initial_word.text
    assert "设备进场后逐台验收。" in "\n".join(
        p.text for p in Document(BytesIO(initial_word.content)).paragraphs
    )

    lines = f"项目名称,{PROJECT}\n名称,规格型号,单位,数量,单价,合价\n试验柜,SC-26,台,2,150,300\n"
    draft = data(
        http.put(
            path + "/quote",
            data={"revision": draft["revision"]},
            files={"file": ("HQ-26-01 报价.csv", lines.encode("utf-8-sig"))},
        )
    )
    row = draft["brief"]["quoteLines"][0]
    assert (row["name"], row["spec"], row["qty"], row["unitPrice"], row["amount"]) == (
        "试验柜",
        "SC-26",
        2,
        150,
        300,
    )
    assert draft["output"]["stale"] is True
    assert texts(draft) == {
        "scheme": "设备进场后逐台验收。",
        "services": "服务台每天登记报修记录。",
        "training": "",
    }
    wrong_lines = (
        f"项目名称,{PROJECT}\n名称,规格型号,单位,数量,单价,合价\n投影仪,PRJ-390,台,2,150,300\n"
    )
    rejected = http.put(
        path + "/quote",
        data={"revision": draft["revision"]},
        files={"file": ("不匹配.csv", wrong_lines.encode("utf-8-sig"))},
    )
    assert rejected.status_code == 422
    assert data(http.get(path))["brief"]["quoteLines"] == draft["brief"]["quoteLines"]

    image = BytesIO()
    Image.new("RGB", (40, 40), "blue").save(image, format="PNG")
    draft = data(
        http.put(
            path + "/attachments/product",
            data={"revision": draft["revision"]},
            files={"file": ("检测报告.png", image.getvalue(), "image/png")},
        )
    )
    assert draft["attachments"]["product"]["name"] == "检测报告.png"
    assert draft["quote"]["name"] == "HQ-26-01 报价.csv"
    proposed = data(
        http.post(path + "/chapters/scheme/suggestion", json={"revision": draft["revision"]})
    )
    assert proposed["text"] == "设备上线前完成接口联调。"
    assert texts(data(http.get(path))) == texts(draft)
    draft = data(
        http.put(
            path + "/chapters/scheme",
            json={"revision": proposed["revision"], "text": proposed["text"]},
        )
    )
    assert texts(draft) == {
        "scheme": "设备上线前完成接口联调。",
        "services": "服务台每天登记报修记录。",
        "training": "",
    }
    draft = data(http.post(path + "/generate", json={"revision": draft["revision"]}))
    assert draft["output"]["stale"] is False
    final = http.get(path + "/files/output")
    assert final.status_code == 200
    word = Document(BytesIO(final.content))
    paragraphs = "\n".join(p.text for p in word.paragraphs)
    assert "设备上线前完成接口联调。" in paragraphs
    assert "服务台每天登记报修记录。" in paragraphs
    assert "设备进场后逐台验收。" not in paragraphs
    assert word.inline_shapes
