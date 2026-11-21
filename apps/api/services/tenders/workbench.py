"""Persistent, user-owned drafts for the ordinary technical tender workbench."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from api.services.tenders.assets import tender_assets_dir
from api.services.tenders.schema import BidBrief, OutlineItem
from common.errors import AppError, ErrorCode


def directory() -> Path:
    path = tender_assets_dir() / "workbench"
    path.mkdir(exist_ok=True)
    return path


def files_dir(owner: int, project_id: str) -> Path:
    return directory() / str(owner) / project_id


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(directory() / "projects.sqlite3")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY, owner INTEGER NOT NULL, revision INTEGER NOT NULL,
            updated TEXT NOT NULL, data TEXT NOT NULL
        )"""
    )
    connection.execute("CREATE INDEX IF NOT EXISTS projects_owner ON projects(owner, updated)")
    return connection


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def invalid(message: str) -> AppError:
    return AppError(ErrorCode.VALIDATION, message, status_code=422)


def missing() -> AppError:
    return AppError(ErrorCode.NOT_FOUND, "项目或文件不存在", status_code=404)


def conflict() -> AppError:
    return AppError(
        ErrorCode.CONFLICT, "项目已在其他页面更新，请重新打开后核对并保存", status_code=409
    )


def create(owner: int, project_id: str, sources: list[dict]) -> dict:
    project = {
        "sources": sources,
        "brief": None,
        "inputIssue": "",
        "notes": [],
        "attachments": {},
        "allowedSlots": [],
        "legalPersons": [],
        "quote": None,
        "format": None,
        "output": None,
        "lastChapterId": "",
    }
    updated = _now()
    with closing(_connect()) as db, db:
        db.execute(
            "INSERT INTO projects VALUES (?, ?, 1, ?, ?)",
            (project_id, owner, updated, json.dumps(project, ensure_ascii=False)),
        )
    return {**project, "id": project_id, "revision": 1, "updatedAt": updated}


def get(owner: int, project_id: str, revision: int | None = None) -> dict:
    with closing(_connect()) as db:
        row = db.execute(
            "SELECT revision, updated, data FROM projects WHERE id=? AND owner=?",
            (project_id, owner),
        ).fetchone()
    if row is None:
        raise missing()
    if revision is not None and row[0] != revision:
        raise conflict()
    return {**json.loads(row[2]), "id": project_id, "revision": row[0], "updatedAt": row[1]}


def list_projects(owner: int) -> list[dict]:
    with closing(_connect()) as db:
        rows = db.execute(
            """SELECT id, updated, json_extract(data, '$.brief.projectName'),
                      json_extract(data, '$.sources[0].name'), json_extract(data, '$.inputIssue'),
                      json_type(data, '$.brief')
               FROM projects WHERE owner=? ORDER BY updated DESC, id DESC""",
            (owner,),
        ).fetchall()
    return [
        {
            "id": row[0],
            "updatedAt": row[1],
            "name": row[2] or row[3],
            "status": "blocked" if row[4] else "ready" if row[5] == "object" else "warning",
        }
        for row in rows
    ]


def save(owner: int, project: dict, *, changed: bool = False) -> dict:
    snapshot = {**project}
    project_id = snapshot.pop("id")
    revision = snapshot.pop("revision")
    snapshot.pop("updatedAt")
    if changed and snapshot["output"]:
        snapshot["output"] = {**snapshot["output"], "stale": True}
    updated = _now()
    with closing(_connect()) as db, db:
        result = db.execute(
            """UPDATE projects SET data=?, revision=revision+1, updated=?
               WHERE id=? AND owner=? AND revision=?""",
            (json.dumps(snapshot, ensure_ascii=False), updated, project_id, owner, revision),
        )
        if result.rowcount != 1:
            raise conflict()
    return {**snapshot, "id": project_id, "revision": revision + 1, "updatedAt": updated}


def public(project: dict) -> dict:
    """Return only the client data, without storage keys or internal paths."""

    def file_summary(file: dict) -> dict:
        return {key: file[key] for key in ("id", "name") if key in file}

    return {
        **project,
        "legalPersons": project.get("legalPersons", []),
        "sources": [file_summary(item) for item in project["sources"]],
        "attachments": {key: file_summary(item) for key, item in project["attachments"].items()},
        "quote": file_summary(project["quote"]) if project["quote"] else None,
        "format": {key: value for key, value in project["format"].items() if key != "file"}
        if project["format"]
        else None,
        "output": {key: value for key, value in project["output"].items() if key != "file"}
        if project["output"]
        else None,
    }


def brief_for(project: dict) -> BidBrief:
    if project["inputIssue"]:
        raise invalid(project["inputIssue"])
    if not project["brief"]:
        raise invalid("请先识别项目的招标文件")
    return BidBrief.model_validate(project["brief"])


def technical_chapters(brief: BidBrief) -> list[OutlineItem]:
    return [
        item
        for item in brief.outlineItems
        if not item.skipped and item.kind in {"tech_plan", "tech_chapter"}
    ]


def chapter_for(brief: BidBrief, chapter_id: str) -> OutlineItem:
    for item in technical_chapters(brief):
        if item.id == chapter_id:
            return item
    raise missing()


def attachment_slots(brief: BidBrief, match: dict) -> list[dict]:
    from api.services.tenders.categories import slot_volume

    technical_keys = (
        {"product", "tech_drawings"}
        | {
            item.slotKey
            for item in brief.bidRequirements
            if item.volume == "technical" and item.slotKey
        }
        | {
            item.slotKey
            for item in brief.materialDemands
            if item.volume == "technical" and item.slotKey
        }
    )
    slots = {
        item["key"]: {
            "key": item["key"],
            "title": item["title"],
            "inLibrary": group == "matched",
            "required": bool(item.get("required")),
        }
        for group in ("matched", "missingFiles")
        for item in match.get(group, [])
        if item["key"] in technical_keys and slot_volume(item["key"], item["title"]) == "technical"
    }
    for key, title in (("product", "产品检测报告与参数证明"), ("tech_drawings", "本项目技术图纸")):
        slots.setdefault(key, {"key": key, "title": title, "inLibrary": False, "required": False})
    return list(slots.values())


async def legal_person_choices(db) -> list[dict]:
    from api.services.tenders.library_kb import legal_persons_from_library

    return [
        {"key": item["key"], "name": item["name"]}
        for item in await legal_persons_from_library(db, extract_missing=False)
        if item.get("key") and item.get("name")
    ]


def initialize_brief(brief: BidBrief) -> dict:
    from api.services.tenders.outline import ensure_tech_chapters

    brief.generateVolume = "technical"
    brief.generateBoth = False
    brief.splitSlice = False
    brief.authEnabled = False
    brief.autoDraftTechPlan = False
    brief.autoDraftBizResp = False
    brief.technicalPackage = {}
    brief.formatConfirmed = False
    brief.outlineItems = ensure_tech_chapters(list(brief.outlineItems), brief)
    seen = set()
    for item in brief.outlineItems:
        if not item.id or item.id in seen:
            item.id = uuid4().hex
        seen.add(item.id)
    return brief.model_dump(mode="json")
