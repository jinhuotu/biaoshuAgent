"""Company scoping through the actual library and knowledge endpoints."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import BigInteger, Integer, MetaData, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_current_user
from api.routers import knowledge, tenders
from api.services.knowledge import ingest
from api.services.tenders import company_library
from api.services.tenders.library_scope import current_company_base
from common.config import get_settings
from common.errors import AppError
from db.base import Base
from db.models.knowledge import KnowledgeBase, KnowledgeDocument, KnowledgeIngestTask
from db.models.user import User
from db.session import get_db

ROOT = "/tenders/materials/companies"
ALPHA = {"name": "甲方线缆有限公司", "creditCode": "911100001000000001", "aliases": ["甲线缆"]}
BETA = {"name": "乙方线缆有限公司", "creditCode": "911100001000000002"}
ALPHA_SCAN = b"%PDF-1.4\nalpha independently supplied original"
BETA_SCAN = b"%PDF-1.4\nbeta independently supplied original"
NEW_ALPHA_SCAN = b"%PDF-1.4\nalpha independently supplied replacement"


@pytest.fixture
async def library_client(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    for table in metadata.tables.values():
        for column in table.primary_key.columns:
            if isinstance(column.type, BigInteger) and column.autoincrement is True:
                column.type = Integer()
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    async with sessions() as db:
        for user_id, username in [(1, "admin"), (2, "manager"), (3, "viewer")]:
            db.add(User(id=user_id, username=username, hashed_password="fixture"))
        # This file shares the same slot key with both company libraries.
        legacy = KnowledgeBase(public_id="tenderlib01", name="投标资料库", purpose="asset")
        db.add(legacy)
        await db.commit()
    legacy_path = tmp_path / "tender-assets" / "slots" / "license" / "legacy.pdf"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(b"independent legacy original")
    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))
    monkeypatch.setattr(company_library, "require_embedding_ready", AsyncMock())
    monkeypatch.setattr(company_library, "notify_ingest_worker", Mock())
    monkeypatch.setattr(company_library.library, "_purge_doc_vectors", Mock())

    app = FastAPI()
    app.include_router(tenders.router)
    app.include_router(knowledge.router)

    async def test_user(request: Request):
        user_id = int(request.headers.get("x-test-user", "1"))
        return SimpleNamespace(
            id=user_id, username=f"user{user_id}", is_superuser=user_id == 1, roles=[]
        )

    async def test_db():
        async with sessions() as db:
            yield db

    @app.exception_handler(AppError)
    async def handle_error(request, exc):
        return JSONResponse({"msg": exc.msg}, status_code=exc.status_code)

    app.dependency_overrides[get_current_user] = test_user
    app.dependency_overrides[get_db] = test_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, sessions, tmp_path
    finally:
        await engine.dispose()


async def create_company(client, payload):
    response = await client.post(ROOT, json=payload)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def library_url(company):
    return f"{ROOT}/{company['id']}/library"


def user(user_id):
    return {"x-test-user": str(user_id)}


async def upload(client, company, content, *, replace=False, headers=None):
    return await client.post(
        library_url(company) + "/items/license/files",
        files=[("files", ("执照.pdf", content, "application/pdf"))],
        data={"replace": str(replace)},
        headers=headers,
    )


async def license_files(client, company):
    response = await client.get(library_url(company))
    assert response.status_code == 200, response.text
    slot = next(slot for slot in response.json()["data"]["slots"] if slot["key"] == "license")
    return slot["files"]


async def test_scoped_permissions_and_atomic_replacement(library_client, monkeypatch):
    client, sessions, root = library_client
    alpha = await create_company(client, ALPHA)
    beta = await create_company(client, BETA)
    assert alpha["id"] != beta["id"] and alpha["baseId"] != beta["baseId"]
    assert alpha["aliases"] == ["甲线缆"]
    assert (await client.post(ROOT, json=ALPHA)).status_code == 409
    assert (await client.post(ROOT, json=ALPHA, headers=user(2))).status_code == 403
    assert await license_files(client, alpha) == []
    assert await license_files(client, beta) == []

    grant = await client.put(
        f"/knowledge/bases/{alpha['baseId']}/acl",
        json={
            "grants": [
                {"subjectType": "user", "subjectId": 2, "canManage": True},
                {"subjectType": "user", "subjectId": 3, "canView": True},
            ]
        },
    )
    assert grant.status_code == 200, grant.text
    listing = (await client.get(ROOT, headers=user(2))).json()["data"]
    assert [item["id"] for item in listing["items"]] == [alpha["id"]]
    assert listing["legacyAvailable"] is False
    assert (await client.get(library_url(beta), headers=user(2))).status_code == 404
    assert (await upload(client, alpha, ALPHA_SCAN, headers=user(3))).status_code == 403
    assert (await upload(client, alpha, ALPHA_SCAN, headers=user(2))).status_code == 200
    assert (await upload(client, beta, BETA_SCAN)).status_code == 200
    (old_alpha,) = await license_files(client, alpha)
    (original_beta,) = await license_files(client, beta)

    failed = await client.post(
        library_url(alpha) + "/items/license/files",
        files=[
            ("files", ("replacement.pdf", NEW_ALPHA_SCAN)),
            ("files", ("not-a-scan.exe", b"unrelated file")),
        ],
        data={"replace": "true"},
        headers=user(2),
    )
    assert failed.status_code == 422
    assert await license_files(client, alpha) == [old_alpha]
    original_dir = root / "tender-assets" / "companies" / alpha["id"] / "license"
    with monkeypatch.context() as patch:
        patch.setattr(
            company_library,
            "stage_ingest_task",
            Mock(side_effect=RuntimeError("queue unavailable")),
        )
        with pytest.raises(RuntimeError, match="queue unavailable"):
            await upload(client, alpha, NEW_ALPHA_SCAN, replace=True, headers=user(2))
    assert [path.read_bytes() for path in original_dir.iterdir()] == [ALPHA_SCAN]
    assert await license_files(client, alpha) == [old_alpha]
    assert (await upload(client, alpha, NEW_ALPHA_SCAN, replace=True, headers=user(2))).is_success
    (new_alpha,) = await license_files(client, alpha)
    assert new_alpha["id"] != old_alpha["id"]
    assert await license_files(client, beta) == [original_beta]

    def file_response(company, doc, action):
        return f"/knowledge/documents/{doc['id']}/{action}?baseId={company['baseId']}"

    own_file = await client.get(file_response(alpha, new_alpha, "file"), headers=user(3))
    assert own_file.content == NEW_ALPHA_SCAN
    assert own_file.status_code == 200
    for action in ("preview", "download", "file"):
        foreign_file = await client.get(file_response(beta, original_beta, action), headers=user(2))
        mixed_ids = await client.get(file_response(alpha, original_beta, action), headers=user(2))
        assert foreign_file.status_code == mixed_ids.status_code == 404
    assert (await client.get(file_response(beta, original_beta, "download"))).content == BETA_SCAN
    assert (await client.get(file_response(alpha, old_alpha, "file"))).status_code == 404
    wrong_delete = await client.delete(library_url(alpha) + f"/files/{original_beta['id']}")
    assert wrong_delete.status_code == 404
    assert (await client.delete(f"/knowledge/bases/{alpha['baseId']}")).status_code == 422

    async with sessions() as db:
        tasks = (
            await db.execute(
                select(KnowledgeIngestTask.base_id, KnowledgeDocument.public_id).join(
                    KnowledgeDocument, KnowledgeIngestTask.document_id == KnowledgeDocument.id
                )
            )
        ).all()
    assert {doc_id for _, doc_id in tasks} == {new_alpha["id"], original_beta["id"]}
    assert len({base_id for base_id, _ in tasks}) == 2
    legacy = root / "tender-assets" / "slots" / "license" / "legacy.pdf"
    assert legacy.read_bytes() == b"independent legacy original"
    assert (
        await client.delete(library_url(alpha) + "/items/license/files", headers=user(2))
    ).is_success
    assert await license_files(client, alpha) == []
    assert await license_files(client, beta) == [original_beta]
    assert legacy.is_file()


async def test_search_uses_company_acl_and_disabled_company_hides_existing_results(
    library_client,
    monkeypatch,
):
    client, sessions, _ = library_client
    alpha = await create_company(client, ALPHA)
    beta = await create_company(client, BETA)
    assert (await upload(client, alpha, ALPHA_SCAN)).is_success
    assert (await upload(client, beta, BETA_SCAN)).is_success
    alpha_id = (await license_files(client, alpha))[0]["id"]
    beta_id = (await license_files(client, beta))[0]["id"]
    async with sessions() as db:
        docs = (
            await db.scalars(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.public_id.in_([alpha_id, beta_id])
                )
            )
        ).all()
        for doc in docs:
            doc.status = "ready"
        await db.commit()
    assert (
        await client.put(
            f"/knowledge/bases/{alpha['baseId']}/acl",
            json={
                "grants": [
                    {"subjectType": "user", "subjectId": 2, "canUse": True},
                ]
            },
        )
    ).is_success

    def hit(company, doc_id, marker):
        return {
            "kb_id": company["baseId"],
            "doc_id": doc_id,
            "chunk_index": 0,
            "score": 0.99,
            "content": f"营业执照 企业标记 {marker}",
            "tags": ["slot:license"],
        }

    alpha_hit = hit(alpha, alpha_id, "甲独有")
    beta_hit = hit(beta, beta_id, "乙独有")
    vector = SimpleNamespace(
        search=Mock(return_value=[alpha_hit, beta_hit]),
        search_text_contains=Mock(return_value=[]),
        fetch_neighbor_chunks=Mock(return_value=[beta_hit]),
    )
    embedder = SimpleNamespace(embed_query=AsyncMock(return_value=[1.0]))
    monkeypatch.setattr(ingest, "get_embedding_client", AsyncMock(return_value=embedder))
    monkeypatch.setattr(ingest, "get_qdrant_store", lambda: vector)
    monkeypatch.setattr(get_settings(), "kb_neighbor_window", 1)

    visible = (await client.get("/knowledge/bases?access=use", headers=user(2))).json()["data"]
    assert [item["id"] for item in visible["items"]] == [alpha["baseId"]]
    response = await client.post("/knowledge/search", headers=user(2), json={"query": "营业执照"})
    assert response.status_code == 200, response.text
    assert [chunk["doc_id"] for chunk in response.json()["data"]["chunks"]] == [alpha_id]
    assert vector.search.call_args.kwargs["kb_ids"] == [alpha["baseId"]]
    forbidden = await client.post(
        "/knowledge/search",
        headers=user(2),
        json={
            "query": "营业执照",
            "baseId": beta["baseId"],
        },
    )
    assert forbidden.status_code == 404
    async with sessions() as db:
        # Generic/internal retrieval has no company authorization from the caller.
        assert await ingest.search_chunks(db, query="营业执照") == []
        assert await ingest.search_chunks(db, query="营业执照", kb_ids=[beta["baseId"]]) == []

    inactive = await client.put(f"{ROOT}/{alpha['id']}", json={**ALPHA, "active": False})
    assert inactive.status_code == 200
    assert inactive.json()["data"]["baseId"] == alpha["baseId"]
    assert (await client.get(library_url(alpha), headers=user(2))).status_code == 404
    assert (
        await client.get(f"/knowledge/bases/{alpha['baseId']}", headers=user(2))
    ).status_code == 404
    assert (await client.get(ROOT, headers=user(2))).json()["data"]["items"] == []
    assert (await client.put(f"{ROOT}/{alpha['id']}", json=ALPHA)).status_code == 200
    assert [file["id"] for file in await license_files(client, alpha)] == [alpha_id]


async def test_classic_preflight_parse_and_generation_use_the_bound_company(
    library_client, monkeypatch
):
    from api.services.tenders import bid_archive, records, scoring_tasks
    from api.services.tenders.company_materials import scoped_library_media
    from api.services.tenders.library_kb import resolve_attachments
    from api.services.tenders.schema import BidBrief, PlaceholderItem

    client, sessions, _ = library_client
    alpha = await create_company(client, ALPHA)
    beta = await create_company(client, BETA)
    assert (await upload(client, alpha, ALPHA_SCAN)).is_success
    assert (await upload(client, beta, BETA_SCAN)).is_success
    grant = await client.put(
        f"/knowledge/bases/{alpha['baseId']}/acl",
        json={
            "grants": [
                {"subjectType": "user", "subjectId": 2, "canUse": True},
                {"subjectType": "user", "subjectId": 3, "canView": True},
            ]
        },
    )
    assert grant.is_success

    tender = {
        "projectName": "测试采购项目",
        "tenderer": "测试采购人",
        "bidderCompanyId": alpha["id"],
        "bidderName": BETA["name"],
        "bidderCreditCode": BETA["creditCode"],
        "includeSlotKeys": ["license"],
        "requiredSlotKeys": ["license"],
    }
    alpha_url = f"{ROOT}/{alpha['id']}/classic"
    beta_url = f"{ROOT}/{beta['id']}/classic"
    report = await client.post(alpha_url + "/preflight", json=tender, headers=user(2))
    assert report.is_success, report.text
    preflight = report.json()["data"]
    assert preflight["companyName"] == ALPHA["name"]
    assert [
        row["fileCount"]
        for row in preflight["attachmentMatch"]["matched"]
        if row["key"] == "license"
    ] == [1]
    assert (
        await client.post(alpha_url + "/preflight", json=tender, headers=user(3))
    ).status_code == 403
    foreign = {**tender, "bidderCompanyId": beta["id"]}
    assert (
        await client.post(beta_url + "/preflight", json=foreign, headers=user(2))
    ).status_code == 404

    async def parse_sample(db, files, *, current, **kwargs):
        assert current_company_base().public_id == alpha["baseId"]
        assert current.bidderName == ALPHA["name"]
        return {"brief": current.model_dump(mode="json"), "attachmentMatch": {}}

    monkeypatch.setattr(tenders, "parse_invitation", parse_sample)
    monkeypatch.setattr(scoring_tasks, "create_scoring_task", Mock(return_value="score-fixture"))
    monkeypatch.setattr("api.services.tenders.demo.demo_enabled", lambda: False)
    parsed = await client.post(
        alpha_url + "/parse-invitation",
        headers=user(2),
        files=[("files", ("邀请书.docx", b"fixture"))],
        data={"current": BidBrief.model_validate(tender).model_dump_json()},
    )
    assert parsed.is_success, parsed.text
    parsed = parsed.json()["data"]
    assert parsed["brief"]["bidderCompanyId"] == alpha["id"]
    assert parsed["brief"]["bidderCreditCode"] == ALPHA["creditCode"]
    assert [row["key"] for row in parsed["attachmentMatch"]["matched"]] == ["license"]

    captured = []

    async def render_fixture(db, brief, **kwargs):
        assert current_company_base().public_id == alpha["baseId"]
        slots = [PlaceholderItem(key="license", title="营业执照")]
        candidate = await resolve_attachments(db, slots)
        media, _, _, _ = await scoped_library_media(db, brief, candidate)
        captured.append((brief.bidderName, [path.read_bytes() for path in media["license"]]))
        return {"docxFile": f"fixture-{len(captured)}.docx"}, {"matched": []}

    monkeypatch.setattr(records, "_prepare_and_generate", render_fixture)
    monkeypatch.setattr(bid_archive, "schedule_archive", Mock())
    assert (
        await client.post("/tenders/generate", json=foreign, headers=user(2))
    ).status_code == 404
    output = await client.post("/tenders/generate", json=tender, headers=user(2))
    assert output.is_success, output.text
    record_id = output.json()["data"]["id"]
    detail = (await client.get(f"/tenders/records/{record_id}", headers=user(2))).json()["data"]
    assert detail["brief"]["bidderCompanyId"] == alpha["id"]
    assert detail["brief"]["bidderName"] == ALPHA["name"]
    regenerate = f"/tenders/records/{record_id}/regenerate"
    assert (await client.post(regenerate, headers=user(2))).is_success
    assert (await client.post(regenerate, json=foreign, headers=user(2))).status_code == 422
    assert (
        await client.post(regenerate, json={**tender, "bidderCompanyId": ""}, headers=user(2))
    ).status_code == 422
    assert captured == [(ALPHA["name"], [ALPHA_SCAN]), (ALPHA["name"], [ALPHA_SCAN])]

    async with sessions() as db:
        assert current_company_base() is None
        legacy = await resolve_attachments(db, [PlaceholderItem(key="license", title="营业执照")])
        assert [path.read_bytes() for path in legacy["license"]] == [b"independent legacy original"]
