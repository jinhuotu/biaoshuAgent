"""Company-owned tender material libraries backed by the existing knowledge ACL."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload

from api.services.knowledge import access
from api.services.knowledge.bases import PURPOSE_ASSET, get_base_by_public_id, short_id
from api.services.knowledge.ingest import (
    _extracted_text_key,
    require_embedding_ready,
    resolve_storage_path,
    unlink_stored_file,
)
from api.services.knowledge.parsers import extract_text_from_file
from api.services.knowledge.queue import notify_ingest_worker, stage_ingest_task
from api.services.menus import user_is_admin
from api.services.tenders import library_kb as library
from api.services.tenders.facts import apply_correction
from api.services.tenders.material_identity import standard_material_key
from api.services.tenders.schema import PlaceholderItem
from common.errors import AppError, ErrorCode
from db.models.knowledge import KnowledgeBase, KnowledgeDocument, KnowledgeIngestTask
from db.models.tender_library import TenderCompany, TenderLibraryDismissal
from db.models.user import User

logger = logging.getLogger(__name__)
DEFAULTS = {
    item.key: item for item in library.DEFAULT_SLOTS if item.key != library.TECH_DRAWING_KEY
}
MAX_FILE_BYTES = library._MAX_BYTES
MAX_FILES = library._MAX_FILES_PER_SLOT
MAX_BATCH_BYTES = 2 * MAX_FILE_BYTES


def invalid(message: str) -> AppError:
    return AppError(ErrorCode.VALIDATION, message, status_code=422)


def not_found(message: str) -> AppError:
    return AppError(ErrorCode.NOT_FOUND, message, status_code=404)


def _company_rows():
    return (
        select(TenderCompany, KnowledgeBase)
        .join(KnowledgeBase, TenderCompany.base_id == KnowledgeBase.id)
        .options(lazyload("*"))
    )


def _company_item(company, base, flags):
    return access.attach_perms(
        {
            "id": company.public_id,
            "name": company.name,
            "creditCode": company.credit_code,
            "aliases": company.aliases,
            "active": base.status == "active",
            "baseId": base.public_id,
        },
        flags,
    )


async def list_companies(db: AsyncSession, user: User) -> dict:
    admin = user_is_admin(user)
    stmt = _company_rows().order_by(TenderCompany.name, TenderCompany.credit_code)
    if not admin:
        stmt = stmt.where(KnowledgeBase.status == "active")
    rows = (await db.execute(stmt)).all()
    perms = await access.perms_for_bases(db, user, [base for _, base in rows])
    items = [
        _company_item(company, base, perms[base.id])
        for company, base in rows
        if access.PERM_VIEW in perms[base.id]
    ]
    legacy = admin
    if not legacy:
        try:
            base = await get_base_by_public_id(db, access.TENDER_LIB_PUBLIC_ID)
        except AppError as exc:
            if exc.status_code != 404:
                raise
        else:
            legacy = access.PERM_MANAGE in await access.perms_for_base(db, user, base)
    return {"items": items, "canCreate": admin, "legacyAvailable": legacy}


async def has_company_access(db: AsyncSession, user: User) -> bool:
    bases = (
        await db.scalars(
            select(KnowledgeBase)
            .join(TenderCompany, TenderCompany.base_id == KnowledgeBase.id)
            .where(KnowledgeBase.status == "active")
            .options(lazyload("*"))
        )
    ).all()
    perms = await access.perms_for_bases(db, user, list(bases))
    return any(access.PERM_VIEW in flags for flags in perms.values())


async def require_company(
    db: AsyncSession, user: User, company_id: str, perm: access.Perm, *, lock: bool = False
) -> tuple[TenderCompany, KnowledgeBase]:
    stmt = _company_rows().where(TenderCompany.public_id == company_id)
    if lock:
        stmt = stmt.with_for_update()
    row = (await db.execute(stmt)).one_or_none()
    if row is None or row[1].status != "active":
        raise not_found("投标公司不存在或已停用")
    company, base = row
    flags = await access.perms_for_base(db, user, base)
    if access.PERM_VIEW not in flags:
        raise not_found("投标公司不存在或已停用")
    if perm not in flags:
        raise AppError(ErrorCode.FORBIDDEN, "无权维护该公司的投标资料", status_code=403)
    return company, base


async def save_company(
    db: AsyncSession,
    *,
    name: str,
    credit_code: str,
    aliases: list[str],
    active: bool,
    company_id: str | None = None,
) -> dict:
    name = name.strip()
    credit_code = credit_code.strip().upper()
    if not name:
        raise invalid("请填写公司法定全称")
    if not re.fullmatch(r"[0-9A-HJ-NPQRTUWXY]{18}", credit_code):
        raise invalid("统一社会信用代码应为 18 位有效字符")
    clean_aliases = list(
        dict.fromkeys(alias.strip() for alias in aliases if alias.strip() and alias.strip() != name)
    )
    try:
        if company_id is None:
            base = KnowledgeBase(
                public_id=access.COMPANY_TENDER_LIB_PREFIX + short_id(16),
                name=f"{name} · 投标资料库",
                purpose=PURPOSE_ASSET,
                status="active" if active else "inactive",
            )
            db.add(base)
            await db.flush()
            company = TenderCompany(public_id=short_id(16), base_id=base.id)
            db.add(company)
        else:
            stmt = _company_rows().where(TenderCompany.public_id == company_id).with_for_update()
            row = (await db.execute(stmt)).one_or_none()
            if row is None:
                raise not_found("投标公司不存在")
            company, base = row
            base.name = f"{name} · 投标资料库"
            base.status = "active" if active else "inactive"
        company.name = name
        company.credit_code = credit_code
        company.aliases = clean_aliases
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if await db.scalar(
            select(TenderCompany.public_id).where(TenderCompany.credit_code == credit_code)
        ):
            raise AppError(ErrorCode.CONFLICT, "统一社会信用代码已存在", status_code=409) from exc
        raise
    return _company_item(company, base, set(access.ALL_PERMS))


def _virtual_slot(item: PlaceholderItem) -> dict:
    return {
        "key": item.key,
        "title": item.title,
        "hint": item.hint,
        "docId": "",
        "files": [],
        "fileCount": 0,
        "pinned": True,
    }


def _slot_view(base, parent, children) -> dict:
    return library.item_to_slot(parent, children, base_public_id=base.public_id)


async def _lock_documents(db: AsyncSession, base: KnowledgeBase) -> list[KnowledgeDocument]:
    rows = await db.scalars(
        select(KnowledgeDocument).where(KnowledgeDocument.base_id == base.id).with_for_update()
    )
    return list(rows.all())


async def library_payload(db: AsyncSession, base: KnowledgeBase) -> dict:
    docs = await library._list_docs(db, base.id)
    parents = {
        library.key_from_tags(doc.tags): doc for doc in docs if library._is_material_parent(doc)
    }
    children = library._group_children(docs)
    dismissed = set(
        (
            await db.scalars(
                select(TenderLibraryDismissal.key).where(
                    TenderLibraryDismissal.base_public_id == base.public_id
                )
            )
        ).all()
    )
    slots = []
    for key, spec in DEFAULTS.items():
        parent = parents.pop(key, None)
        if parent is not None:
            slots.append(_slot_view(base, parent, children.get(parent.public_id, [])))
        elif key not in dismissed:
            slots.append(_virtual_slot(spec))
    for parent in sorted(parents.values(), key=lambda doc: (doc.name, doc.public_id)):
        slots.append(_slot_view(base, parent, children.get(parent.public_id, [])))
    return {
        "slots": slots,
        "filledCount": sum(bool(slot["fileCount"]) for slot in slots),
        "totalCount": len(slots),
        "baseId": base.public_id,
        "hint": "原件归档到当前公司的投标资料库，OCR 完成后可在 AI 智能问答中选择该库检索。",
    }


def _find_parent(docs: Sequence[KnowledgeDocument], key: str) -> KnowledgeDocument | None:
    return next(
        (
            doc
            for doc in docs
            if library._is_material_parent(doc) and library.key_from_tags(doc.tags) == key
        ),
        None,
    )


def _require_parent(docs, key):
    parent = _find_parent(docs, key)
    if parent is None:
        raise not_found("资料项不存在")
    return parent


def _validate_key(key: str) -> None:
    if not library._KEY_RE.fullmatch(key) or key == library.TECH_DRAWING_KEY:
        raise invalid("无效的资料项编号")


async def create_item(
    db: AsyncSession, base: KnowledgeBase, user: User, *, key: str, title: str, hint: str
) -> None:
    title = title.strip()
    if not title:
        raise invalid("请填写资料名称")
    if key:
        _validate_key(key)
    key = standard_material_key(PlaceholderItem(key=key, title=title)) or key or "m" + short_id(16)
    _validate_key(key)
    docs = await _lock_documents(db, base)
    if _find_parent(docs, key) or any(
        library._is_material_parent(doc) and doc.name == title for doc in docs
    ):
        raise AppError(ErrorCode.CONFLICT, "该公司已维护此资料项", status_code=409)
    parent = library._new_parent(
        base_id=base.id, public_id=short_id(), key=key, title=title, hint=hint
    )
    parent.created_by = user.id
    db.add(parent)
    dismissed = await db.get(TenderLibraryDismissal, (base.public_id, key), with_for_update=True)
    if dismissed:
        await db.delete(dismissed)
    await db.commit()


async def update_item(
    db: AsyncSession, base: KnowledgeBase, key: str, *, title: str, hint: str
) -> None:
    clean_title = title.strip()
    if not clean_title:
        raise invalid("请填写资料名称")
    parent = _require_parent(await _lock_documents(db, base), key)
    parent.name = clean_title
    parent.summary = hint.strip()[:200] or clean_title[:200]
    await db.commit()


def _cleanup_files(base: KnowledgeBase, docs: Sequence[KnowledgeDocument]) -> None:
    for doc in docs:
        library._purge_doc_vectors(doc)
        try:
            unlink_stored_file(doc.file_key or doc.storage_path)
            unlink_stored_file(_extracted_text_key(base.public_id, doc.public_id))
        except OSError:
            logger.exception("Failed to clean up company material %s", doc.public_id)


async def delete_item(db: AsyncSession, base: KnowledgeBase, key: str, *, clear: bool) -> None:
    docs = await _lock_documents(db, base)
    parent = _find_parent(docs, key)
    if parent is None and (clear or key not in DEFAULTS):
        raise not_found("资料项不存在")
    removed = []
    if parent is not None:
        removed = library._group_children(docs).get(parent.public_id, [])
        for child in removed:
            await db.delete(child)
        if not clear:
            await db.delete(parent)
            removed = [*removed, parent]
    if not clear and key in DEFAULTS:
        if not await db.get(TenderLibraryDismissal, (base.public_id, key), with_for_update=True):
            db.add(TenderLibraryDismissal(base_public_id=base.public_id, key=key))
    await db.commit()
    _cleanup_files(base, removed)


def _find_file(docs, doc_id):
    doc = next(
        (
            doc
            for doc in docs
            if doc.public_id == doc_id and doc.parent_id and library._doc_file_path(doc)
        ),
        None,
    )
    if doc is None:
        raise not_found("扫描件不存在")
    return doc


async def delete_file(db: AsyncSession, base: KnowledgeBase, doc_id: str) -> None:
    doc = _find_file(await _lock_documents(db, base), doc_id)
    await db.delete(doc)
    await db.commit()
    _cleanup_files(base, [doc])


async def update_facts(
    db: AsyncSession, base: KnowledgeBase, doc_id: str, *, role: str, fields: list
) -> None:
    doc = _find_file(await _lock_documents(db, base), doc_id)
    try:
        doc.summary = apply_correction(doc.summary, role=role, fields=fields)
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    await db.commit()


async def upload_files(
    db: AsyncSession,
    company: TenderCompany,
    base: KnowledgeBase,
    user: User,
    key: str,
    files: list[tuple[str, bytes]],
    *,
    replace: bool,
) -> None:
    _validate_key(key)
    docs = await _lock_documents(db, base)
    parent = _find_parent(docs, key)
    if parent is None:
        default = DEFAULTS.get(key)
        if default is None or await db.get(
            TenderLibraryDismissal, (base.public_id, key), with_for_update=True
        ):
            raise not_found("资料项不存在")
        parent = library._new_parent(
            base_id=base.id,
            public_id=short_id(),
            key=key,
            title=default.title,
            hint=default.hint,
        )
        parent.created_by = user.id
        db.add(parent)
    children = library._group_children(docs).get(parent.public_id, [])
    original_children = children if replace else []
    retained = [] if replace else children
    hashes = {child.content_hash for child in retained}
    incoming = []
    for filename, data in files:
        name = Path(filename.replace("\\", "/")).name
        ext = Path(name).suffix.lower()
        if ext not in library._ALLOWED_EXT or not data or len(data) > MAX_FILE_BYTES:
            raise invalid(f"{name or '文件'}为空、超过 40MB 或格式不受支持")
        digest = hashlib.sha256(data).hexdigest()
        if digest not in hashes:
            incoming.append((name, ext, data, digest))
            hashes.add(digest)
    if not files or len(retained) + len(incoming) > MAX_FILES:
        raise invalid(f"请上传文件，每项最多 {MAX_FILES} 个")
    if not incoming:
        return

    await require_embedding_ready(db)
    created_paths: list[Path] = []
    try:
        new_documents = []
        for name, ext, data, digest in incoming:
            doc_id = short_id()
            storage_key = f"tender-assets/companies/{company.public_id}/{key}/{doc_id}{ext}"
            path = resolve_storage_path(storage_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                created_paths.append(path)
                stream.write(data)
            if ext in {".doc", ".docx"}:
                await extract_text_from_file(path, ext=ext[1:], ocr_budget=0)
            doc = KnowledgeDocument(
                public_id=doc_id,
                base_id=base.id,
                parent_id=parent.public_id,
                name=Path(name).stem[:255] or doc_id,
                source="file",
                kind="doc",
                file_type=ext[1:],
                storage_path=storage_key,
                file_key=storage_key,
                size=len(data),
                content_hash=digest,
                summary="正在解析…",
                status="parsing",
                review_status="approved",
                created_by=user.id,
                tags=[
                    *library._library_file_tags(parent),
                    f"bidder:{company.name}",
                    f"original-name:{name}",
                    f"material-key:{key}",
                    f"material-title:{parent.name}",
                ],
            )
            db.add(doc)
            new_documents.append(doc)
        for child in original_children:
            await db.delete(child)
        await db.flush()
        for doc in new_documents:
            stage_ingest_task(db, base=base, doc=doc)
        await db.commit()
    except (Exception, asyncio.CancelledError):
        await db.rollback()
        for path in created_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to discard incomplete company material %s", path.name)
        raise
    notify_ingest_worker()
    _cleanup_files(base, original_children)


async def reindex(db: AsyncSession, base: KnowledgeBase, *, force: bool) -> dict:
    busy = set(
        (
            await db.scalars(
                select(KnowledgeIngestTask.document_id)
                .where(
                    KnowledgeIngestTask.base_id == base.id,
                    KnowledgeIngestTask.status.in_(("queued", "running")),
                )
                .with_for_update()
            )
        ).all()
    )
    queued = skipped = 0
    for doc in await _lock_documents(db, base):
        if doc.review_status != "approved" or library._doc_file_path(doc) is None:
            continue
        if doc.id in busy:
            skipped += 1
            continue
        if not force and doc.status == "ready" and doc.chunk_count:
            continue
        if not queued:
            await require_embedding_ready(db)
        doc.status = "parsing"
        doc.error_msg = None
        doc.chunk_count = 0
        stage_ingest_task(db, base=base, doc=doc, force_reextract=True)
        queued += 1
    if queued:
        await db.commit()
        notify_ingest_worker()
    return {"queued": queued, "skipped": skipped}
