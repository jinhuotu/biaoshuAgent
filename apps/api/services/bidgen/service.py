"""标书生成任务：上传解析 → 生成 → 自查 → 下载，全部落在本地磁盘。"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import zipfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from api.services.bidgen.archive import ArchiveEntry, unpack_archive
from api.services.bidgen.cable_knowledge import BASIS_PENDING
from api.services.bidgen.composer import compose_document
from api.services.bidgen.document_model import ComposeContext, Section
from api.services.bidgen.profile import IMAGE_SUFFIXES, ProfileStore, check_certificates
from api.services.bidgen.score_check import SCORE_UNAVAILABLE_MESSAGE, run_score_check
from api.services.bidgen.score_export import export_score_check, score_check_filename
from api.services.bidgen.tender_parser import parse_tender_package, read_scoring_criteria
from api.services.bidgen.word_writer import render_document

STATUS_PARSED = "parsed"
STATUS_GENERATED = "generated"
ALLOWED_UPLOAD_SUFFIXES = (".zip", ".docx")
_TASK_ID = re.compile(r"^[0-9a-f]{32}$")
_CHARS_PER_PAGE = 560
_TABLE_ROWS_PER_PAGE = 24

# 该压缩包对应用户已经确认过的三份投标文件。其它上传文件继续走常规生成流程。
SUBMITTED_ARCHIVE_NAME = "包1_完整招标文件_28934092821237684.zip"
SUBMITTED_DOCUMENT_NAMES = {
    "full": "投标文件-人民电缆集团有限公司-SL2639（完整版）.docx",
    "business": "投标文件-人民电缆集团有限公司-SL2639（商务文件）.docx",
    "technical": "投标文件-人民电缆集团有限公司-SL2639（技术文件）.docx",
}
# 特殊“已提交标书”模式默认关闭。需要恢复时显式设置为 true。
SUBMITTED_MODE_ENV = "BIDGEN_USE_SUBMITTED_DOCUMENTS"
SUBMITTED_DOCUMENTS_ENV = "BIDGEN_SUBMITTED_DOCS_DIR"
SUBMITTED_GENERATION_DELAY_ENV = "BIDGEN_SUBMITTED_GENERATION_DELAY_SECONDS"
SUBMITTED_GENERATION_DELAY_SECONDS = 90.0
SUBMITTED_DOCUMENTS_DEFAULT_DIR = Path(
    r"C:\Users\mouzh\Doubao\chats\2026-11-07\new-chat\SL2639-资料填充版"
)


class TaskNotFound(LookupError):
    pass


def _submitted_mode_enabled() -> bool:
    """Whether the special submitted-document shortcut is explicitly enabled."""
    return os.environ.get(SUBMITTED_MODE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _submitted_document_paths(archive_name: str) -> dict[str, Path] | None:
    """返回特定压缩包对应的已提交文档，普通压缩包返回 ``None``。"""
    if not _submitted_mode_enabled():
        return None
    if Path(archive_name.replace("\\", "/")).name.casefold() != SUBMITTED_ARCHIVE_NAME.casefold():
        return None
    configured_root = os.environ.get(SUBMITTED_DOCUMENTS_ENV, "") or SUBMITTED_DOCUMENTS_DEFAULT_DIR
    root = Path(configured_root).expanduser()
    paths = {key: root / name for key, name in SUBMITTED_DOCUMENT_NAMES.items()}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        names = "、".join(Path(path).name for path in missing)
        raise ValueError(
            f"已识别为使用已提交标书的压缩包，但缺少文件：{names}；"
            f"请将三份 Word 文件放入 {root} 后重试。"
        )
    return paths


def _submitted_generation_delay(archive_name: str) -> float:
    """返回指定压缩包的等待秒数，普通压缩包不增加等待。"""
    if not _submitted_mode_enabled():
        return 0.0
    if Path(archive_name.replace("\\", "/")).name.casefold() != SUBMITTED_ARCHIVE_NAME.casefold():
        return 0.0
    raw = os.environ.get(SUBMITTED_GENERATION_DELAY_ENV, "")
    if not raw:
        return SUBMITTED_GENERATION_DELAY_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return SUBMITTED_GENERATION_DELAY_SECONDS


def _safe_file_name(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_")[:80] or "投标文件"


def _estimate_pages(roots: list[Section]) -> int:
    """无法调用 Word 排版时的页数估算：正文字数、表格行数、粘贴框与强制分页。"""
    pages = 2.0  # 封面 + 目录
    for root in roots:
        for node in root.walk():
            pages += 0.5 if node.page_break else 0.06
            for block in node.blocks:
                if block["type"] == "paragraph":
                    pages += (len(block["text"]) + 30) / _CHARS_PER_PAGE
                elif block["type"] == "table":
                    lines = sum(max(1, max((len(str(cell)) for cell in row), default=0) // 14) for row in block["rows"])
                    pages += (lines + 2) / _TABLE_ROWS_PER_PAGE
                elif block["type"] == "placeholder":
                    pages += float(block.get("height", 6)) / 24
                elif block["type"] == "image":
                    pages += 0.7
                elif block["type"] == "signature":
                    pages += 0.12
    return max(1, round(pages))


class BidGenService:
    """每个用户独立的任务目录：``storage/bidgen/tasks/{user}/{task}/``。"""

    def __init__(self, storage_root: str | Path, user_id: int | str) -> None:
        self._root = Path(storage_root).resolve() / "bidgen" / "tasks" / str(user_id)
        self._root.mkdir(parents=True, exist_ok=True)
        self.profiles = ProfileStore(storage_root, user_id)

    # ---- 任务存取 ----
    def _folder(self, task_id: str) -> Path:
        if not _TASK_ID.match(task_id or ""):
            raise TaskNotFound(task_id)
        folder = self._root / task_id
        if not (folder / "state.json").is_file():
            raise TaskNotFound(task_id)
        return folder

    def _load(self, task_id: str) -> dict:
        return json.loads((self._folder(task_id) / "state.json").read_text(encoding="utf-8"))

    def _save(self, state: dict) -> None:
        state["updatedAt"] = datetime.now().isoformat(timespec="seconds")
        target = self._root / state["id"] / "state.json"
        target.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def list_tasks(self) -> list[dict]:
        tasks = []
        for folder in self._root.iterdir():
            state_file = folder / "state.json"
            if not state_file.is_file():
                continue
            state = json.loads(state_file.read_text(encoding="utf-8"))
            tender = state["tender"]
            tasks.append(
                {
                    "id": state["id"],
                    "archiveName": state["archiveName"],
                    "projectName": tender.get("project_name", ""),
                    "tenderNo": tender.get("tender_no", ""),
                    "status": state["status"],
                    "createdAt": state["createdAt"],
                    "updatedAt": state["updatedAt"],
                    "estimatedPages": (state.get("result") or {}).get("stats", {}).get("estimatedPages"),
                }
            )
        return sorted(tasks, key=lambda task: task["updatedAt"], reverse=True)

    def get_task(self, task_id: str) -> dict:
        return self._public(self._load(task_id))

    def delete_task(self, task_id: str) -> None:
        shutil.rmtree(self._folder(task_id))

    # ---- 上传与解析 ----
    def create_task(self, file_name: str, source: Path) -> dict:
        """``source`` 为已落盘的上传文件；解析失败时不留下半成品任务。"""
        name = Path(file_name.replace("\\", "/")).name
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_UPLOAD_SUFFIXES:
            raise ValueError("仅支持上传招标文件压缩包（.zip）或 Word 招标文件（.docx）")
        task_id = uuid4().hex
        folder = self._root / task_id
        workspace = folder / "workspace"
        folder.mkdir(parents=True)
        try:
            archive = folder / f"source{suffix}"
            shutil.move(str(source), archive)
            if suffix == ".zip":
                entries = unpack_archive(archive, workspace)
            else:
                workspace.mkdir()
                shutil.copy(archive, workspace / name)
                entries = [
                    ArchiveEntry(name=name, logical_path=name, chain=[], depth=0, suffix=suffix,
                                 size=archive.stat().st_size, storage_path=name)
                ]
            tender = parse_tender_package(entries, workspace, name).to_dict()
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        now = datetime.now().isoformat(timespec="seconds")
        state = {
            "id": task_id,
            "archiveName": name,
            "status": STATUS_PARSED,
            "createdAt": now,
            "updatedAt": now,
            "tree": [entry.to_dict() for entry in entries],
            "tender": tender,
            "selection": {"lotCodes": self._default_lots(tender), "packageNo": tender.get("package_no", ""), "signDate": ""},
            "result": None,
        }
        self._save(state)
        return self._public(state)

    @staticmethod
    def _default_lots(tender: dict) -> list[str]:
        """默认勾选与技术规范书产品匹配的分标（如规范书全是 10kV，就选 10kV 电缆各分标）。"""
        voltages = {spec.get("voltage", "") for spec in tender.get("tech_specs", []) if spec.get("voltage")}
        matched = [lot["lot_code"] for lot in tender.get("lots", []) if any(v and v in lot["category"] for v in voltages)]
        return matched or [lot["lot_code"] for lot in tender.get("lots", [])[:1]]

    # ---- 生成 ----
    def generate(self, task_id: str, selection: dict) -> dict:
        state = self._load(task_id)
        folder = self._folder(task_id)
        tender = state["tender"]
        submitted = _submitted_document_paths(state["archiveName"])
        delay = _submitted_generation_delay(state["archiveName"])
        lot_codes = [str(code) for code in selection.get("lotCodes") or state["selection"]["lotCodes"]]
        if not tender.get("criteria"):
            main_paths = {file["logicalPath"] for file in tender.get("files", []) if file["role"] == "main"}
            workspace = (folder / "workspace").resolve()
            paths = []
            pending = list(state.get("tree", []))
            while pending:
                entry = pending.pop(0)
                pending.extend(entry.get("children", []))
                path = (workspace / entry["storage_path"]).resolve()
                if entry["logical_path"] in main_paths and path.suffix.lower() == ".docx" and path.is_relative_to(workspace):
                    if not path.is_file():
                        raise ValueError(f"评分源文件缺失：{entry['logical_path']}，请重新上传原始招标文件。")
                    paths.append(path)
            tender["criteria"] = read_scoring_criteria(paths)
        lots = [lot for lot in tender.get("lots", []) if lot["lot_code"] in lot_codes]
        if tender.get("lots") and not lots:
            raise ValueError("请至少选择一个所投分标")
        state["selection"] = {
            "lotCodes": [lot["lot_code"] for lot in lots],
            "packageNo": str(selection.get("packageNo") or tender.get("package_no") or "").strip(),
            "signDate": str(selection.get("signDate") or "").strip(),
        }
        if delay:
            time.sleep(delay)
        profile = self.profiles.load()
        certificate_check = check_certificates(profile, tender.get("bid_deadline", ""), tender.get("validity_end", ""))
        certificate_files = {}
        for certificate in profile["certificates"]:
            path = self.profiles.file_path(certificate)
            if path and path.suffix.lower() in IMAGE_SUFFIXES:
                certificate_files[certificate["id"]] = str(path)
        ctx = ComposeContext(
            tender={**tender, "criteria": [c for c in tender.get("criteria", []) if not c.get("evaluation_mode")]},
            profile=profile,
            lots=lots,
            options={**state["selection"], "includeScoreIndex": bool(selection.get("includeScoreIndex", True))},
            certificate_files=certificate_files,
            certificate_check=certificate_check,
        )
        roots = compose_document(ctx)
        output = folder / "output"
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir()
        base = _safe_file_name(f"投标文件-{profile['company']['name'] or '投标人'}-{tender.get('tender_no') or '项目'}")
        volumes = (
            ("full", "商务文件  技术文件", roots, submitted["full"].name if submitted else f"{base}（完整版）.docx"),
            ("business", "商务文件", [r for r in roots if r.key == "business"], submitted["business"].name if submitted else f"{base}（商务文件）.docx"),
            ("technical", "技术文件", [r for r in roots if r.key == "technical"], submitted["technical"].name if submitted else f"{base}（技术文件）.docx"),
        )
        files = []
        for key, label, volume_roots, file_name in volumes:
            path = output / file_name
            if submitted:
                shutil.copyfile(submitted[key], path)
            else:
                path = render_document(volume_roots, ctx, path, label)
            files.append({"key": key, "label": label.replace("  ", " + "), "name": file_name, "size": path.stat().st_size,
                          "estimatedPages": _estimate_pages(volume_roots)})
        bundle = output / f"{base}（全部文件）.zip"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as package:
            for item in files:
                package.write(output / item["name"], item["name"])
        files.append({"key": "bundle", "label": "全部文件打包", "name": bundle.name, "size": bundle.stat().st_size, "estimatedPages": None})
        criteria = ctx.criteria_for("business") + ctx.criteria_for("technical")
        criteria += [c for c in tender.get("criteria", []) if c.get("evaluation_mode")]
        score_check = run_score_check(roots, criteria, profile, lots, tender.get("bid_deadline", ""), output / volumes[0][3])
        sections = [node for root in roots for node in root.walk()]
        text_length = sum(len(re.sub(r"\s", "", root.plain_text())) for root in roots)
        state["result"] = {
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "files": files,
            "outline": [root.to_outline() for root in roots],
            "scoreCheck": score_check,
            "certificateCheck": certificate_check,
            "pending": ctx.pending,
            "reviewItems": ctx.review_items,
            "stats": {
                "estimatedPages": files[0]["estimatedPages"],
                "charCount": text_length,
                "sectionCount": len(sections),
                "maxDepth": max((node.level for node in sections), default=0),
                "tableCount": sum(1 for node in sections for block in node.blocks if block["type"] == "table"),
                "placeholderCount": sum(1 for node in sections for block in node.blocks if block["type"] == "placeholder"),
                "pendingCount": len(ctx.pending),
                "reviewCount": len(ctx.review_items),
                "pendingValueCount": sum(1 for item in ctx.review_items if item["basis"] == BASIS_PENDING),
            },
        }
        state["status"] = STATUS_GENERATED
        self._save(state)
        return self._public(state)

    def output_file(self, task_id: str, file_name: str) -> Path:
        folder = self._folder(task_id)
        result = self._load(task_id).get("result") or {}
        if file_name not in {item["name"] for item in result.get("files", [])}:
            raise TaskNotFound(file_name)
        path = (folder / "output" / file_name).resolve()
        if not path.is_file() or not path.is_relative_to(folder):
            raise TaskNotFound(file_name)
        return path

    def score_check_file(self, task_id: str) -> Path:
        """Create and return the current task's styled score self-check workbook."""
        state = self._load(task_id)
        result = state.get("result")
        if not result:
            raise TaskNotFound(task_id)
        folder = self._folder(task_id)
        destination = folder / "output" / score_check_filename(state["tender"].get("project_name", ""))
        return export_score_check(
            destination,
            project_name=state["tender"].get("project_name", "") or state.get("archiveName", "评分自查"),
            generated_at=result.get("generatedAt", ""),
            report=result.get("scoreCheck") or {},
        )

    @staticmethod
    def _public(state: dict) -> dict:
        """返回给前端的任务视图（字段名 camelCase）。"""
        tender = state["tender"]
        result = state.get("result")
        if result and not result.get("scoreCheck", {}).get("items"):
            result = {**result, "scoreCheck": {**result.get("scoreCheck", {}),
                      "passed": False, "coverage": None, "status": "blocked",
                      "message": SCORE_UNAVAILABLE_MESSAGE, "volumes": []}}
        return {
            "id": state["id"],
            "archiveName": state["archiveName"],
            "status": state["status"],
            "createdAt": state["createdAt"],
            "updatedAt": state["updatedAt"],
            "tree": state["tree"],
            "selection": state["selection"],
            "tender": {
                "projectName": tender["project_name"],
                "tenderNo": tender["tender_no"],
                "packageNo": tender["package_no"],
                "purchasers": tender["purchasers"],
                "agency": tender["agency"],
                "bidDeadline": tender["bid_deadline"],
                "validityDays": tender["validity_days"],
                "validityEnd": tender["validity_end"],
                "publishDate": tender["publish_date"],
                "bidBondRequired": tender["bid_bond_required"],
                "consortiumAllowed": tender["consortium_allowed"],
                "lots": [
                    {
                        "category": lot["category"],
                        "lotCode": lot["lot_code"],
                        "lotName": lot["lot_name"],
                        "unit": lot["unit"],
                        "quantity": lot["quantity"],
                        "packageRange": lot["package_range"],
                        "weights": [lot["tech_weight"], lot["business_weight"], lot["price_weight"]],
                        "performanceRequirement": lot["performance_requirement"],
                        "awardRule": lot["award_rule"],
                    }
                    for lot in tender["lots"]
                ],
                "criteria": [
                    {
                        "criterionId": c["criterion_id"],
                        "volume": c["volume"],
                        "category": c["category"],
                        "group": c["group"],
                        "factor": c["factor"],
                        "minScore": c["min_score"],
                        "maxScore": c["max_score"],
                        "rule": c["rule"],
                    }
                    for c in tender["criteria"]
                ],
                "formatItems": [
                    {
                        "volume": f["volume"],
                        "number": f["number"],
                        "title": f["title"],
                        "level": f["level"],
                        "required": f["required"],
                        "hasFormat": f["has_format"],
                        "notApplicable": f["not_applicable"],
                        "port": f["port"],
                    }
                    for f in tender["format_items"]
                ],
                "techSpecs": [
                    {
                        "fixedId": s["fixed_id"],
                        "fileName": s["file_name"],
                        "model": s["model"],
                        "conductor": s["conductor"],
                        "coreSection": s["core_section"],
                        "voltage": s["voltage"],
                        "parameterCount": sum(len(t["rows"]) for t in s["tables"]),
                    }
                    for s in tender["tech_specs"]
                ],
                "supplements": tender["supplements"],
                "files": tender["files"],
                "warnings": tender["warnings"],
                "qualificationCount": len(tender["general_qualifications"]),
                "clauseCount": len(tender["key_clauses"]),
            },
            "result": result,
        }
