"""效期同步的独立边界用例；固定日期，不依赖 seed 或被测规则生成期望值。"""

import asyncio
import json
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.bidgen.library import load_library_expiry, match_library_documents
from api.services.bidgen.profile import ProfileStore, check_certificates, normalize_certificate
from common.config import get_settings


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 12, 12, tzinfo=tz)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr("api.services.bidgen.profile.datetime", FixedDatetime)
    monkeypatch.setattr("api.services.knowledge.expiry.datetime", FixedDatetime)
    monkeypatch.setattr(get_settings(), "expire_warn_days", 90)
    monkeypatch.setattr(get_settings(), "expire_danger_days", 30)


@pytest.mark.parametrize("end,expected,days", [
    ("2026-11-11", "expired", -1),
    ("2026-11-12", "expiring", 0),
    ("2026-12-12", "expiring", 30),
    ("2026-12-13", "expiring", 31),
    ("2027-02-10", "expiring", 90),
    ("2027-02-11", "valid", 91),
])
def test_certificate_warning_boundaries(end, expected, days):
    certificate = normalize_certificate({"id": "cert", "category": "business_license", "validTo": end})
    item = check_certificates({"certificates": [certificate]}, "", "")["items"][0]
    assert (item["validTo"], item["status"], item["daysLeft"]) == (end, expected, days)
    assert item["perpetual"] is False


def test_project_dates_still_take_priority_and_perpetual_is_not_expiring():
    cert = normalize_certificate({"category": "business_license", "validTo": "2026-12-13"})
    profile = {"certificates": [cert]}
    item = check_certificates(profile, "2026-12-20", "2027-03-20")["items"][0]
    assert (item["status"], item["daysLeft"]) == ("expired", 31)
    item = check_certificates(profile, "2026-12-01", "2027-03-01")["items"][0]
    assert (item["status"], item["daysLeft"]) == ("insufficient", 31)
    cert["perpetual"] = True
    item = check_certificates(profile, "", "")["items"][0]
    assert (item["status"], item["daysLeft"]) == ("valid", None)


def record(identifier, end=None, **overrides):
    return SimpleNamespace(**{
        "public_id": identifier, "parent_id": None, "name": identifier,
        "expire_at": date.fromisoformat(end) if end else None, "is_perpetual": False,
        "status": "ready", "review_status": "approved", "tags": [],
        "summary": "", "file_key": None, "storage_path": None, **overrides,
    })


def database(records):
    base = SimpleNamespace(id=1, public_id="tenderlib01")
    tender_records = {}

    async def execute(statement):
        if "tender_records" in str(statement):
            task_id = statement.compile().params["public_id_1"]
            return SimpleNamespace(scalar_one_or_none=lambda: tender_records.get(task_id))
        if "knowledge_documents" in str(statement):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: records))
        return SimpleNamespace(scalar_one_or_none=lambda: base)

    return SimpleNamespace(
        execute=AsyncMock(side_effect=execute),
        add=lambda row: tender_records.__setitem__(row.public_id, row),
        commit=AsyncMock(), rollback=AsyncMock(), refresh=AsyncMock(),
    )


def test_library_reminders_include_non_certificates_and_metadata_only(monkeypatch):
    records = [
        record("parent", name="普通材料"),
        record("expired", "2026-11-11"),
        record("child", "2026-12-13", parent_id="parent", name="未完成OCR的附件"),
        record("boundary", "2027-02-10"),
        record("later", "2027-02-11"),
        record("perpetual", "2026-11-11", is_perpetual=True),
        record("unknown"),
        record("rejected", "2026-11-11", review_status="rejected"),
        record("deleted", "2026-11-11", status="deleted"),
        record("hidden", "2026-11-11", parent_id="rejected"),
    ]
    db = database(records)
    items = asyncio.run(load_library_expiry(db))
    assert [item["documentId"] for item in items] == ["expired", "child", "boundary"]
    assert [(i["expire_status"], i["expire_level"], i["expire_days"]) for i in items] == [
        ("expired", "danger", -1), ("expiring", "warning", 31), ("expiring", "warning", 90),
    ]
    assert items[1]["source"] == "普通材料"
    assert items[1]["name"] == "未完成OCR的附件"
    assert items[1]["expire_at"] == "2026-12-13"
    assert db.execute.await_count == 2
    # 调整配置后两处都使用较大的提醒窗口。
    monkeypatch.setattr(get_settings(), "expire_warn_days", 10)
    assert [i["documentId"] for i in asyncio.run(load_library_expiry(db))] == ["expired"]
    cert = normalize_certificate({"category": "other", "validTo": "2026-12-13"})
    assert check_certificates({"certificates": [cert]}, "", "")["items"][0]["status"] == "valid"


def test_expiry_rematch_updates_library_dates_but_keeps_manual_values(tmp_path):
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(b"unchanged library original")
    doc = SimpleNamespace(public_id="license", name="营业执照", title="营业执照", slot="license",
                          tags=[], summary="", text="", path=scan,
                          expire_at=date(2026, 12, 13), is_perpetual=False)
    store = ProfileStore(tmp_path, 1)
    first = match_library_documents(store, [doc])["profile"]
    first["company"]["contact"] = "人工联系人"
    store.save(first)
    doc.expire_at = date(2027, 2, 10)
    updated = match_library_documents(store, [doc])["profile"]
    assert updated["certificates"][0]["validTo"] == "2027-02-10"
    assert updated["certificates"][0]["libraryDocumentId"] == "license"
    assert updated["company"]["contact"] == "人工联系人"
    updated["certificates"][0]["validTo"] = "2028-06-30"
    store.save(updated)
    doc.expire_at = date(2027, 3, 1)
    assert match_library_documents(store, [doc])["profile"]["certificates"][0]["validTo"] == "2028-06-30"
    assert scan.read_bytes() == b"unchanged library original"


def test_api_refresh_is_read_only_and_generation_syncs_before_snapshot(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from docx import Document
    from api.deps import get_current_user
    from api.routers.bid_gen import router
    from api.services.bidgen.service import BidGenService
    from db.session import get_db

    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))
    source = tmp_path / "input.docx"
    word = Document()
    word.add_paragraph("测试项目投标文件格式")
    word.save(source)
    service = BidGenService(tmp_path, 1)
    task = service.create_task("招标文件正文.docx", source)
    scan = tmp_path / "license.pdf"
    scan.write_bytes(b"library original")
    records = [record("license", "2026-12-13", name="营业执照", file_key="license.pdf", tags=["slot:license"]),
               record("other", "2026-12-12", name="其他有期限的文件")]
    db = database(records)
    app = FastAPI()
    app.include_router(router)
    user = SimpleNamespace(id=1, is_superuser=True, roles=[])
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        assert client.get("/bid-gen/profile").status_code == 200
        first = client.post(f"/bid-gen/tasks/{task['id']}/generate", json={})
        assert first.status_code == 200, first.text
        old_result = first.json()["data"]["result"]
        assert old_result["certificateCheck"]["items"][0]["validTo"] == "2026-12-13"
        state_path = tmp_path / "bidgen" / "tasks" / "1" / task["id"] / "state.json"
        old_state = state_path.read_bytes()
        records[0].expire_at = date(2027, 2, 10)
        response = client.get("/bid-gen/library/expiry")
        assert response.status_code == 200
        reminders = response.json()["data"]
        assert reminders["warnDays"] == 90
        assert [i["documentId"] for i in reminders["items"]] == ["other", "license"]
        assert reminders["items"][1]["expire_at"] == "2027-02-10"
        assert state_path.read_bytes() == old_state
        assert service.profiles.load()["certificates"][0]["validTo"] == "2026-12-13"
        regenerated = client.post(f"/bid-gen/tasks/{task['id']}/generate", json={})
        assert regenerated.status_code == 200, regenerated.text
        result = regenerated.json()["data"]["result"]
        assert result["certificateCheck"]["items"][0]["validTo"] == "2027-02-10"
        assert result["certificateCheck"]["items"][0]["status"] == "expiring"
        assert [f["name"] for f in result["files"]] == [f["name"] for f in old_result["files"]]
        assert len(result["files"]) == 4
        assert json.loads(state_path.read_text(encoding="utf-8"))["tree"] == json.loads(old_state)["tree"]
        assert scan.read_bytes() == b"library original"
        # 其他用户不能以任意任务 ID 触发资料同步或重新生成。
        user.id = 2
        assert client.post(f"/bid-gen/tasks/{task['id']}/generate", json={}).status_code == 404
        user.is_superuser = False
        assert client.get("/bid-gen/library/expiry").status_code == 403
