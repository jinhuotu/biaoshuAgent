"""AI 标书生成（本地算法版）：上传招标压缩包 → 规则解析 → 生成 Word → 评分自查 / 证件效期校验。

全程不调用大模型或任何外部 API。
"""

from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from api.deps import CurrentUser, DbSession, user_is_admin
from api.services.bidgen.archive import ArchiveError
from api.services.bidgen.corpus import CORPUS_SUFFIXES
from api.services.bidgen.library import match_profile_from_library
from api.services.bidgen.profile import CERT_CATEGORIES, check_certificates
from api.services.bidgen import task_bridge
from api.services.bidgen.service import (
    ALLOWED_UPLOAD_SUFFIXES, UNSUPPORTED_UPLOAD_MESSAGE, BidGenService, TaskNotFound,
)
from api.services.knowledge.access import require_tender_lib_read
from api.services.menus import can_access_menu
from common.config import get_settings
from common.response import ok
from common.errors import AppError

router = APIRouter(prefix="/bid-gen", tags=["bid-gen"])

CERTIFICATE_MAX_BYTES = 20 * 1024 * 1024
_MEDIA_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".zip": "application/zip",
    ".rar": "application/vnd.rar",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".pdf": "application/pdf",
}


class GenerateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    lot_codes: list[str] = Field(default_factory=list, alias="lotCodes")
    package_no: str = Field(default="", alias="packageNo", max_length=40)
    sign_date: str = Field(default="", alias="signDate", max_length=10)
    include_score_index: bool = Field(default=True, alias="includeScoreIndex")


class CertificateCheckRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(default="", alias="taskId")


class ConfirmVersionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    generated_at: str = Field(alias="generatedAt", max_length=64)
    content_digest: str = Field(default="", alias="contentDigest", max_length=128)


def _service(user) -> BidGenService:
    if not user_is_admin(user) and not can_access_menu(user, "/tenders"):
        raise HTTPException(403, "没有标书功能权限")
    return BidGenService(get_settings().storage_root, user.id)


async def _guarded(function, *args):
    """把服务层异常翻译成前端可读的 HTTP 错误。"""
    try:
        return await run_in_threadpool(function, *args)
    except TaskNotFound as exc:
        raise HTTPException(404, "任务或文件不存在") from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0]) if exc.args else "数据不存在") from exc
    except (ArchiveError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/tasks")
async def list_tasks(user: CurrentUser, db: DbSession):
    service = _service(user)
    await task_bridge.lock_owner(db, user.id)
    tasks = await _guarded(service.list_tasks)
    # 既有新版生成记录在打开生成页时补登记，不要求重新生成标书。
    for task in tasks:
        await task_bridge.lock_owner(db, user.id)
        row = await task_bridge.find_record(db, task["id"], lock=True)
        if row is None and task["status"] == "generated":
            detail = await _guarded(service.get_task, task["id"])
            await task_bridge.register_result(db, service, detail, user)
            row = await task_bridge.find_record(db, task["id"])
        task.update(task_bridge.with_workflow({}, row))
    return ok(tasks)


@router.post("/tasks")
async def create_task(user: CurrentUser, file: UploadFile = File(...)):
    """上传招标文件压缩包（支持压缩包套压缩包）并立即解析。"""
    service = _service(user)
    file_name = file.filename or "招标文件.zip"
    suffix = Path(file_name).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(422, UNSUPPORTED_UPLOAD_MESSAGE)
    limit = get_settings().kb_upload_max_bytes
    size = 0
    with NamedTemporaryFile(delete=False, suffix=suffix) as buffer:
        temporary = Path(buffer.name)
        try:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"文件超过 {limit // (1024 * 1024)}MB 上限，请拆分后上传")
                buffer.write(chunk)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
    if not size:
        temporary.unlink(missing_ok=True)
        raise HTTPException(422, "上传的文件为空")
    try:
        return ok(await _guarded(service.create_task, file_name, temporary))
    finally:
        temporary.unlink(missing_ok=True)


async def _save_upload(file: UploadFile, suffixes: tuple[str, ...], message: str) -> Path:
    """上传文件落盘到临时文件；调用方负责删除。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in suffixes:
        raise HTTPException(422, message)
    limit = get_settings().kb_upload_max_bytes
    size = 0
    with NamedTemporaryFile(delete=False, suffix=suffix) as buffer:
        temporary = Path(buffer.name)
        try:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"文件超过 {limit // (1024 * 1024)}MB 上限")
                buffer.write(chunk)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
    if not size:
        temporary.unlink(missing_ok=True)
        raise HTTPException(422, "上传的文件为空")
    return temporary


@router.get("/corpus")
async def corpus_summary(user: CurrentUser):
    """历史标书段落库：已导入的标书与章节数。"""
    return ok(_service(user).corpus.summary())


@router.post("/corpus")
async def corpus_import(user: CurrentUser, file: UploadFile = File(...)):
    """导入公司往期投标文件（Word / PDF），生成时按评分点复用同类章节。"""
    service = _service(user)
    temporary = await _save_upload(file, CORPUS_SUFFIXES, "历史标书仅支持 Word（.docx）或 PDF 文件")
    try:
        await _guarded(service.corpus.import_file, temporary, file.filename or "历史标书")
        return ok(service.corpus.summary())
    finally:
        temporary.unlink(missing_ok=True)


@router.delete("/corpus/{source_id}")
async def corpus_delete(source_id: str, user: CurrentUser):
    service = _service(user)
    await _guarded(service.corpus.delete, source_id)
    return ok(service.corpus.summary())


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, user: CurrentUser, db: DbSession):
    service = _service(user)
    await task_bridge.lock_owner(db, user.id)
    task = await _guarded(service.get_task, task_id)
    return ok(await task_bridge.register_result(db, service, task, user))


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, user: CurrentUser, db: DbSession):
    service = _service(user)
    await task_bridge.lock_owner(db, user.id)
    await _guarded(service.get_task, task_id)
    row = await task_bridge.find_record(db, task_id, lock=True)
    if row is not None:
        from api.services.tenders.records import delete_record
        await delete_record(db, row.public_id, user=user, admin=user_is_admin(user))
    else:
        await _guarded(service.delete_task, task_id)
    return ok({"id": task_id})


@router.post("/tasks/{task_id}/generate")
async def generate(task_id: str, body: GenerateRequest, user: CurrentUser, db: DbSession):
    """按当前企业资料生成投标文件，并同步完成评分自查与证件效期校验。"""
    selection = body.model_dump(by_alias=True)
    service = _service(user)
    await task_bridge.lock_owner(db, user.id)
    await _guarded(service.get_task, task_id)
    if not user_is_admin(user):
        await require_tender_lib_read(db, user)
    # 可从解析页直接生成；即使没有打开资料页，也应匹配最新资料库附件。
    await match_profile_from_library(service.profiles, db)
    row = await task_bridge.find_record(db, task_id, lock=True)
    task_bridge.assert_editable(row)
    task = await _guarded(service.generate, task_id, selection)
    try:
        return ok(await task_bridge.register_result(db, service, task, user, row=row))
    except AppError:
        raise
    except Exception as exc:
        await db.rollback()
        raise HTTPException(503, "文件已生成，但工作任务登记失败；请重新打开该任务重试登记") from exc


@router.post("/tasks/{task_id}/confirm")
async def confirm_task(task_id: str, body: ConfirmVersionRequest, user: CurrentUser, db: DbSession):
    await task_bridge.lock_owner(db, user.id)
    await _guarded(_service(user).get_task, task_id)
    row = await task_bridge.find_record(db, task_id, lock=True)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "工作任务尚未登记，请重新打开任务")
    task_bridge.assert_editable(row)
    source = task_bridge.source_of(row)
    if (body.generated_at != source.get("generatedAt")
            or body.content_digest != source.get("contentDigest", "")):
        raise HTTPException(409, "标书版本已更新，请重新打开任务，检查最新版本后再确认")
    task_bridge.confirm_version(row)
    await db.commit()
    from api.services.tenders.records import _record_to_dict
    return ok(_record_to_dict(row))


async def _review_record(db, user, record_id: str):
    row = await task_bridge.find_record(db, record_id)
    if row is None or not task_bridge.source_of(row):
        raise HTTPException(404, "新版任务不存在")
    if not user_is_admin(user) and not any(can_access_menu(user, menu) for menu in (
        "/tenders", "/tender-qa", "/work-tasks", "/tender-tasks", "/approval"
    )):
        raise HTTPException(403, "无权访问投标任务")
    if row.user_id != user.id and not user_is_admin(user) and not any(
        can_access_menu(user, menu) for menu in ("/tender-tasks", "/approval")
    ):
        raise HTTPException(403, "无权查看其他用户的投标文件")
    return row


@router.get("/records/{record_id}/review")
async def record_review(record_id: str, user: CurrentUser, db: DbSession):
    row = await _review_record(db, user, record_id)
    snapshot = await _guarded(task_bridge.read_snapshot, row)
    return ok(task_bridge.with_workflow(snapshot["task"], row))


@router.get("/records/{record_id}/files/{file_name}")
async def record_review_file(record_id: str, file_name: str, user: CurrentUser, db: DbSession):
    row = await _review_record(db, user, record_id)
    path = await _guarded(task_bridge.snapshot_file, row, file_name)
    return FileResponse(path, filename=file_name, media_type=_MEDIA_TYPES.get(path.suffix.lower()))


@router.get("/tasks/{task_id}/files/{file_name}")
async def download_file(task_id: str, file_name: str, user: CurrentUser):
    path = await _guarded(_service(user).output_file, task_id, file_name)
    return FileResponse(path, filename=path.name, media_type=_MEDIA_TYPES.get(path.suffix.lower()))


@router.get("/profile")
async def get_profile(user: CurrentUser, db: DbSession):
    service = _service(user)
    if not user_is_admin(user):
        await require_tender_lib_read(db, user)
    matched = await match_profile_from_library(service.profiles, db)
    categories = [{"key": key, "label": label, "required": required} for key, (label, required) in CERT_CATEGORIES.items()]
    return ok({**matched, "certificateCategories": categories})


@router.put("/profile")
async def save_profile(body: dict, user: CurrentUser):
    return ok({"profile": await _guarded(_service(user).profiles.save, body)})


@router.post("/profile/certificates/{certificate_id}/file")
async def upload_certificate_file(certificate_id: str, user: CurrentUser, file: UploadFile = File(...)):
    """上传证件扫描件；能识别出有效期时自动回填。"""
    content = await file.read(CERTIFICATE_MAX_BYTES + 1)
    await file.close()
    if not content:
        raise HTTPException(422, "上传的文件为空")
    if len(content) > CERTIFICATE_MAX_BYTES:
        raise HTTPException(413, "扫描件不能超过 20MB")
    return ok(await _guarded(_service(user).profiles.attach_file, certificate_id, file.filename or "scan.jpg", content))


@router.get("/profile/certificates/{certificate_id}/file")
async def view_certificate_file(certificate_id: str, user: CurrentUser):
    profiles = _service(user).profiles
    profile = await _guarded(profiles.load)
    certificate = next((c for c in profile["certificates"] if c["id"] == certificate_id), None)
    path = profiles.file_path(certificate) if certificate else None
    if path is None:
        raise HTTPException(404, "扫描件不存在")
    return FileResponse(path, filename=certificate["fileName"] or path.name, media_type=_MEDIA_TYPES.get(path.suffix.lower()))


@router.post("/profile/certificate-check")
async def certificate_check(body: CertificateCheckRequest, user: CurrentUser):
    """校验证件有效期；带任务时以该项目的投标截止日、投标有效期为基准。"""
    service = _service(user)
    deadline, validity_end = "", ""
    if body.task_id:
        task = await _guarded(service.get_task, body.task_id)
        deadline, validity_end = task["tender"]["bidDeadline"], task["tender"]["validityEnd"]
    profile = await _guarded(service.profiles.load)
    return ok(check_certificates(profile, deadline, validity_end, get_settings().expire_danger_days))
