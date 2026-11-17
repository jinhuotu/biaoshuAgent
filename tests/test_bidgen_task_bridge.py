"""新版任务登记和审批闭环。独立样例，不依赖 seed 或外部数据库。"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from docx import Document
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from api.routers import bid_gen

from api.services.bidgen import task_bridge as bridge
from api.services.bidgen.service import BidGenService
from api.services.bidgen.tender_parser import TenderInfo
from api.services.tenders import records
from api.services.tenders.assets import load_invitation_text
from api.services.tenders.generate import resolve_output_file
from common.errors import AppError
from db.models.tender import TenderApprovalLog, TenderRecord


class TestDb:
    __test__ = False

    def __init__(self):
        self.engine = create_engine("sqlite://")
        TenderRecord.__table__.create(self.engine)
        TenderApprovalLog.__table__.create(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.counter = 0

    async def execute(self, statement):
        return self.session.execute(statement)

    def add(self, row):
        self.counter += 1
        row.id = self.counter
        self.session.add(row)

    async def commit(self):
        self.session.commit()

    async def rollback(self):
        self.session.rollback()

    async def refresh(self, row):
        self.session.refresh(row)

    async def delete(self, row):
        self.session.delete(row)


@pytest.fixture
def setup_bridge(tmp_path, monkeypatch):
    settings = SimpleNamespace(storage_root=str(tmp_path))
    monkeypatch.setattr("common.config.get_settings", lambda: settings)
    monkeypatch.setattr("api.services.tenders.assets.get_settings", lambda: settings)
    monkeypatch.setattr(bridge, "lock_owner", AsyncMock())
    service = BidGenService(tmp_path, 7)
    identifier = "a" * 32
    folder = service._root / identifier
    (folder / "workspace").mkdir(parents=True)
    (folder / "output").mkdir()
    original = Document()
    original.add_paragraph("项目名称：独立样例采购；投标有效期90日；人员方案最高10分。")
    original.save(folder / "workspace" / "招标正文.docx")
    tender = TenderInfo(project_name="独立样例采购", tender_no="TEST-17", purchasers=["甲采购人", "乙采购人"],
                        bid_deadline="2026-12-20T10:00", validity_days=90)
    tender.files = [{"logicalPath": "招标正文.docx", "role": "main"}]
    files = []
    for key, label in (("business", "商务文件"), ("technical", "技术文件"), ("full", "完整版")):
        name = f"独立样例（{label}）.docx"
        document = Document()
        document.add_heading(label, 1)
        document.add_paragraph("独立样例采购，人员安排完整，按计划交付。")
        document.save(folder / "output" / name)
        files.append({"key": key, "label": label, "name": name, "size": 1, "estimatedPages": 1})
    state = {"id": identifier, "archiveName": "招标正文.docx", "status": "generated",
             "createdAt": "2026-11-17T10:00:00", "updatedAt": "2026-11-17T10:00:01",
             "parserVersion": 8, "tender": tender.to_dict(),
             "selection": {"lotCodes": ["分标1"], "packageNo": "包2", "signDate": "2026-11-17"},
             "tree": [{"name": "招标正文.docx", "logical_path": "招标正文.docx", "chain": [],
                       "depth": 0, "suffix": ".docx", "size": 1, "storage_path": "招标正文.docx", "children": []}],
             "result": {"generatedAt": "2026-11-17T10:01:00", "contentDigest": "version-one",
                        "files": files, "reviewItems": [], "riskCheck": [
                            {"level": "高", "item": "报价", "detail": "金额待核对", "suggestion": "人工核实"}],
                        "analysis": {"basics": [{"label": "项目编号", "value": "TEST-17"}]}}}
    service._save(state)
    user = SimpleNamespace(id=7, username="owner", is_superuser=True)
    db = TestDb()
    yield db, service, service._public(state), user
    db.session.close()
    db.engine.dispose()


@pytest.mark.asyncio
async def test_register_list_preview_and_idempotent_retry(setup_bridge):
    db, service, task, user = setup_bridge
    first = await bridge.register_result(db, service, task, user)
    assert first["recordId"] == "a" * 32
    assert first["workflowStatus"] == "processing"
    assert first["versionConfirmed"] is False
    row = await bridge.find_record(db, task["id"])
    assert (row.project_name, row.tenderer, row.deadline, row.user_id) == (
        "独立样例采购", "甲采购人、乙采购人", "2026-12-20", 7)
    assert row.brief_json["lotNo"] == "分标1"
    assert row.brief_json["packageNo"] == "包2"
    assert "人员方案最高10分" in load_invitation_text(row.brief_json["invitationId"])
    assert resolve_output_file(row.docx_file).read_bytes() == service.output_file(task["id"], task["result"]["files"][0]["name"]).read_bytes()
    mine = await records.list_records(db, user_id=7)
    all_tasks = await records.list_records(db)
    assert mine["total"] == all_tasks["total"] == 1
    assert mine["items"][0]["sourceTaskId"] == "a" * 32
    assert mine["items"][0]["techDocxAvailable"] is True
    stored = row.docx_file
    await bridge.register_result(db, service, deepcopy(task), user)
    assert row.docx_file == stored
    assert db.session.scalar(select(func.count()).select_from(TenderRecord)) == 1
    snapshot = bridge.read_snapshot(row)
    assert snapshot["task"]["result"]["riskCheck"][0]["detail"] == "金额待核对"
    assert snapshot["task"]["result"]["analysis"]["basics"] == [{"label": "项目编号", "value": "TEST-17"}]


@pytest.mark.asyncio
async def test_confirm_submit_reject_regenerate_approve_and_mark(setup_bridge, monkeypatch):
    db, service, task, user = setup_bridge
    await bridge.register_result(db, service, task, user)
    row = await bridge.find_record(db, task["id"])
    flow = {"steps": [{"key": "submit", "name": "提交", "kind": "start"},
                       {"key": "review", "name": "审核", "kind": "review", "roleCode": None},
                       {"key": "done", "name": "完成", "kind": "end"}]}
    monkeypatch.setattr(records, "get_tender_flow", AsyncMock(return_value=flow))
    with pytest.raises(AppError, match="确认当前版本"):
        await records.submit_for_approval(db, task["id"], user=user, admin=False)
    bridge.confirm_version(row)
    await db.commit()
    pending = await records.submit_for_approval(db, task["id"], user=user, admin=False)
    assert pending["status"] == "pending"
    assert pending["approvalSteps"][1]["name"] == "审核"
    with pytest.raises(AppError, match="不可重新生成"):
        bridge.assert_editable(row)
    snapshot_bytes = resolve_output_file(row.docx_file).read_bytes()
    service.output_file(task["id"], task["result"]["files"][0]["name"]).write_bytes(b"source modified")
    assert resolve_output_file(row.docx_file).read_bytes() == snapshot_bytes
    rejected = await records.decide_approval(db, task["id"], user=user, passed=False, comment="补充说明")
    assert rejected["status"] == "processing"
    old_snapshot = bridge.source_of(row)["snapshot"]
    task["result"]["contentDigest"] = "version-two"
    task["result"]["generatedAt"] = "2026-11-17T10:02:00"
    await bridge.register_result(db, service, task, user)
    assert not bridge.source_of(row).get("confirmedAt")
    assert not (resolve_output_file(row.docx_file).parent / old_snapshot).exists()
    bridge.confirm_version(row)
    await db.commit()
    await records.submit_for_approval(db, task["id"], user=user, admin=False)
    approved = await records.decide_approval(db, task["id"], user=user, passed=True)
    assert approved["status"] == "approved"
    submitted = await records.mark_result(db, task["id"], user=user, admin=False, status="submitted")
    assert submitted["status"] == "submitted"


@pytest.mark.asyncio
async def test_tamper_missing_files_owner_and_evidence_guards(setup_bridge):
    db, service, task, user = setup_bridge
    await bridge.register_result(db, service, task, user)
    row = await bridge.find_record(db, task["id"])
    wrong_owner = SimpleNamespace(id=8, username="other")
    with pytest.raises(AppError, match="来源不匹配"):
        await bridge.register_result(db, service, task, wrong_owner)
    with pytest.raises(AppError, match="不存在"):
        bridge.snapshot_file(row, "../outside.docx")
    brief = deepcopy(row.brief_json)
    brief["bidgen"]["reviewItems"] = [{"name": "※导体电阻", "basis": "copy", "value": "0.1"}]
    row.brief_json = brief
    with pytest.raises(AppError, match="L0/L1"):
        bridge.check_evidence(row)
    brief["bidgen"]["reviewItems"][0]["name"] = "外径"
    bridge.check_evidence(row)
    with pytest.raises(AppError, match="L0/L1"):
        bridge.check_evidence(row, final=True)
    resolve_output_file(row.docx_file).write_bytes(b"tampered")
    with pytest.raises(AppError, match="文件已变化"):
        bridge.confirm_version(row)
    resolve_output_file(row.tech_docx_file).unlink()
    with pytest.raises(AppError, match="丢失"):
        bridge.snapshot_file(row, task["result"]["files"][1]["name"])


@pytest.mark.asyncio
async def test_failed_registration_cleans_copies_and_retry_recovers(setup_bridge, monkeypatch):
    db, service, task, user = setup_bridge
    original_commit = db.commit
    monkeypatch.setattr(db, "commit", AsyncMock(side_effect=RuntimeError("database unavailable")))
    with pytest.raises(RuntimeError, match="database unavailable"):
        await bridge.register_result(db, service, task, user)
    assert list((service._root.parents[2] / "tenders").glob("*.docx")) == []
    assert list((service._root.parents[2] / "tenders").glob("*.bidgen.json")) == []
    monkeypatch.setattr(db, "commit", original_commit)
    await bridge.register_result(db, service, task, user)
    assert (await records.list_records(db))["total"] == 1


@pytest.mark.asyncio
async def test_snapshot_access_permissions(setup_bridge, monkeypatch):
    from fastapi import HTTPException
    db, service, task, user = setup_bridge
    await bridge.register_result(db, service, task, user)
    monkeypatch.setattr(bid_gen, "user_is_admin", lambda u: u.is_superuser)
    monkeypatch.setattr(bid_gen, "can_access_menu", lambda u, menu: menu in u.menus)
    viewer = SimpleNamespace(id=8, is_superuser=False, menus=["/tender-qa"])
    with pytest.raises(HTTPException) as error:
        await bid_gen.record_review(task["id"], viewer, db)
    assert error.value.status_code == 403
    viewer.menus = ["/approval"]
    result = await bid_gen.record_review(task["id"], viewer, db)
    assert result["data"]["result"]["analysis"]["basics"][0]["value"] == "TEST-17"


@pytest.mark.asyncio
async def test_generate_endpoint_registers_and_rejects_frozen_version(setup_bridge, monkeypatch):
    db, service, task, user = setup_bridge
    monkeypatch.setattr(bid_gen, "_service", lambda u: service)
    monkeypatch.setattr(bid_gen, "user_is_admin", lambda u: True)
    monkeypatch.setattr(bid_gen, "match_profile_from_library", AsyncMock())
    calls = []
    monkeypatch.setattr(service, "generate", lambda *args: calls.append(args) or task)
    result = await bid_gen.generate(task["id"], bid_gen.GenerateRequest(), user, db)
    assert result["data"]["workflowStatus"] == "processing"
    assert (await records.list_records(db, user_id=7))["total"] == 1
    row = await bridge.find_record(db, task["id"])
    row.status = "pending"
    await db.commit()
    with pytest.raises(AppError, match="不可重新生成"):
        await bid_gen.generate(task["id"], bid_gen.GenerateRequest(), user, db)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_backfill_and_delete_do_not_resurrect_task(setup_bridge, monkeypatch):
    db, service, task, user = setup_bridge
    monkeypatch.setattr(bid_gen, "_service", lambda u: service)
    monkeypatch.setattr(bid_gen, "user_is_admin", lambda u: True)
    monkeypatch.setattr("api.services.tenders.bid_archive.forget_record", AsyncMock())
    listed = await bid_gen.list_tasks(user, db)
    assert listed["data"][0]["recordId"] == task["id"]
    again = await bid_gen.list_tasks(user, db)
    assert len(again["data"]) == 1
    await bid_gen.delete_task(task["id"], user, db)
    assert (await bid_gen.list_tasks(user, db))["data"] == []
    assert (await records.list_records(db))["total"] == 0


@pytest.mark.asyncio
async def test_new_record_uses_existing_generated_and_uploaded_qa(setup_bridge, monkeypatch):
    db, service, task, user = setup_bridge
    await bridge.register_result(db, service, task, user)
    row = await bridge.find_record(db, task["id"])
    inspect = AsyncMock(return_value={"score": 75})
    monkeypatch.setattr("api.services.tenders.qa.inspect_bid", inspect)
    result = await records.inspect_record_qa(db, task["id"], volume="technical")
    assert result == {"score": 75}
    assert inspect.call_args.kwargs["docx_file"] == row.tech_docx_file
    assert inspect.call_args.kwargs["brief"].invitationId == row.brief_json["invitationId"]
    payload = resolve_output_file(row.docx_file).read_bytes()
    await records.inspect_record_qa_upload(db, task["id"], data=payload, filename="终稿.docx", volume="business")
    assert inspect.call_args.kwargs["source"] == "upload"
    assert inspect.call_args.kwargs["bid_path"].read_bytes() == payload


@pytest.mark.asyncio
async def test_real_generation_keeps_original_files_and_registers_all_deliverables(setup_bridge):
    from dataclasses import asdict
    from api.services.bidgen.tender_parser import FormatItem, ScoreCriterion
    db, service, task, user = setup_bridge
    state = service._load(task["id"])
    state["tender"]["format_items"] = [
        asdict(FormatItem("1", "投标函", "business", 1, True, False)),
        asdict(FormatItem("2", "人员配置方案", "technical", 1, True, False)),
    ]
    state["tender"]["criteria"] = [asdict(ScoreCriterion("people", "technical", "", "人员", "人员配置", 0, 10, "提供人员配置方案，最高10分"))]
    service._save(state)
    generated = service.generate(task["id"], task["selection"])
    assert len(generated["result"]["files"]) == 5
    assert {item["key"] for item in generated["result"]["files"]} == {"full", "business", "technical", "report", "bundle"}
    before = {item["name"]: service.output_file(task["id"], item["name"]).read_bytes() for item in generated["result"]["files"]}
    await bridge.register_result(db, service, generated, user)
    row = await bridge.find_record(db, task["id"])
    for name, content in before.items():
        assert service.output_file(task["id"], name).read_bytes() == content
        assert bridge.snapshot_file(row, name).read_bytes() == content
    review = bridge.read_snapshot(row)["task"]["result"]
    assert review["scoreCheck"]["items"][0]["factor"] == "人员配置"
    assert review["analysis"]["basics"][0]["value"] == "独立样例采购"


@pytest.mark.asyncio
async def test_confirmation_rejects_stale_tab_version(setup_bridge, monkeypatch):
    from fastapi import HTTPException
    db, service, task, user = setup_bridge
    monkeypatch.setattr(bid_gen, "_service", lambda u: service)
    await bridge.register_result(db, service, task, user)
    stale = bid_gen.ConfirmVersionRequest(generatedAt=task["result"]["generatedAt"], contentDigest="older-version")
    with pytest.raises(HTTPException) as error:
        await bid_gen.confirm_task(task["id"], stale, user, db)
    assert error.value.status_code == 409
    row = await bridge.find_record(db, task["id"])
    assert not bridge.source_of(row).get("confirmedAt")
    current = bid_gen.ConfirmVersionRequest(generatedAt=task["result"]["generatedAt"], contentDigest="version-one")
    result = await bid_gen.confirm_task(task["id"], current, user, db)
    assert result["data"]["versionConfirmed"] is True
