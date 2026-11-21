"""Authenticated company-scoped routes used by the old tender page."""

import json
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field

from api.deps import CurrentUser, DbSession
from api.services.tenders import classic_company, material_store
from api.services.tenders.library_scope import use_company_base
from api.services.tenders.schema import BidBrief
from common.errors import AppError, ErrorCode
from common.response import ok

router = APIRouter(prefix="/companies/{company_id}/classic")


async def _bind(db: DbSession, user: CurrentUser, company_id: str, brief: BidBrief):
    if brief.bidderCompanyId and brief.bidderCompanyId != company_id:
        raise AppError(ErrorCode.VALIDATION, "表单中的公司与当前选择不一致", status_code=422)
    brief.bidderCompanyId = company_id
    return await classic_company.authorize_company_bidder(db, user, brief)


@router.post("/preflight")
async def preflight(company_id: str, body: BidBrief, db: DbSession, user: CurrentUser):
    base = await _bind(db, user, company_id, body)
    with use_company_base(base):
        result = await classic_company.attachment_preflight(db, body)
    archives = material_store.preflight(int(user.id), body)
    notes = [*result.pop("warnings"), *archives["warnings"]]
    return ok({**archives, **result, "warnings": list(dict.fromkeys(notes))})


@router.post("/parse-invitation")
async def parse_invitation(
    company_id: str,
    db: DbSession,
    user: CurrentUser,
    files: Annotated[list[UploadFile], File()],
    current: Annotated[str | None, Form()] = None,
    quoteFiles: Annotated[list[UploadFile] | None, File()] = None,
    quoteRecordIds: Annotated[str | None, Form()] = None,
    kbIds: Annotated[str | None, Form()] = None,
):
    from api.routers.tenders import _parse_current_brief, parse_kb_ids, tenders_parse_invitation

    brief, _ = _parse_current_brief(current)
    brief = brief or BidBrief()
    base = await _bind(db, user, company_id, brief)
    references = await classic_company.usable_reference_libraries(db, user, parse_kb_ids(kbIds))
    with use_company_base(base):
        response = await tenders_parse_invitation(
            db=db,
            user=user,
            files=files,
            file=None,
            quoteFiles=quoteFiles,
            quoteFile=None,
            quoteRecordIds=quoteRecordIds,
            kbIds=json.dumps(references),
            current=brief.model_dump_json(exclude_unset=True),
        )
        parsed = response["data"]
        parsed["brief"].update(
            bidderCompanyId=brief.bidderCompanyId,
            bidderName=brief.bidderName,
            bidderCreditCode=brief.bidderCreditCode,
        )
        report = await classic_company.attachment_preflight(
            db, BidBrief.model_validate(parsed["brief"])
        )
        parsed.update(attachmentMatch=report["attachmentMatch"])
    return response


class TechDraft(BaseModel):
    brief: BidBrief
    kbIds: list[str] = Field(default_factory=list)
    itemId: str | None = None
    promptId: str | None = None
    materialIds: list[str] = Field(default_factory=list)


@router.post("/tech/draft")
async def draft_tech(company_id: str, body: TechDraft, db: DbSession, user: CurrentUser):
    from api.routers.tenders import TechDraftIn, tenders_tech_draft

    base = await _bind(db, user, company_id, body.brief)
    body.kbIds = await classic_company.usable_reference_libraries(db, user, body.kbIds)
    with use_company_base(base):
        return await tenders_tech_draft(TechDraftIn.model_validate(body.model_dump()), db, user)
