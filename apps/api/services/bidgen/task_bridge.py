"""新版生成结果接入统一投标任务；文件及自查结果均按版本保存快照。"""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from api.services.bidgen.archive import flatten_entries
from api.services.bidgen.docx_reader import blocks_to_text, read_docx_blocks
from api.services.tenders.assets import save_invitation_text, tenders_output_dir
from api.services.tenders.schema import BidBrief
from common.errors import AppError, ErrorCode
from db.models.tender import TenderRecord
from db.models.user import User


def source_of(row) -> dict:
    brief = row.brief_json if isinstance(row.brief_json, dict) else {}
    value = brief.get("bidgen")
    return value if isinstance(value, dict) else {}


def _file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def find_record(db, task_id: str, *, lock: bool = False):
    stmt = select(TenderRecord).where(TenderRecord.public_id == task_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def lock_owner(db, user_id: int) -> None:
    # 首次生成尚无记录时也要串行化，避免多进程重复创建 / 覆盖同一任务。
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())


def assert_editable(row) -> None:
    if row is not None and row.status != "processing":
        raise AppError(ErrorCode.VALIDATION, "审批中或已定稿的任务不可重新生成，请先驳回", status_code=422)


def _snapshot_path(name: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}\.bidgen\.json", name or ""):
        raise AppError(ErrorCode.VALIDATION, "新版任务快照路径无效", status_code=422)
    return tenders_output_dir() / name


def read_snapshot(row) -> dict:
    source = source_of(row)
    try:
        snapshot = json.loads(_snapshot_path(source.get("snapshot", "")).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppError(ErrorCode.NOT_FOUND, "新版任务快照缺失，请回到编制页重新生成", status_code=404) from exc
    if snapshot.get("task", {}).get("id") != source.get("taskId"):
        raise AppError(ErrorCode.VALIDATION, "新版任务快照不匹配", status_code=422)
    return snapshot


def snapshot_file(row, file_name: str) -> Path:
    snapshot = read_snapshot(row)
    item = next((f for f in snapshot["files"] if f["name"] == file_name), None)
    if not item or not re.fullmatch(r"[a-f0-9]{12}\.(docx|zip)", item.get("stored", "")):
        raise AppError(ErrorCode.NOT_FOUND, "任务文件不存在", status_code=404)
    path = tenders_output_dir() / item["stored"]
    if not path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "任务文件已丢失", status_code=404)
    return path


def _invitation_text(service, state) -> str:
    workspace = service._folder(state["id"]) / "workspace"
    roles = {f["logicalPath"]: f["role"] for f in state["tender"].get("files", [])}
    entries = [service._entry_from_dict(item) for item in state.get("tree", [])]
    text = []
    for entry in flatten_entries(entries):
        if entry.suffix != ".docx" or roles.get(entry.logical_path) not in {"main", "invitation"}:
            continue
        path = (workspace / entry.storage_path).resolve()
        if not path.is_relative_to(workspace.resolve()):
            raise ValueError("招标原件路径超出任务目录")
        text.append(blocks_to_text(read_docx_blocks(path)))
    if not text:
        # 兼容历史 PDF 任务：保留实际解析出的需求及评分细则，不补造原文。
        tender = state["tender"]
        text = [tender.get("project_name", "")]
        text += [f"{c.get('factor', '')}：{c.get('rule', '')}" for c in tender.get("criteria", [])]
        text += [json.dumps(tender.get("project_facts", {}), ensure_ascii=False)]
    return "\n\n".join(text)


def _prepare_snapshot(service, task: dict) -> tuple[dict, dict]:
    copied = []
    snapshot_name = uuid4().hex + ".bidgen.json"
    try:
        for item in task["result"]["files"]:
            original = service.output_file(task["id"], item["name"])
            stored = uuid4().hex[:12] + original.suffix.lower()
            destination = tenders_output_dir() / stored
            shutil.copyfile(original, destination)
            copied.append({**item, "stored": stored, "sha256": _file_digest(destination)})
        snapshot = {"task": deepcopy(task), "files": copied}
        _snapshot_path(snapshot_name).write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        state = service._load(task["id"])
        profile = service.profiles.load()
        invitation_id = sha256(task["id"].encode()).hexdigest()[:16]
        save_invitation_text(_invitation_text(service, state), stem=invitation_id)
        tender = task["tender"]
        brief = BidBrief(
            projectName=tender["projectName"], tenderer="、".join(tender["purchasers"]),
            tenderNo=tender["tenderNo"], bidDate=tender["bidDeadline"][:10],
            bidValidityDays=min(365, max(0, tender["validityDays"])),
            packageNo=task["selection"]["packageNo"], lotNo="、".join(task["selection"]["lotCodes"]),
            bidderName=profile["company"]["name"], legalPersonName=profile["legalRep"]["name"],
            invitationId=invitation_id, generateBoth=True,
        ).model_dump(mode="json")
        brief["bidgen"] = {
            "taskId": task["id"], "snapshot": snapshot_name,
            "generatedAt": task["result"]["generatedAt"],
            "contentDigest": task["result"].get("contentDigest", ""),
            "reviewItems": task["result"].get("reviewItems", []),
        }
        return brief, snapshot
    except Exception:
        _snapshot_path(snapshot_name).unlink(missing_ok=True)
        for item in copied:
            (tenders_output_dir() / item["stored"]).unlink(missing_ok=True)
        raise


def purge_snapshot(row, *, remove_source: bool = False) -> None:
    source = source_of(row)
    if not source:
        return
    try:
        snapshot = read_snapshot(row)
    except AppError:
        snapshot = {"files": []}
    for item in snapshot["files"]:
        name = item.get("stored", "")
        if re.fullmatch(r"[a-f0-9]{12}\.(docx|zip)", name):
            (tenders_output_dir() / name).unlink(missing_ok=True)
    _snapshot_path(source.get("snapshot", "")).unlink(missing_ok=True)
    if remove_source:
        from api.services.bidgen.service import BidGenService, TaskNotFound
        from common.config import get_settings
        try:
            BidGenService(get_settings().storage_root, row.user_id).delete_task(source["taskId"])
        except TaskNotFound:
            pass


async def register_result(db, service, task: dict, user, *, row=None) -> dict:
    if not task.get("result"):
        return task
    await lock_owner(db, user.id)
    row = row if row is not None else await find_record(db, task["id"], lock=True)
    if row is not None:
        if row.user_id != user.id or source_of(row).get("taskId") != task["id"]:
            raise AppError(ErrorCode.FORBIDDEN, "任务来源不匹配", status_code=403)
        source = source_of(row)
        unchanged = (source.get("generatedAt") == task["result"]["generatedAt"]
                     and source.get("contentDigest") == task["result"].get("contentDigest", ""))
        if row.status != "processing":
            return with_workflow(task, row)
        if unchanged:
            try:
                snapshot = read_snapshot(row)
                if all((tenders_output_dir() / f["stored"]).is_file() for f in snapshot["files"]):
                    return with_workflow(task, row)
            except AppError:
                pass  # 仍在编制中时允许从生成目录恢复丢失的快照。
    brief, snapshot = await run_in_threadpool(_prepare_snapshot, service, task)
    previous = SimpleNamespace(brief_json=deepcopy(row.brief_json)) if row is not None else None
    created = row is None
    if created:
        row = TenderRecord(public_id=task["id"], user_id=user.id, username=user.username or "",
                           status="processing", current_step="", project_type="")
    by_key = {item["key"]: item for item in snapshot["files"]}
    if not {"business", "technical"} <= by_key.keys():
        temporary = TenderRecord(brief_json=brief)
        purge_snapshot(temporary)
        raise AppError(ErrorCode.VALIDATION, "商务或技术文件缺失，任务登记失败", status_code=422)
    row.project_name = brief["projectName"][:256]
    row.tenderer = brief["tenderer"][:256]
    row.deadline = brief["bidDate"][:16]
    row.legal_person_name = brief["legalPersonName"][:64]
    row.docx_file = by_key["business"]["stored"]
    row.download_name = by_key["business"]["name"][:256]
    row.tech_docx_file = by_key["technical"]["stored"]
    row.tech_download_name = by_key["technical"]["name"][:256]
    row.brief_json = brief
    row.warnings = list(task["tender"].get("warnings", []))
    if created:
        db.add(row)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        purge_snapshot(TenderRecord(brief_json=brief))
        raise
    if previous is not None:
        purge_snapshot(previous)
    from api.services.tenders.qa import unlink_qa_report
    unlink_qa_report(task["id"])
    return with_workflow(task, row)


def with_workflow(task: dict, row) -> dict:
    from api.services.tenders.records import workflow_locked
    return {**task, "recordId": row.public_id if row is not None else None,
            "workflowStatus": row.status if row is not None else None,
            "workflowLocked": workflow_locked(row.status) if row is not None else False,
            "versionConfirmed": bool(source_of(row).get("confirmedAt")) if row is not None else False}


def verify_files(row) -> None:
    for item in read_snapshot(row)["files"]:
        path = snapshot_file(row, item["name"])
        if _file_digest(path) != item["sha256"]:
            raise AppError(ErrorCode.VALIDATION, "当前文件已变化，请重新生成、检查并确认版本", status_code=422)


def confirm_version(row) -> None:
    verify_files(row)
    brief = deepcopy(row.brief_json)
    brief["bidgen"]["confirmedAt"] = datetime.now(UTC).isoformat()
    row.brief_json = brief


def require_confirmation(row) -> None:
    if not source_of(row):
        return
    if not source_of(row).get("confirmedAt"):
        raise AppError(ErrorCode.VALIDATION, "请先回到新版编制页检查文件并确认当前版本，再提交审批", status_code=422)
    verify_files(row)


def check_evidence(row, *, final: bool = False) -> None:
    source = source_of(row)
    if not source:
        return
    for item in source.get("reviewItems", []):
        if item.get("evidenceLevel") in {"L0", "L1"}:
            continue
        if final or "※" in item.get("name", ""):
            raise AppError(ErrorCode.VALIDATION,
                           f"参数“{item.get('name', '')}”缺少 L0/L1 证据，请补齐真实报告或产品主数据后再提交",
                           status_code=422)
