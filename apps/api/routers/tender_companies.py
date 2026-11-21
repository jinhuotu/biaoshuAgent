"""Company directory and company-scoped tender material endpoints."""

from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field, StringConstraints

from api.deps import AdminUser, CurrentUser, DbSession
from api.services.knowledge.access import PERM_MANAGE, PERM_VIEW
from api.services.tenders import company_library as service
from common.response import ok

router = APIRouter(prefix="/companies")


class CompanyIn(BaseModel):
    name: str = Field(min_length=1, max_length=110)
    creditCode: str = Field(min_length=1, max_length=32)
    aliases: list[Annotated[str, StringConstraints(max_length=110)]] = Field(
        default_factory=list, max_length=30
    )
    active: bool = True


class ItemIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    hint: str = Field(default="", max_length=2000)
    key: str = Field(default="", max_length=64)


class FactField(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    value: str = ""


class FactsIn(BaseModel):
    role: str = ""
    fields: list[FactField] = Field(default_factory=list)


@router.get("")
async def list_companies(db: DbSession, user: CurrentUser):
    return ok(await service.list_companies(db, user))


@router.post("")
async def create_company(body: CompanyIn, db: DbSession, user: AdminUser):
    return ok(
        await service.save_company(
            db,
            name=body.name,
            credit_code=body.creditCode,
            aliases=body.aliases,
            active=body.active,
        )
    )


@router.put("/{company_id}")
async def update_company(company_id: str, body: CompanyIn, db: DbSession, user: AdminUser):
    return ok(
        await service.save_company(
            db,
            company_id=company_id,
            name=body.name,
            credit_code=body.creditCode,
            aliases=body.aliases,
            active=body.active,
        )
    )


@router.get("/{company_id}/library")
async def get_library(company_id: str, db: DbSession, user: CurrentUser):
    _, base = await service.require_company(db, user, company_id, PERM_VIEW)
    return ok(await service.library_payload(db, base))


@router.post("/{company_id}/library/items")
async def create_item(company_id: str, body: ItemIn, db: DbSession, user: CurrentUser):
    _, base = await service.require_company(db, user, company_id, PERM_MANAGE, lock=True)
    await service.create_item(db, base, user, key=body.key, title=body.title, hint=body.hint)
    return ok({})


@router.patch("/{company_id}/library/items/{key}")
async def update_item(company_id: str, key: str, body: ItemIn, db: DbSession, user: CurrentUser):
    _, base = await service.require_company(db, user, company_id, PERM_MANAGE, lock=True)
    await service.update_item(db, base, key, title=body.title, hint=body.hint)
    return ok({})


@router.delete("/{company_id}/library/items/{key}")
async def delete_item(company_id: str, key: str, db: DbSession, user: CurrentUser):
    _, base = await service.require_company(db, user, company_id, PERM_MANAGE, lock=True)
    await service.delete_item(db, base, key, clear=False)
    return ok({})


@router.delete("/{company_id}/library/items/{key}/files")
async def clear_files(company_id: str, key: str, db: DbSession, user: CurrentUser):
    _, base = await service.require_company(db, user, company_id, PERM_MANAGE, lock=True)
    await service.delete_item(db, base, key, clear=True)
    return ok({})


@router.post("/{company_id}/library/items/{key}/files")
async def upload_files(
    company_id: str,
    key: str,
    db: DbSession,
    user: CurrentUser,
    files: Annotated[list[UploadFile], File()],
    replace: Annotated[bool, Form()] = False,
):
    company, base = await service.require_company(db, user, company_id, PERM_MANAGE, lock=True)
    if len(files) > service.MAX_FILES:
        raise service.invalid(f"每项最多 {service.MAX_FILES} 个文件")
    batch = []
    total = 0
    for file in files:
        data = await file.read(service.MAX_FILE_BYTES + 1)
        if len(data) > service.MAX_FILE_BYTES:
            raise service.invalid("单个文件不能超过 40MB")
        total += len(data)
        if total > service.MAX_BATCH_BYTES:
            raise service.invalid("一次上传的文件总大小不能超过 80MB")
        batch.append((file.filename or "", data))
    await service.upload_files(db, company, base, user, key, batch, replace=replace)
    return ok({})


@router.delete("/{company_id}/library/files/{doc_id}")
async def delete_file(company_id: str, doc_id: str, db: DbSession, user: CurrentUser):
    _, base = await service.require_company(db, user, company_id, PERM_MANAGE, lock=True)
    await service.delete_file(db, base, doc_id)
    return ok({})


@router.patch("/{company_id}/library/files/{doc_id}/facts")
async def update_facts(
    company_id: str, doc_id: str, body: FactsIn, db: DbSession, user: CurrentUser
):
    _, base = await service.require_company(db, user, company_id, PERM_MANAGE, lock=True)
    await service.update_facts(db, base, doc_id, role=body.role, fields=body.fields)
    return ok({})


@router.post("/{company_id}/library/reindex")
async def reindex(company_id: str, db: DbSession, user: CurrentUser, force: bool = False):
    _, base = await service.require_company(db, user, company_id, PERM_MANAGE, lock=True)
    return ok(await service.reindex(db, base, force=force))
