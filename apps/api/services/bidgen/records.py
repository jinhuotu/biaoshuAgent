"""新版生成结果登记到统一任务表；复制文件快照，不调用旧生成器。"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from api.services.bidgen.docx_reader import blocks_to_text, read_docx_blocks
from api.services.tenders.assets import save_invitation_text, tenders_output_dir
from api.services.tenders.schema import BidBrief
from common.errors import AppError, ErrorCode
from db.models.tender import TenderRecord


def source_metadata(row) -> dict:
    return (row.brief_json or {}).get("_bidgen", {}) if row else {}


async def linked_record(db, task_id: str, user_id: int, *, lock: bool = False):
    statement = select(TenderRecord).where(TenderRecord.public_id == task_id)
    if lock:
        statement = statement.with_for_update()
    row = (await db.execute(statement)).scalar_one_or_none()
    if row and (int(row.user_id) != int(user_id) or source_metadata(row).get("taskId") != task_id):
        raise AppError(ErrorCode.FORBIDDEN, "任务关联不属于当前用户", status_code=403)
    return row


async def require_mutable(db, task_id: str, user_id: int):
    from api.services.tenders.records import workflow_locked
    row = await linked_record(db, task_id, user_id, lock=True)
    if row and workflow_locked(row.status):
        raise AppError(ErrorCode.VALIDATION, "该版本已进入审批或定稿，不能覆盖或删除；请先驳回，或新建任务提交新版本。", status_code=422)
    return row


def record_link(row) -> dict | None:
    if row is None:
        return None
    from api.services.tenders.records import workflow_locked
    return {"id": row.public_id, "status": row.status, "workflowLocked": workflow_locked(row.status)}


def _source_text(service, task: dict) -> str:
    workspace = (service._folder(task["id"]) / "workspace").resolve()
    wanted = {f["logicalPath"] for f in task["tender"]["files"] if f["role"] in {"invitation", "main"}}
    pending, parts = list(task["tree"]), []
    while pending:
        entry = pending.pop(0)
        pending.extend(entry.get("children", []))
        if entry["logical_path"] not in wanted or entry["suffix"].lower() != ".docx":
            continue
        path = (workspace / entry["storage_path"]).resolve()
        if not path.is_relative_to(workspace) or not path.is_file():
            raise ValueError(f"招标原文缺失：{entry['logical_path']}，请重新上传后登记任务。")
        parts.append(blocks_to_text(read_docx_blocks(path)))
    return "\n\n".join(parts)


async def register_generated(db, service, task: dict, user, *, row=None):
    """幂等登记；锁定的记录保持审批时的文件及分数，不覆盖在途版本。"""
    from api.services.tenders.records import workflow_locked
    if not task.get("result"):
        raise AppError(ErrorCode.VALIDATION, "请先生成投标文件", status_code=422)
    if row is None:
        row = await linked_record(db, task["id"], user.id, lock=True)
    if row and workflow_locked(row.status):
        return row
    result, tender = task["result"], task["tender"]
    files = {f["key"]: f for f in result["files"] if f["key"] in {"business", "technical"}}
    if set(files) != {"business", "technical"}:
        raise AppError(ErrorCode.VALIDATION, "商务或技术文件不完整，不能登记任务", status_code=422)
    paths = {key: service.output_file(task["id"], item["name"]) for key, item in files.items()}
    hashes = {key: await run_in_threadpool(lambda p=path: hashlib.sha256(p.read_bytes()).hexdigest()) for key, path in paths.items()}
    fingerprint = hashlib.sha256(json.dumps({"files": hashes, "result": result}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    old = source_metadata(row)
    if old.get("fingerprint") == fingerprint and all((tenders_output_dir() / name).is_file() for name in (row.docx_file, row.tech_docx_file)):
        return row
    text = await run_in_threadpool(_source_text, service, task)
    invitation_id = await run_in_threadpool(save_invitation_text, text)
    copied = {}
    try:
        for key, path in paths.items():
            filename = uuid4().hex[:12] + ".docx"
            await run_in_threadpool(shutil.copyfile, path, tenders_output_dir() / filename)
            copied[key] = filename
        brief = BidBrief(
            projectName=tender["projectName"] or task["archiveName"], tenderer="、".join(tender["purchasers"]),
            tenderNo=tender["tenderNo"], packageNo=task["selection"]["packageNo"],
            invitationId=invitation_id, generateBoth=True, generateVolume="business",
        ).model_dump()
        brief["_bidgen"] = {"taskId": task["id"], "generatedAt": result["generatedAt"],
                            "fingerprint": fingerprint, "scoreCheck": result["scoreCheck"],
                            "certificateCheck": result["certificateCheck"], "fileHashes": hashes}
        if row is None:
            row = TenderRecord(public_id=task["id"], user_id=int(user.id), username=str(getattr(user, "username", ""))[:64],
                               status="processing", current_step="", project_type="", bid_price_yuan=0,
                               legal_person_name="", docx_file="", download_name="")
            db.add(row)
        row.project_name = brief["projectName"][:256]
        row.tenderer = brief["tenderer"][:256]
        row.deadline = tender["bidDeadline"][:10]
        row.docx_file, row.tech_docx_file = copied["business"], copied["technical"]
        row.download_name, row.tech_download_name = files["business"]["name"], files["technical"]["name"]
        row.warnings = tender["warnings"]
        row.brief_json = brief
        await db.commit()
    except Exception:
        await db.rollback()
        for filename in copied.values():
            (tenders_output_dir() / filename).unlink(missing_ok=True)
        raise
    await db.refresh(row)
    return row
