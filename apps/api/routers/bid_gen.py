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
from api.services.bidgen.library import load_library_expiry, match_profile_from_library
from api.services.bidgen.profile import CERT_CATEGORIES, check_certificates
from api.services.bidgen.records import linked_record, record_link, register_generated, require_mutable
from api.services.bidgen.service import ALLOWED_UPLOAD_SUFFIXES, BidGenService, TaskNotFound
from api.services.knowledge.access import require_tender_lib_read
from api.services.menus import can_access_menu
from common.config import get_settings
from common.response import ok

router = APIRouter(prefix="/bid-gen", tags=["bid-gen"])

CERTIFICATE_MAX_BYTES = 20 * 1024 * 1024
_MEDIA_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".zip": "application/zip",
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


def _service(user) -> BidGenService:
    # 评分页需要读取生成任务的只读结果；拥有“AI标书评分”权限时也应可访问。
    if not user_is_admin(user) and not (
        can_access_menu(user, "/tenders") or can_access_menu(user, "/tender-qa")
    ):
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
async def list_tasks(user: CurrentUser):
    return ok(await _guarded(_service(user).list_tasks))


@router.post("/tasks")
async def create_task(user: CurrentUser, file: UploadFile = File(...)):
    """上传招标文件压缩包（支持压缩包套压缩包）并立即解析。"""
    service = _service(user)
    file_name = file.filename or "招标文件.zip"
    suffix = Path(file_name).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(422, "仅支持上传招标文件压缩包（.zip）或 Word 招标文件（.docx）")
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


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, user: CurrentUser, db: DbSession):
    task = await _guarded(_service(user).get_task, task_id)
    task["record"] = record_link(await linked_record(db, task_id, user.id))
    return ok(task)


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, user: CurrentUser, db: DbSession):
    row = await require_mutable(db, task_id, user.id)
    if row:
        from api.services.tenders.records import delete_record
        await delete_record(db, task_id, user=user, admin=False)
    await _guarded(_service(user).delete_task, task_id)
    return ok({"id": task_id})


@router.post("/tasks/{task_id}/generate")
async def generate(task_id: str, body: GenerateRequest, user: CurrentUser, db: DbSession):
    """按当前企业资料生成投标文件，并同步完成评分自查与证件效期校验。"""
    service = _service(user)
    await _guarded(service.get_task, task_id)
    row = await require_mutable(db, task_id, user.id)
    if not user_is_admin(user):
        await require_tender_lib_read(db, user)
    await match_profile_from_library(service.profiles, db)
    selection = body.model_dump(by_alias=True)
    task = await _guarded(service.generate, task_id, selection)
    row = await register_generated(db, service, task, user, row=row)
    task["record"] = record_link(row)
    return ok(task)


@router.post("/sync-records")
async def sync_records(user: CurrentUser, db: DbSession):
    service = _service(user)
    synced, errors = [], []
    for summary in await _guarded(service.list_tasks):
        if summary["status"] != "generated":
            continue
        try:
            task = await _guarded(service.get_task, summary["id"])
            row = await register_generated(db, service, task, user)
            synced.append(row.public_id)
        except Exception:
            await db.rollback()
            errors.append({"id": summary["id"], "name": summary["projectName"] or summary["archiveName"],
                           "message": "登记失败，请检查原始招标文件、生成文件是否完整后重试"})
    return ok({"synced": synced, "errors": errors})


@router.get("/library/expiry")
async def library_expiry(user: CurrentUser, db: DbSession):
    _service(user)
    if not user_is_admin(user):
        await require_tender_lib_read(db, user)
    settings = get_settings()
    return ok({"items": await load_library_expiry(db),
               "warnDays": max(settings.expire_warn_days, settings.expire_danger_days)})


@router.get("/tasks/{task_id}/files/{file_name}")
async def download_file(task_id: str, file_name: str, user: CurrentUser):
    path = await _guarded(_service(user).output_file, task_id, file_name)
    return FileResponse(path, filename=path.name, media_type=_MEDIA_TYPES.get(path.suffix.lower()))


@router.get("/tasks/{task_id}/score-check.xlsx")
async def download_score_check(task_id: str, user: CurrentUser):
    """下载当前任务的评分标准自查明细 Excel。"""
    path = await _guarded(_service(user).score_check_file, task_id)
    return FileResponse(path, filename=path.name, media_type=_MEDIA_TYPES[".xlsx"])


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
    return ok(check_certificates(profile, deadline, validity_end))
