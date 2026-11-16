"""只读对接“投标资料库”页面的记录，将可确认内容匹配到生成资料。"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from api.services.bidgen.library_fields import (
    ROW_KEYS, certificate_category, extract_profile_fields, label_key, person_group,
)
from api.services.bidgen.profile import (
    ProfileStore, normalize_certificate, parse_validity_text,
)
from api.services.knowledge.access import TENDER_LIB_PUBLIC_ID
from api.services.knowledge.ingest import read_extracted_text, resolve_storage_path
from api.services.tenders.company_identity import company_from_document, same_company
from api.services.tenders.facts import view_facts
from common.errors import AppError
from db.models.knowledge import KnowledgeBase, KnowledgeDocument

logger = logging.getLogger(__name__)
GROUPS = {
    "base": {"company", "legalRep", "agent", "techLead", "shareholders"},
    "people": {"staff", "finance", "service", "declarations"},
    "capability": {"equipment", "testEquipment", "performances", "patents", "standards", "awards", "systems"},
    "certificates": {"certificates"},
}


@dataclass
class LibraryDocument:
    public_id: str
    name: str
    title: str
    slot: str = ""
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    text: str = ""
    path: Path | None = None
    expire_at: date | None = None
    is_perpetual: bool = False


def _read_document(document, parent, base_id: str) -> LibraryDocument | None:
    tags = list(dict.fromkeys([*(getattr(parent, "tags", None) or []), *(document.tags or [])]))
    slot = next((tag[5:] for tag in tags if tag.startswith("slot:")), "")
    path = None
    for key in dict.fromkeys([document.file_key, document.storage_path]):
        if key:
            try:
                candidate = resolve_storage_path(key)
                if candidate.is_file():
                    path = candidate
                    break
            except (OSError, AppError):
                continue
    text = read_extracted_text(base_id, document.public_id) or ""
    # Word / Excel 原件无需等待向量库，可以直接读取；扫描件使用资料库已有 OCR。
    if not text and path:
        try:
            if path.suffix.lower() == ".docx":
                from docx import Document

                word = Document(path)
                parts = [paragraph.text for paragraph in word.paragraphs]
                parts.extend("\t".join(cell.text for cell in row.cells) for table in word.tables for row in table.rows)
                text = "\n".join(parts)
            elif path.suffix.lower() == ".xlsx":
                from openpyxl import load_workbook

                workbook = load_workbook(path, read_only=True, data_only=True)
                try:
                    text = "\n".join("\t".join("" if value is None else str(value) for value in row)
                                     for sheet in workbook for row in sheet.iter_rows(values_only=True))
                finally:
                    workbook.close()
            elif path.suffix.lower() == ".pdf":
                from pypdf import PdfReader

                text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        except Exception:
            logger.warning("bidgen library text unavailable: %s", document.public_id, exc_info=True)
    summary = document.summary or ""
    if not path and not text and not view_facts(summary):
        return None  # 资料库分组标题/占位项不是企业资料。
    return LibraryDocument(
        public_id=document.public_id, name=document.name or "", title=getattr(parent, "name", "") or document.name or "",
        slot=slot, summary=summary, tags=tags, path=path, text=text,
        expire_at=getattr(document, "expire_at", None), is_perpetual=bool(getattr(document, "is_perpetual", False)),
    )


async def load_library_documents(db) -> list[LibraryDocument]:
    """与 TenderLibraryView 共用 tenderlib01；不触发建库、迁移或重新 OCR。"""
    result = await db.execute(select(KnowledgeBase).where(
        KnowledgeBase.public_id == TENDER_LIB_PUBLIC_ID, KnowledgeBase.status != "deleted",
    ))
    base = result.scalar_one_or_none()
    if base is None:
        # 与页面的 ensure_library_base 兼容早期按名称创建的 asset 库。
        result = await db.execute(select(KnowledgeBase).where(
            KnowledgeBase.name == "投标资料库", KnowledgeBase.purpose == "asset", KnowledgeBase.status != "deleted",
        ))
        base = result.scalar_one_or_none()
    if base is None:
        return []
    result = await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.base_id == base.id)
                              .order_by(KnowledgeDocument.updated_at.desc(), KnowledgeDocument.id.desc()))
    records = list(result.scalars().all())
    by_id = {document.public_id: document for document in records}
    documents = []
    for record in records:
        parent = by_id.get(record.parent_id)
        if record.review_status != "approved" or record.status == "deleted":
            continue
        if parent and (parent.review_status != "approved" or parent.status == "deleted"):
            continue
        document = await run_in_threadpool(_read_document, record, parent, base.public_id)
        if document:
            documents.append(document)
    return documents


def _flatten(patch: dict, prefix: str = "") -> dict:
    fields = {}
    for key, value in patch.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            fields.update(_flatten(value, path))
        elif value != "" and value != []:
            fields[path] = value
    return fields


_SUGGESTION_GROUPS = {"company", "legalRep", "agent", "techLead", "staff", "service"}


def _collect_suggestions(suggestions: dict, patch: dict, document, owner: str) -> None:
    """资料库识别出的单值字段，连同来源一起留给页面展示；主体不一致的资料也展示，但标明主体。"""
    for path, value in _flatten(patch).items():
        if path.split(".")[0] not in _SUGGESTION_GROUPS or not isinstance(value, (str, int, float)) or isinstance(value, bool):
            continue
        entries = suggestions.setdefault(path, [])
        if all(str(entry["value"]) != str(value) for entry in entries):
            entries.append({"value": str(value), "documentId": document.public_id, "name": document.name,
                            "title": document.title, "owner": owner})


def _profile_value(profile: dict, path: str) -> str:
    value: object = profile
    for key in path.split("."):
        value = value.get(key, "") if isinstance(value, dict) else ""
    return str(value or "").strip()


def _assign(patch: dict, path: str, value) -> None:
    keys = path.split(".")
    target = patch
    for key in keys[:-1]:
        target = target.setdefault(key, {})
    target[keys[-1]] = value


def _row_identity(group: str, row: dict) -> str:
    if group == "certificates":
        return str(row.get("id", ""))
    return label_key(row.get(ROW_KEYS.get(group, "name"), ""))


def _merge_values(current, candidate, previous, *, group: str = ""):
    """三方合并：刷新自动值，保留相对上次自动结果的人工修正。"""
    if isinstance(current, dict):
        candidate, previous = candidate or {}, previous or {}
        merged = {key: _merge_values(value, candidate.get(key), previous.get(key), group=key if not group else group)
                for key, value in current.items()} | {
                    key: deepcopy(value) for key, value in candidate.items() if key not in current
                }
        if group == "certificates" and current.get("storedFile") and previous and current["storedFile"] != previous.get("storedFile"):
            for key in ("storedFile", "fileName", "libraryDocumentId", "librarySource"):
                merged[key] = current.get(key, "")
        return merged
    if isinstance(current, list):
        candidate, previous = candidate or [], previous or []
        if group == "systems":
            return [value for value in current if value not in previous or value in candidate] + [
                value for value in candidate if value not in current and value not in previous
            ]
        by_candidate = {_row_identity(group, row): row for row in candidate}
        by_previous = {_row_identity(group, row): row for row in previous}
        merged, seen = [], set()
        for row in current:
            identity = _row_identity(group, row)
            seen.add(identity)
            old, new = by_previous.get(identity), by_candidate.get(identity)
            if old is not None and new is None:
                if row != old:
                    merged.append(deepcopy(row))
                continue
            merged.append(_merge_values(row, new, old, group=group))
        for row in candidate:
            identity = _row_identity(group, row)
            if identity not in seen and identity not in by_previous:
                merged.append(deepcopy(row))
                seen.add(identity)
        return merged
    empty = False if isinstance(current, bool) else ""
    if current == previous or (previous is None and current == empty):
        return deepcopy(candidate) if candidate is not None else empty
    return current


def _certificate(document, fields: list[dict], current: dict, previous: dict, claimed_ids: set[str]) -> dict | None:
    category = certificate_category("", document.name) or certificate_category(document.slot, document.title)
    if not category or not document.path:
        return None
    identifier = "lib_" + hashlib.sha256(document.public_id.encode()).hexdigest()[:16]
    linked = next((item for item in [*current["certificates"], *previous.get("certificates", [])]
                   if item.get("libraryDocumentId") == document.public_id), None)
    placeholders = [item for item in current["certificates"] if item["category"] == category
                    and not item.get("libraryDocumentId") and not item.get("storedFile") and item["id"] not in claimed_ids]
    if linked:
        identifier = linked["id"]
    elif len(placeholders) == 1:
        identifier = placeholders[0]["id"]
    claimed_ids.add(identifier)
    values = {label_key(item["name"]): item["value"] for item in fields if item.get("conf", 1) >= 0.8}
    validity = parse_validity_text(document.text) or {}
    if document.expire_at or document.is_perpetual:
        validity = {"validTo": document.expire_at.isoformat() if document.expire_at else "",
                    "perpetual": document.is_perpetual}
    certificate = normalize_certificate({
        "id": identifier, "category": category, "name": document.name or document.title,
        "number": next((values[key] for key in ["证书编号", "证件编号", "证书号", "许可证编号"] if values.get(key)), ""),
        "issuer": next((values[key] for key in ["颁发机构", "发证机关", "签发机关"] if values.get(key)), ""),
        **validity, "fileName": document.name + ("" if Path(document.name).suffix else document.path.suffix),
        "storedFile": f"{identifier}{document.path.suffix.lower()}",
        "libraryDocumentId": document.public_id, "librarySource": document.title,
    })
    return certificate


def _exclude_other_people(parsed: list, current: dict, previous: dict, warnings: list[str]) -> set[str]:
    """按完整人员身份选取资料，姓名有冲突时不能拼接其他字段。"""
    excluded = set()
    for group, label in (("legalRep", "法定代表人"), ("agent", "委托代理人"), ("techLead", "技术负责人")):
        people, company_names = [], set()
        for document, _fields, patch, _owner in parsed:
            facts = view_facts(document.summary) or {}
            if person_group(document.slot, document.title, facts.get("role", "")) == group:
                people.append((document, patch.get(group, {})))
            elif patch.get(group, {}).get("name"):
                company_names.add(patch[group]["name"])
        if not people:
            continue
        saved_name = current[group].get("name", "")
        if saved_name == previous.get(group, {}).get("name") and len(company_names) == 1:
            saved_name = next(iter(company_names))
        names = {person.get("name") for _doc, person in people if person.get("name")}
        selected_name = saved_name or (next(iter(company_names)) if len(company_names) == 1 else "")
        selected_name = selected_name or (next(iter(names)) if len(names) == 1 else "")
        selected_id = current[group].get("idNumber", "")
        if len(names) > 1 and not selected_name and not selected_id:
            excluded.update(document.public_id for document, _person in people)
            warnings.append(f"资料库包含多位{label}，请先填写本次使用的姓名或身份证号并重新匹配。")
            continue
        ids = {person.get("idNumber") for _doc, person in people if person.get("idNumber")
               and (not selected_name or person.get("name") == selected_name)}
        selected_id = selected_id or (next(iter(ids)) if len(ids) == 1 else "")
        if len(ids) > 1 and not selected_id:
            excluded.update(document.public_id for document, _person in people)
            warnings.append(f"资料库包含多位{label}，请先填写本次使用的姓名或身份证号并重新匹配。")
            continue
        for document, person in people:
            name, identity = person.get("name", ""), person.get("idNumber", "")
            if ((selected_name and name and selected_name != name)
                    or (selected_id and identity and selected_id != identity)
                    or (selected_name and not name and identity and identity != selected_id)
                    or (len(people) > 1 and not name and not identity)):
                excluded.add(document.public_id)
    return excluded


def match_library_documents(store: ProfileStore, documents: list) -> dict:
    current = store.load()
    metadata_path = store.folder / "library-match.json"
    previous = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
    parsed, owners = [], set()
    warnings = []
    for document in documents:
        facts = view_facts(document.summary) or {}
        fields = facts.get("fields", [])
        patch = extract_profile_fields(fields=fields, text=document.text, slot=document.slot,
                                       title=document.title, role=facts.get("role", ""))
        owner = company_from_document(document) or patch.get("company", {}).get("name", "")
        if owner and owner != "__conflicting_company__":
            owners.add(owner)
        parsed.append((document, fields, patch, owner))
    bidder = current["company"]["name"] or (next(iter(owners)) if len(owners) == 1 else "")
    multiple_companies = len({label_key(owner) for owner in owners}) > 1
    if multiple_companies and not bidder:
        warnings.append("资料库包含多家企业，请先填写本次投标人名称并重新匹配。")
    candidates: dict[str, list] = {}
    certificate_files = {}
    claimed_ids: set[str] = set()
    excluded_people = _exclude_other_people(parsed, current, previous, warnings)
    pending_count = 0
    suggestions: dict[str, list[dict]] = {}
    for document, fields, patch, owner in parsed:
        if document.public_id in excluded_people:
            continue
        if owner == "__conflicting_company__" or (owner and bidder and not same_company(owner, bidder)):
            subject = "资料中有多个企业主体" if owner == "__conflicting_company__" else f"资料主体为“{owner}”"
            warnings.append(f"{document.title or document.name}（{document.name}）：{subject}，与投标人“{bidder}”不一致，"
                            "未自动填入；识别结果已列在对应字段下，确认无误后可点“采用”，或先核对投标人名称。")
            _collect_suggestions(suggestions, patch, document, owner if owner != "__conflicting_company__" else "多个企业主体")
            continue
        _collect_suggestions(suggestions, patch, document, "")
        if multiple_companies and (not bidder or not owner):
            continue
        source = {"documentId": document.public_id, "name": document.name, "title": document.title}
        certificate = _certificate(document, fields, current, previous, claimed_ids)
        if certificate:
            patch["certificates"] = [certificate]
            certificate_files[certificate["id"]] = document.path
        if not fields and not document.text.strip():
            pending_count += 1
        for path, value in _flatten(patch).items():
            candidates.setdefault(path, []).append((value, source))
    patch, sources = {}, {}
    for path, entries in candidates.items():
        if isinstance(entries[0][0], list):
            group = path
            if group == "systems":
                values = list(dict.fromkeys(value for items, _source in entries for value in items))
            else:
                rows: dict[str, dict] = {}
                conflicts: set[tuple[str, str]] = set()
                for items, _source in entries:
                    for row in items:
                        identity = _row_identity(group, row)
                        target = rows.setdefault(identity, {})
                        for key, value in row.items():
                            if (identity, key) in conflicts:
                                continue
                            if key in target and target[key] != value and target[key] != "" and value != "":
                                target.pop(key)
                                conflicts.add((identity, key))
                                warnings.append(f"{group} / {identity}：多份资料的 {key} 不一致，请手动核对。")
                            elif value != "":
                                target[key] = value
                            else:
                                target.setdefault(key, value)
                values = list(rows.values())
            _assign(patch, path, values)
            sources[path] = [source for _value, source in entries]
        else:
            values = {json.dumps(value, ensure_ascii=False) for value, _source in entries}
            if len(values) == 1:
                _assign(patch, path, entries[0][0])
                sources[path] = [source for _value, source in entries]
            else:
                warnings.append(f"{path}：资料库中的值不一致，请手动核对。")
    profile = _merge_values(current, patch, previous)
    for certificate in profile["certificates"]:
        original = certificate_files.get(certificate["id"])
        expected = next((item for item in patch.get("certificates", []) if item["id"] == certificate["id"]), {})
        if original and certificate.get("storedFile") == expected.get("storedFile"):
            destination = store.folder / certificate["storedFile"]
            if not destination.is_file() or destination.read_bytes() != original.read_bytes():
                shutil.copyfile(original, destination)
    store._write(profile)
    metadata_path.write_text(json.dumps(patch, ensure_ascii=False, indent=2), encoding="utf-8")
    groups = {group: sum(len(patch.get(path, [])) if isinstance(patch.get(path), list) else 1
                         for path in sources if path.split(".")[0] in sections)
              for group, sections in GROUPS.items()}
    return {"profile": profile, "libraryMatch": {
        "documentCount": len(documents), "matchedCount": sum(groups.values()), "groups": groups,
        "sources": sources, "warnings": list(dict.fromkeys(warnings)), "pendingCount": pending_count,
        # 与当前资料不同的识别值（含主体不一致资料）：页面在字段下展示，由用户决定是否采用
        "suggestions": {path: [entry for entry in entries if entry["value"] != _profile_value(profile, path)]
                        for path, entries in suggestions.items()
                        if any(entry["value"] != _profile_value(profile, path) for entry in entries)},
    }}


async def match_profile_from_library(store: ProfileStore, db) -> dict:
    documents = await load_library_documents(db)
    return await run_in_threadpool(match_library_documents, store, documents)
