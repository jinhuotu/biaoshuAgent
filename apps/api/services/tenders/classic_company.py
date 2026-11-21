"""Company selection for the old tender workflow."""

from sqlalchemy.ext.asyncio import AsyncSession

from api.services.knowledge import access
from api.services.tenders.company_identity import same_company
from api.services.tenders.company_library import require_company
from api.services.tenders.schema import BidBrief
from common.errors import AppError, ErrorCode
from db.models.knowledge import KnowledgeBase
from db.models.user import User


async def usable_reference_libraries(db: AsyncSession, user: User, ids: list[str]) -> list[str]:
    """General references and the selected company's attachments take separate paths."""
    return await access.require_usable_ids(
        db, user, [pid for pid in ids if not access.is_tender_library_id(pid.strip())]
    )


async def authorize_company_bidder(db: AsyncSession, user: User, brief: BidBrief) -> KnowledgeBase:
    company, base = await require_company(db, user, brief.bidderCompanyId, access.PERM_USE)
    brief.bidderCompanyId = company.public_id
    brief.bidderName = company.name
    brief.bidderCreditCode = company.credit_code
    # This switch refers to the legacy standalone qualification PDF.
    brief.attachQualifications = False
    brief.techPlanKbIds = await usable_reference_libraries(db, user, brief.techPlanKbIds)
    brief.bizRespKbIds = await usable_reference_libraries(db, user, brief.bizRespKbIds)
    return base


def preserve_record_company(snapshot: dict, brief: BidBrief) -> None:
    saved_id = str(snapshot.get("bidderCompanyId") or "")
    if saved_id:
        if "bidderCompanyId" in brief.model_fields_set and brief.bidderCompanyId != saved_id:
            raise AppError(
                ErrorCode.VALIDATION, "该标书已绑定投标公司，换公司请新建标书", status_code=422
            )
        brief.bidderCompanyId = saved_id


def validate_record_bidder(snapshot: dict, brief: BidBrief) -> None:
    if not snapshot.get("bidderCompanyId") and not same_company(
        str(snapshot.get("bidderName") or ""),
        brief.bidderName,
        left_code=str(snapshot.get("bidderCreditCode") or ""),
        right_code=brief.bidderCreditCode,
    ):
        raise AppError(
            ErrorCode.VALIDATION, "历史标书的投标主体不同，请为该公司新建标书", status_code=422
        )


async def attachment_preflight(db: AsyncSession, brief: BidBrief) -> dict:
    from api.services.tenders.company_materials import scope_performance_lines, scoped_library_media
    from api.services.tenders.extract import merge_performance_lines
    from api.services.tenders.library_kb import (
        catalog_placeholders,
        filter_legal_id_attachments,
        filter_performance_attachments,
        list_slots_status_kb,
        performance_from_library,
        resolve_attachments,
    )
    from api.services.tenders.match import build_attachment_match
    from api.services.tenders.performance import bid_performance_lines
    from api.services.tenders.placeholders import collect_slots, this_bid_keys
    from api.services.tenders.requirement_bid import invitation_text
    from api.services.tenders.slots import attach_invitation_drawings

    catalog = await catalog_placeholders(db)
    required, included = this_bid_keys(
        extra=brief.extraPlaceholders,
        catalog=catalog,
        required_keys=brief.requiredSlotKeys,
        include_keys=brief.includeSlotKeys,
        has_agent=bool(brief.authEnabled),
        outline_items=brief.outlineItems,
        requirements=brief.bidRequirements,
        source_text=invitation_text(brief),
        invitation_id=brief.invitationId,
        material_demands=brief.materialDemands,
    )
    slots = collect_slots(
        brief.extraPlaceholders,
        catalog=catalog,
        include_keys=included,
        outline_items=brief.outlineItems,
    )
    media = attach_invitation_drawings(await resolve_attachments(db, slots), brief.invitationId)
    media, _, _, notes = await scoped_library_media(db, brief, media)
    if brief.legalPersonKey and "id_legal" in media:
        media["id_legal"] = await filter_legal_id_attachments(
            db, media["id_legal"], brief.legalPersonKey
        )
    if "perf" in media:
        performance, _ = scope_performance_lines(brief)
        from_library = await performance_from_library(db, bidder_name=brief.bidderName)
        if from_library:
            performance = merge_performance_lines(
                performance,
                from_library,
                requirement=brief.performanceRequirement,
                preserve_flags=True,
            )
        media["perf"] = await filter_performance_attachments(
            db, media["perf"], bid_performance_lines(performance)
        )
    matches = build_attachment_match(
        [
            {"key": item.key, "title": item.title, "fileCount": len(media.get(item.key) or [])}
            for item in slots
        ],
        required_keys=required,
        include_keys=included,
    )
    return {
        "attachmentMatch": matches,
        "catalogSlots": await list_slots_status_kb(db),
        "slots": await list_slots_status_kb(
            db, brief.extraPlaceholders, include_keys=included, invitation_id=brief.invitationId
        ),
        "companyName": brief.bidderName,
        "warnings": notes,
    }
