"""Select a bounded, authorized knowledge scope for ordinary chat."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload

from api.services.knowledge import access
from api.services.knowledge.bases import PURPOSE_ASSET, PURPOSE_RAG
from api.services.knowledge.rerank import keyword_overlap_score
from db.models.knowledge import KnowledgeBase
from db.models.tender_library import TenderCompany
from db.models.user import User

_TENDER = re.compile(
    r"营业执照|开户许可证|资质证书|体系认证|授权委托书|法人身份证|法定代表人|投标资料|检测报告|检验报告|试验报告|产品报告|业绩合同|投标主体|投标公司"
)
_INTERNAL = re.compile(
    r"知识库|内部资料|公司制度|内部制度|操作规程|作业指导书|使用手册|公司规定|内部规范|本公司|我们公司|公司内部|咱们公司|资料库|(?:报销|请假|考勤|调休)(?:流程|标准|规定|制度)|(?:怎么|如何)报销|年假(?:几天|怎么算)"
)
_PROJECT = re.compile(
    r"本项目|当前项目|这[个份]项目|这次投标|另一个项目|其他项目|招标文件|投标邀请书"
)
_GENERAL = re.compile(
    r"^\s*(?:请问|帮我|请)?\s*(?:解释(?:一下)?|什么叫|什么是|如何办理|如何申请|如何编写)|是什么意思|的定义是什么"
)
_FOLLOW_UP = re.compile(
    r"^(?:那|它|该|这份|这个|同一|同一家|有效期|到期)|(?:还有效吗|什么时候到期|哪天到期|有原件吗|还有吗|发我原件|(?:能|可以|怎么|如何)续期(?:吗|呢)?)[？?。！!]*$|这家(?:公司)?|上[一份个次]"
)
_SWITCH = re.compile(r"另一家|其他公司|别的公司|其它公司|换一[家个]|另一个项目|其他项目")
_COMPARISON = re.compile(r"对照|比较|差异|招标文件.{0,8}(?:要求|规定)")
_COMPANY_REFERENCE = re.compile(r"这家|该公司|同一家|本公司|本单位")
_COMPANY_NAME = re.compile(r"有限公司|集团公司|股份公司|[0-9A-Z]{18}", re.I)
_COMPANY_ONLY = re.compile(
    r"(?:[\w()（）·.\-\s]{2,100}(?:有限公司|集团公司|股份公司)|[0-9A-Z]{18})[。！？?!]?",
    re.I,
)

COMPANY_QUESTION = "请告诉我这次要查询的投标公司名称。我会按该公司的资料范围查找。"
PROJECT_QUESTION = "请给出存放该项目文件的知识库名称，或附上要核对的项目文件，我再依据原文回答。"
NO_SCOPE = "目前无法确定可用的资料范围。你可以补充资料名称，或在知识库菜单中指定来源后重试。"
COMPANY_LIBRARY_MISMATCH = (
    "指定的知识库没有关联到这家投标公司。"
    "请直接告诉我想查哪项公司资料，我会在该公司的投标资料库中查找。"
)
NO_EVIDENCE = "在本次可用资料中未找到能支持回答的片段。你可以补充文件名称、版本或更具体的问题。"
SEARCH_FAILED = "本次知识库检索暂时失败，请稍后重试。"
MAX_CHUNKS = 5
MAX_CHUNK_CHARS = 600
MAX_EVIDENCE_CHARS = 2400
_MAX_GENERAL_BASES = 3


@dataclass(frozen=True)
class KnowledgeRoute:
    query: str = ""
    base_ids: tuple[str, ...] = ()
    reply: str = ""


def _key(text: str) -> str:
    return re.sub(r"[\s()（）]", "", text).casefold()


def _project_label(name: str) -> str:
    label = _key(name)
    return label if label.endswith("项目库") else ""


def _names_base(query: str, name: str) -> bool:
    label = _key(name)
    project = _project_label(name).removesuffix("库")
    return (len(label) >= 2 and label in query) or (len(project) >= 4 and project in query)


def _company_matches(query: str, companies: list[tuple[str, list[str]]]) -> set[str]:
    query = _key(query)
    matches: list[tuple[str, int, int]] = []
    for base_id, names in companies:
        for name in names:
            term = _key(name)
            if len(term) < 2:
                continue
            starts = (m.start() for m in re.finditer(f"(?={re.escape(term)})", query))
            matches.extend((base_id, start, start + len(term)) for start in starts)
    # A short alias inside a longer company name does not identify a second company.
    return {
        base_id
        for base_id, start, end in matches
        if not any(a <= start and b >= end and (a, b) != (start, end) for _, a, b in matches)
    }


def _follow_up(query: str, previous: str) -> bool:
    return bool(
        previous and len(query) <= 100 and _FOLLOW_UP.search(query) and not _SWITCH.search(query)
    )


def _bare_company(query: str, companies: list[tuple[str, list[str]]]) -> bool:
    term = _key(query.strip().rstrip("。！？?!"))
    return bool(term and any(term == _key(name) for _, names in companies for name in names))


def _unknown_company_identifier(query: str, companies: list[tuple[str, list[str]]]) -> bool:
    text = _key(query)
    # A short alias inside an unregistered full name must not resolve that company.
    identifiers = [
        _key(name) for _, names in companies for name in names if _COMPANY_NAME.search(name)
    ]
    return any(
        not any(
            term.endswith(match.group()) and text[: match.end()].endswith(term)
            for term in identifiers
        )
        for match in _COMPANY_NAME.finditer(text)
    )


def _turn_context(
    query: str,
    previous: str,
    companies: list[tuple[str, list[str]]],
    general_bases: list[tuple[str, str, str, bool]],
) -> tuple[str, str]:
    """Return this turn's retrieval query and the bounded user-provided topic anchor."""
    if not previous or _GENERAL.search(query):
        return query, query
    current_companies = _company_matches(query, companies)
    prior_companies = _company_matches(previous, companies)
    prior_types = list(dict.fromkeys(_TENDER.findall(previous)))
    unknown_company = _unknown_company_identifier(query, companies)
    if prior_types and not prior_companies and _bare_company(query, companies):
        clarified = f"{previous[-280:]}\n{query[-210:]}"
        return clarified, clarified

    normalized = _key(query)
    named_base = any(_names_base(normalized, name) for _, name, _, _ in general_bases)
    if named_base and _PROJECT.search(previous):
        bare_name = normalized.rstrip("。！？?!")
        project_labels = [
            label for _, name, _, _ in general_bases if (label := _project_label(name))
        ]
        if any(
            bare_name in (label, label.removesuffix("库")) for label in project_labels
        ) and not any(_names_base(_key(previous), label) for label in project_labels):
            clarified = f"{previous[-280:]}\n{query[-210:]}"
            return clarified, clarified
    changing_scope = bool(
        unknown_company
        or _SWITCH.search(query)
        or (current_companies and current_companies != prior_companies)
    )
    if changing_scope:
        if (
            not _TENDER.search(query)
            and not named_base
            and prior_types
            and (_FOLLOW_UP.search(query) or _SWITCH.search(query))
        ):
            changed = f"{query[-280:]}\n{'、'.join(prior_types)}"
            return changed, changed
        return query, query

    if named_base or _TENDER.search(query):
        # A new explicit topic replaces the previous one. References like “这家公司”
        # still need the confirmed company, without carrying along the old document type.
        if (
            not current_companies
            and len(prior_companies) == 1
            and not _PROJECT.search(query)
            and (_COMPANY_REFERENCE.search(query) or _follow_up(query, previous))
        ):
            company = next(names[0] for base_id, names in companies if base_id in prior_companies)
            updated = f"{company}\n{query[-280:]}"
            return updated, updated
        return query, query

    if _PROJECT.search(query) and not _PROJECT.search(previous):
        return query, query
    if _follow_up(query, previous):
        return f"{previous[-280:]}\n{query[-210:]}", previous
    return query, query


def _scope(
    query: str,
    previous: str,
    *,
    companies: list[tuple[str, list[str]]],
    usable_company_ids: set[str],
    general_bases: list[tuple[str, str, str, bool]],
    has_attachment: bool,
) -> KnowledgeRoute:
    company_ids = _company_matches(query, companies)
    earlier_companies = _company_matches(previous, companies) if previous else set()
    if (
        _TENDER.search(previous)
        and not earlier_companies
        and _COMPANY_ONLY.fullmatch(query)
        and not _GENERAL.search(query)
        and (not company_ids or _unknown_company_identifier(query, companies))
    ):
        return KnowledgeRoute(reply=NO_SCOPE)
    effective, _ = _turn_context(query, previous, companies, general_bases)
    company_ids = _company_matches(effective, companies)
    project = bool(_PROJECT.search(effective))
    tender = bool(
        _TENDER.search(effective)
        or (
            (company_ids or _COMPANY_REFERENCE.search(effective))
            and re.search(r"证书|资质|合同|报告|执照", effective)
        )
    )
    normalized = _key(effective)
    named = [
        (base_id, usable)
        for base_id, name, _, usable in general_bases
        if _names_base(normalized, name)
    ]
    explicit = [base_id for base_id, usable in named if usable]

    if (
        _GENERAL.search(query)
        and not (company_ids or named or _INTERNAL.search(query) or _PROJECT.search(query))
        and not _COMPANY_REFERENCE.search(query)
    ):
        return KnowledgeRoute()
    if (
        has_attachment
        and (project or re.search(r"附件|这张图|这份文件", query))
        and not (company_ids or named)
        and not (tender and project)
    ):
        return KnowledgeRoute()
    if len(explicit) != len(named):
        return KnowledgeRoute(reply=NO_SCOPE)
    if tender:
        if _unknown_company_identifier(effective, companies):
            return KnowledgeRoute(reply=NO_SCOPE)
        if len(company_ids) != 1:
            return KnowledgeRoute(reply=COMPANY_QUESTION)
        if not company_ids <= usable_company_ids:
            return KnowledgeRoute(reply=NO_SCOPE)
        if project and _COMPARISON.search(effective):
            project_bases = [
                base_id
                for base_id, name, _, _ in general_bases
                if base_id in explicit and _project_label(name)
            ]
            if explicit and len(project_bases) != len(explicit):
                return KnowledgeRoute(reply=COMPANY_LIBRARY_MISMATCH)
            if len(project_bases) > 1 or (not project_bases and not has_attachment):
                return KnowledgeRoute(reply=PROJECT_QUESTION)
            return KnowledgeRoute(effective, (*company_ids, *project_bases))
        if explicit:
            return KnowledgeRoute(reply=COMPANY_LIBRARY_MISMATCH)
        return KnowledgeRoute(effective, tuple(company_ids))
    if explicit:
        if len(explicit) > _MAX_GENERAL_BASES:
            return KnowledgeRoute(reply=NO_SCOPE)
        return KnowledgeRoute(effective, tuple(explicit))
    if project:
        return KnowledgeRoute(reply=PROJECT_QUESTION)
    if not _INTERNAL.search(effective):
        return KnowledgeRoute()

    ranked = sorted(
        (
            (keyword_overlap_score(effective, description, name), base_id)
            for base_id, name, description, usable in general_bases
            if usable
        ),
        key=lambda item: (-item[0], item[1]),
    )
    selected = tuple(base_id for score, base_id in ranked[:_MAX_GENERAL_BASES] if score >= 0.12)
    if not selected and len(ranked) == 1:
        selected = (ranked[0][1],)
    return KnowledgeRoute(effective, selected) if selected else KnowledgeRoute(reply=NO_SCOPE)


async def resolve_knowledge_route(
    db: AsyncSession,
    user: User,
    query: str,
    history: list[dict[str, Any]],
    *,
    has_attachment: bool,
) -> KnowledgeRoute:
    if not query.strip():
        return KnowledgeRoute()
    previous_turns = [str(row.get("content") or "") for row in history if row.get("role") == "user"]
    bases = list(
        (
            await db.scalars(
                select(KnowledgeBase)
                .where(KnowledgeBase.status == "active")
                .where(
                    KnowledgeBase.purpose.in_([PURPOSE_RAG, PURPOSE_ASSET])
                    | KnowledgeBase.purpose.is_(None)
                )
                .options(lazyload("*"))
                .order_by(KnowledgeBase.public_id)
            )
        ).all()
    )
    company_base_ids = [
        base.id for base in bases if access.is_company_tender_library_id(base.public_id)
    ]
    company_rows = (
        (
            await db.scalars(
                select(TenderCompany).where(TenderCompany.base_id.in_(company_base_ids))
            )
        ).all()
        if company_base_ids
        else []
    )
    by_base = {company.base_id: company for company in company_rows}
    perms = await access.perms_for_bases(db, user, bases)
    companies = []
    usable_companies = set()
    general = []
    for base in bases:
        company = by_base.get(base.id)
        can_use = access.PERM_USE in perms.get(base.id, set())
        if company:
            companies.append(
                (base.public_id, [company.name, company.credit_code, *(company.aliases or [])])
            )
            if can_use:
                usable_companies.add(base.public_id)
        elif not access.is_tender_library_id(base.public_id) and base.purpose in (
            None,
            PURPOSE_RAG,
        ):
            general.append((base.public_id, base.name, base.description or "", can_use))
    previous = ""
    for turn in previous_turns:
        _, previous = _turn_context(turn, previous, companies, general)
    return _scope(
        query,
        previous,
        companies=companies,
        usable_company_ids=usable_companies,
        general_bases=general,
        has_attachment=has_attachment,
    )


def bounded_evidence(
    chunks: list[dict[str, Any]], base_ids: tuple[str, ...], *, min_score: float
) -> list[dict[str, Any]]:
    """Keep references and model input on the same approved, bounded evidence."""
    remaining = MAX_EVIDENCE_CHARS
    evidence = []
    for chunk in chunks:
        if len(evidence) >= MAX_CHUNKS or not remaining:
            break
        if (chunk.get("kb_id") or chunk.get("kbId")) not in base_ids:
            continue
        if float(chunk.get("score") or 0.0) < min_score:
            continue
        content = str(chunk.get("content") or "").strip()[: min(MAX_CHUNK_CHARS, remaining)]
        if not content:
            continue
        evidence.append({**chunk, "content": content})
        remaining -= len(content)
    return evidence
