"""Resumable ordinary technical bids, backed by the existing tender services."""

from __future__ import annotations

import asyncio
import logging
from contextlib import ExitStack, nullcontext
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StringConstraints

from api.deps import CurrentUser, DbSession, user_is_admin
from api.services.menus import can_access_menu
from api.services.tenders import workbench as store
from api.services.tenders.schema import BidBrief
from common.errors import AppError, ErrorCode
from common.response import ok

logger = logging.getLogger(__name__)
WORD = {".doc", ".docx"}
QUOTES = {".xlsx", ".xlsm", ".docx", ".csv"}
SCANS = {".pdf", ".png", ".jpg", ".jpeg"}
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=256)]


def allow(user: CurrentUser) -> None:
    if not (user_is_admin(user) or can_access_menu(user, "/tenders")):
        raise AppError(ErrorCode.FORBIDDEN, "无权限访问投标工作台", status_code=403)


router = APIRouter(
    prefix="/tenders/workbench",
    tags=["tender workbench"],
    dependencies=[Depends(allow)],
)


class Revision(BaseModel):
    revision: int = Field(ge=1)


class ProjectFields(Revision):
    projectName: ShortText | None = None
    tenderer: ShortText | None = None
    bidderName: ShortText | None = None
    tenderNo: ShortText | None = None
    bidderCompanyId: ShortText | None = None
    legalPersonKey: ShortText | None = None


class ChapterText(Revision):
    text: str = Field(max_length=100_000)


def upload_name(upload: UploadFile) -> str:
    return Path((upload.filename or "").replace("\\", "/")).name


async def save_upload(upload: UploadFile, folder: Path, allowed: set[str], help_text: str) -> dict:
    name = upload_name(upload)
    extension = Path(name).suffix.lower()
    if not name or len(name) > 240 or extension not in allowed:
        raise store.invalid(f"《{name or '未命名文件'}》格式或文件名不受支持。{help_text}")
    folder.mkdir(parents=True, exist_ok=True)
    key = uuid4().hex + extension
    path = folder / key
    max_mb = 200 if allowed == WORD else 40
    size = 0
    try:
        with path.open("wb") as dest:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > max_mb * 1024 * 1024:
                    raise store.invalid(f"《{name}》超过 {max_mb} MB，请压缩后重新上传")
                dest.write(chunk)
        if not size:
            raise store.invalid(f"《{name}》内容为空，请选择有内容的文件")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {"id": path.stem, "name": name, "file": key}


async def upload_sources(files: list[UploadFile], folder: Path) -> list[dict]:
    if not 1 <= len(files) <= 5:
        raise store.invalid("请上传 1–5 份 Word 招标文件")
    sources = []
    try:
        for file in files:
            sources.append(
                await save_upload(file, folder, WORD, "请提供可检索的 .doc 或 .docx 招标文件")
            )
    except BaseException:
        for source in sources:
            (folder / source["file"]).unlink(missing_ok=True)
        raise
    return sources


@router.get("")
async def projects(user: CurrentUser):
    return ok(store.list_projects(int(user.id)))


@router.post("")
async def create(user: CurrentUser, files: Annotated[list[UploadFile], File()]):
    owner, project_id = int(user.id), uuid4().hex
    folder = store.files_dir(owner, project_id)
    sources = await upload_sources(files, folder)
    try:
        created = store.create(owner, project_id, sources)
    except Exception:
        for source in sources:
            (folder / source["file"]).unlink(missing_ok=True)
        raise
    return ok(store.public(created))


@router.get("/{project_id}")
async def project(project_id: str, user: CurrentUser):
    return ok(store.public(store.get(int(user.id), project_id)))


@router.put("/{project_id}/sources")
async def replace_sources(
    project_id: str,
    user: CurrentUser,
    revision: Annotated[int, Form(ge=1)],
    files: Annotated[list[UploadFile], File()],
):
    owner = int(user.id)
    current = store.get(owner, project_id, revision)
    if current["brief"]:
        raise store.invalid("该项目已经识别。请用新项目处理另一套招标文件")
    folder = store.files_dir(owner, project_id)
    sources = await upload_sources(files, folder)
    current.update(sources=sources, inputIssue="", notes=[])
    try:
        saved = store.save(owner, current)
    except Exception:
        for source in sources:
            (folder / source["file"]).unlink(missing_ok=True)
        raise
    return ok(store.public(saved))


@router.post("/{project_id}/identify")
async def identify(project_id: str, body: Revision, db: DbSession, user: CurrentUser):
    from api.services.tenders.extract import parse_invitation

    owner = int(user.id)
    current = store.get(owner, project_id, body.revision)
    if current["brief"]:
        raise store.invalid("该项目已识别，请直接修改项目字段或补资料")
    folder = store.files_dir(owner, project_id)
    names = "、".join(source["name"] for source in current["sources"])
    try:
        with ExitStack() as stack:
            uploads = [
                UploadFile(
                    stack.enter_context((folder / source["file"]).open("rb")),
                    filename=source["name"],
                )
                for source in current["sources"]
            ]
            parsed = await asyncio.wait_for(
                parse_invitation(
                    db,
                    uploads if len(uploads) > 1 else uploads[0],
                    current=BidBrief(generateVolume="technical"),
                    volume_locked=True,
                    created_by=owner,
                    persist_library=False,
                ),
                timeout=180,
            )
    except (AppError, OSError, TimeoutError) as exc:
        reason = (
            exc.msg
            if isinstance(exc, AppError)
            else "识别超时"
            if isinstance(exc, TimeoutError)
            else "读取失败"
        )
        current["inputIssue"] = f"《{names}》：{reason}。请重试或替换为可检索的 Word 文件"
    else:
        current["notes"] = list(parsed.get("notes") or [])
        if parsed.get("ocrCapped"):
            current["inputIssue"] = (
                f"《{names}》超过 OCR 识别页数，部分内容未读取。"
                "请拆分或提供可检索的 Word 文件后重试"
            )
        else:
            brief = BidBrief.model_validate(parsed["brief"])
            current["brief"] = store.initialize_brief(brief)
            current["allowedSlots"] = store.attachment_slots(
                brief, parsed.get("attachmentMatch") or {}
            )
            current["legalPersons"] = await store.legal_person_choices(db)
            current["inputIssue"] = ""
    return ok(store.public(store.save(owner, current)))


@router.patch("/{project_id}/fields")
async def save_fields(project_id: str, body: ProjectFields, db: DbSession, user: CurrentUser):
    from api.services.tenders.classic_company import attachment_preflight, authorize_company_bidder
    from api.services.tenders.library_kb import legal_persons_from_library
    from api.services.tenders.library_scope import use_company_base

    owner = int(user.id)
    current = store.get(owner, project_id, body.revision)
    brief = store.brief_for(current)
    updates = body.model_dump(exclude={"revision"}, exclude_none=True)
    new_company = updates.get("bidderCompanyId", brief.bidderCompanyId)
    if brief.bidderCompanyId and new_company != brief.bidderCompanyId:
        raise store.invalid("本项目已绑定投标公司，其他投标主体请另建项目")
    if current["attachments"] and (
        new_company != brief.bidderCompanyId
        or updates.get("bidderName", brief.bidderName) != brief.bidderName
    ):
        raise store.invalid("本项目已上传企业资料，请在原投标主体下继续，其他主体请另建项目")
    candidate = brief.model_copy(update=updates)
    base = (
        await authorize_company_bidder(db, user, candidate) if candidate.bidderCompanyId else None
    )
    scope_changed = (candidate.bidderCompanyId, candidate.bidderName) != (
        brief.bidderCompanyId,
        brief.bidderName,
    )
    with use_company_base(base) if base is not None else nullcontext():
        if scope_changed:
            report = await attachment_preflight(db, candidate)
            current["legalPersons"] = await store.legal_person_choices(db)
            current["allowedSlots"] = store.attachment_slots(candidate, report["attachmentMatch"])
            candidate.legalPersonKey = ""
        if scope_changed or candidate.legalPersonKey != brief.legalPersonKey:
            person = {}
            if candidate.legalPersonKey:
                people = await legal_persons_from_library(db, extract_missing=False)
                person = next(
                    (item for item in people if item.get("key") == candidate.legalPersonKey), {}
                )
                if not person:
                    raise store.invalid("法定代表人已不在本项目的资料范围，请重新选择")
            for field, source in (
                ("legalPersonName", "name"),
                ("legalPersonGender", "gender"),
                ("legalPersonAge", "age"),
                ("legalPersonIdNo", "idNo"),
                ("legalPersonTitle", "title"),
            ):
                setattr(candidate, field, str(person.get(source) or ""))
    snapshot = candidate.model_dump(mode="json")
    if snapshot == current["brief"]:
        return ok(store.public(current))
    current["brief"] = snapshot
    return ok(store.public(store.save(owner, current, changed=True)))


@router.put("/{project_id}/quote")
async def update_quote(
    project_id: str,
    user: CurrentUser,
    revision: Annotated[int, Form(ge=1)],
    file: UploadFile,
):
    from api.services.tenders.quote import attach_sheet, parse_quote_path, quote_source_notes

    owner = int(user.id)
    current = store.get(owner, project_id, revision)
    brief = store.brief_for(current)
    folder = store.files_dir(owner, project_id)
    uploaded = await save_upload(
        file, folder, QUOTES, "请使用 .xlsx、.xlsm、.docx 或 .csv 报价清单"
    )
    path = folder / uploaded["file"]
    try:
        try:
            sheet = await asyncio.to_thread(parse_quote_path, path)
        except Exception as exc:
            raise store.invalid(
                f"《{uploaded['name']}》无法读取。请检查清单表头并重新上传"
            ) from exc
        if sheet is None or not sheet.lines:
            raise store.invalid(
                f"《{uploaded['name']}》没有可用报价行。请提供名称、数量等结构化表头"
            )
        if brief.quoteAutoPrice and brief.bidPriceYuan == brief.quoteSourceIncTax:
            brief.bidPriceYuan = 0
            brief.quoteAutoPrice = False
        candidate = attach_sheet(brief, sheet, source="file", source_name=uploaded["name"])
        if candidate.quoteMatchStatus != "matched":
            raise store.invalid(
                f"《{uploaded['name']}》无法匹配本项目：{candidate.quoteMatchNote}。请核对文件"
            )
        current.update(
            brief=candidate.model_dump(mode="json"),
            quote=uploaded,
            quoteNotes=quote_source_notes(candidate),
        )
        updated = store.save(owner, current, changed=True)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return ok(store.public(updated))


@router.put("/{project_id}/attachments/{slot_key}")
async def attach(
    project_id: str,
    slot_key: str,
    user: CurrentUser,
    revision: Annotated[int, Form(ge=1)],
    file: UploadFile,
):
    from api.services.tenders.material_store import scan_usable

    owner = int(user.id)
    current = store.get(owner, project_id, revision)
    brief = store.brief_for(current)
    if not brief.bidderName.strip():
        raise store.invalid("请先确认投标公司，再补充该公司的技术资料")
    if not any(item["key"] == slot_key for item in current["allowedSlots"]):
        raise store.missing()
    if brief.techAnonymous:
        raise store.invalid("本项目为技术暗标，扫描件需经过项目人工核验后单独处理")
    folder = store.files_dir(owner, project_id)
    uploaded = await save_upload(file, folder, SCANS, "请提供可打开的 PDF、PNG 或 JPG 扫描件")
    path = folder / uploaded["file"]
    try:
        if not await asyncio.to_thread(scan_usable, path):
            raise store.invalid(f"《{uploaded['name']}》无法读取或已加密，请重新上传")
        current["attachments"][slot_key] = uploaded
        updated = store.save(owner, current, changed=True)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return ok(store.public(updated))


@router.put("/{project_id}/chapters/{chapter_id}")
async def save_chapter(project_id: str, chapter_id: str, body: ChapterText, user: CurrentUser):
    owner = int(user.id)
    current = store.get(owner, project_id, body.revision)
    brief = store.brief_for(current)
    chapter = store.chapter_for(brief, chapter_id)
    text = body.text.strip()
    changed = chapter.draftBody != text
    if changed:
        chapter.draftBody = text
        chapter.draftStatus = "manual" if text else "empty"
        current["brief"] = brief.model_dump(mode="json")
    current["lastChapterId"] = chapter_id
    return ok(store.public(store.save(owner, current, changed=changed)))


@router.post("/{project_id}/chapters/{chapter_id}/suggestion")
async def suggest_chapter(
    project_id: str, chapter_id: str, body: Revision, db: DbSession, user: CurrentUser
):
    from api.services.models.runtime import build_llm_client
    from api.services.tenders.classic_company import authorize_company_bidder
    from api.services.tenders.library_scope import use_company_base
    from api.services.tenders.tech_draft import draft_tech

    current = store.get(int(user.id), project_id, body.revision)
    brief = store.brief_for(current)
    store.chapter_for(brief, chapter_id)
    try:
        client = await build_llm_client(db, "fast")
    except Exception:
        logger.warning("Workbench chapter model unavailable", exc_info=True)
        client = None
    if brief.bidderCompanyId:
        base = await authorize_company_bidder(db, user, brief)
        with use_company_base(base):
            drafted, notes = await draft_tech(brief, db=db, item_id=chapter_id, client=client)
    else:
        drafted, notes = await draft_tech(brief, db=db, item_id=chapter_id, client=client)
    chapter = store.chapter_for(drafted, chapter_id)
    return ok({"text": chapter.draftBody, "notes": notes, "revision": body.revision})


@router.post("/{project_id}/format")
async def prepare_format(project_id: str, body: Revision, user: CurrentUser):
    from api.services.tenders.format_preview import prepare_invitation_format_docx

    owner = int(user.id)
    current = store.get(owner, project_id, body.revision)
    brief = store.brief_for(current)
    preview = await asyncio.to_thread(
        prepare_invitation_format_docx, brief.invitationId, brief=brief
    )
    current["format"] = {
        "file": preview["fileName"],
        "message": preview["message"],
        "source": preview["source"],
    }
    return ok(store.public(store.save(owner, current)))


@router.post("/{project_id}/format/confirm")
async def confirm_format(project_id: str, body: Revision, user: CurrentUser):
    owner = int(user.id)
    current = store.get(owner, project_id, body.revision)
    brief = store.brief_for(current)
    if not current["format"]:
        raise store.invalid("请先准备并查看本项目的格式预览")
    if brief.formatConfirmed:
        return ok(store.public(current))
    brief.formatConfirmed = True
    current["brief"] = brief.model_dump(mode="json")
    return ok(store.public(store.save(owner, current, changed=True)))


@router.post("/{project_id}/generate")
async def generate(project_id: str, body: Revision, db: DbSession, user: CurrentUser):
    from api.services.tenders.assets import load_invitation_text
    from api.services.tenders.classic_company import authorize_company_bidder
    from api.services.tenders.library_scope import use_company_base
    from api.services.tenders.records import _prepare_and_generate

    owner = int(user.id)
    current = store.get(owner, project_id, body.revision)
    brief = store.brief_for(current)
    if not load_invitation_text(brief.invitationId):
        names = "、".join(item["name"] for item in current["sources"])
        raise store.invalid(f"《{names}》的解析文件已缺失，请用原件重新建标")
    folder = store.files_dir(owner, project_id)
    media = {key: [folder / item["file"]] for key, item in current["attachments"].items()}
    for key, files in media.items():
        if not files[0].is_file():
            name = current["attachments"][key]["name"]
            raise store.invalid(f"《{name}》已缺失，请在本项目的资料待办里重新上传")
    brief.includeSlotKeys = list(dict.fromkeys([*brief.includeSlotKeys, *media]))
    options = {"workbench_attachments": media, "saved_chapters_only": True}
    if brief.bidderCompanyId:
        base = await authorize_company_bidder(db, user, brief)
        with use_company_base(base):
            generated, _ = await _prepare_and_generate(db, brief, **options)
    else:
        generated, _ = await _prepare_and_generate(db, brief, **options)
    if not generated.get("techDocxFile"):
        raise store.invalid("技术标 Word 草稿生成失败，请核对项目及章节后重试")
    current["output"] = {
        "file": generated["techDocxFile"],
        "name": generated["techDownloadName"],
        "warnings": generated.get("warnings") or [],
        "stale": False,
    }
    return ok(store.public(store.save(owner, current)))


@router.get("/{project_id}/files/{kind}")
async def download(project_id: str, kind: Literal["format", "output"], user: CurrentUser):
    from api.services.tenders.generate import resolve_output_file

    current = store.get(int(user.id), project_id)
    entry = current[kind]
    if not entry:
        raise store.missing()
    return FileResponse(
        resolve_output_file(entry["file"]),
        media_type=DOCX_MIME,
        filename=entry.get("name") or "技术标格式预览.docx",
    )
