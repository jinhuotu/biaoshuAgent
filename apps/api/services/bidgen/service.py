"""标书生成任务：上传解析 → 生成 → 自查 → 下载，全部落在本地磁盘。"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from api.services.bidgen.analysis import analyze_tender, risk_check, write_report
from api.services.bidgen.archive import ARCHIVE_SUFFIXES, ArchiveEntry, unpack_archive
from api.services.bidgen.cable_knowledge import BASIS_PENDING
from api.services.bidgen.charts import add_charts
from api.services.bidgen.composer import compose_document, volume_of
from api.services.bidgen.corpus import CorpusIndex, CorpusStore
from api.services.bidgen.document_model import ComposeContext, Section
from api.services.bidgen.profile import IMAGE_SUFFIXES, ProfileStore, check_certificates
from api.services.bidgen.score_check import run_score_check
from api.services.bidgen.tender_parser import parse_tender_package
from api.services.bidgen.variation import content_digest
from api.services.bidgen.word_writer import render_document

STATUS_PARSED = "parsed"
STATUS_GENERATED = "generated"
ALLOWED_UPLOAD_SUFFIXES = (*ARCHIVE_SUFFIXES, ".docx", ".pdf")
UNSUPPORTED_UPLOAD_MESSAGE = "仅支持上传招标文件压缩包（.zip / .rar）、Word 招标文件（.docx）或 PDF 招标文件（.pdf）"
_TASK_ID = re.compile(r"^[0-9a-f]{32}$")
_CHARS_PER_PAGE = 560
_TABLE_ROWS_PER_PAGE = 24
_MAX_REROLLS = 5
# 解析规则版本：解析器（格式目录、评分、项目事实、需求结构）有改动时递增。
# 旧版本解析的任务在打开或生成时用任务目录里保留的原件自动重新解析，不需要重新上传。
PARSER_VERSION = 10  # 10：识别独立评分标准附件及物资品类权重


class TaskNotFound(LookupError):
    pass


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
        self.corpus = CorpusStore(storage_root, user_id)

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
        return self._public(self._refresh(self._load(task_id)))

    @staticmethod
    def _entry_from_dict(data: dict) -> ArchiveEntry:
        return ArchiveEntry(**{**data, "children": [BidGenService._entry_from_dict(child) for child in data.get("children", [])]})

    def _refresh(self, state: dict) -> dict:
        """解析规则升级后，用任务目录中的原件重新解析；原件缺失或解析失败时保留原结果。"""
        if state.get("parserVersion") == PARSER_VERSION:
            return state
        workspace = self._folder(state["id"]) / "workspace"
        if not state.get("tree") or not workspace.is_dir():
            return state  # 没有原件可重新解析
        try:
            entries = [self._entry_from_dict(item) for item in state["tree"]]
            tender = parse_tender_package(entries, workspace, state["archiveName"]).to_dict()
        except Exception:  # noqa: BLE001 - 旧任务的原件可能已被清理
            return state
        state["tender"] = tender
        codes = {lot["lot_code"] for lot in tender.get("lots", [])}
        kept = [code for code in state["selection"].get("lotCodes", []) if code in codes]
        state["selection"]["lotCodes"] = kept or self._default_lots(tender)
        state["parserVersion"] = PARSER_VERSION
        self._save(state)
        return state

    def delete_task(self, task_id: str) -> None:
        shutil.rmtree(self._folder(task_id))

    # ---- 上传与解析 ----
    def create_task(self, file_name: str, source: Path) -> dict:
        """``source`` 为已落盘的上传文件；解析失败时不留下半成品任务。"""
        name = Path(file_name.replace("\\", "/")).name
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_UPLOAD_SUFFIXES:
            raise ValueError(UNSUPPORTED_UPLOAD_MESSAGE)
        task_id = uuid4().hex
        folder = self._root / task_id
        workspace = folder / "workspace"
        folder.mkdir(parents=True)
        try:
            archive = folder / f"source{suffix}"
            shutil.move(str(source), archive)
            if suffix in ARCHIVE_SUFFIXES:
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
            "parserVersion": PARSER_VERSION,
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
        state = self._refresh(self._load(task_id))
        folder = self._folder(task_id)
        tender = state["tender"]
        if not tender.get("format_items") and not tender.get("lots") and not tender.get("tech_specs"):
            # 没识别出本文件的格式目录，也不是有分标 / 技术规范书的货物标：不能拿通用目录冒充本项目目录
            raise ValueError(
                f"未能从“{state['archiveName']}”中识别出投标（响应）文件格式章节，无法按本项目的目录生成。"
                "请确认上传的是招标 / 采购文件正文（含“投标文件格式”或“响应文件格式”章节）的 Word 版；"
                "如为 PDF 或扫描件，请另存为 .docx 后重新上传。"
            )
        lot_codes = [str(code) for code in selection.get("lotCodes") or state["selection"]["lotCodes"]]
        lots = [lot for lot in tender.get("lots", []) if lot["lot_code"] in lot_codes]
        if tender.get("lots") and not lots:
            raise ValueError("请至少选择一个所投分标")
        state["selection"] = {
            "lotCodes": [lot["lot_code"] for lot in lots],
            "packageNo": str(selection.get("packageNo") or tender.get("package_no") or "").strip(),
            "signDate": str(selection.get("signDate") or "").strip(),
        }
        profile = self.profiles.load()
        certificate_check = check_certificates(profile, tender.get("bid_deadline", ""), tender.get("validity_end", ""))
        certificate_files = {}
        for certificate in profile["certificates"]:
            path = self.profiles.file_path(certificate)
            if path and path.suffix.lower() in (*IMAGE_SUFFIXES, ".pdf"):
                certificate_files[certificate["id"]] = str(path)
        # 每次生成换一套措辞：同一招标文件重复生成时正文不雷同，事实数据不变
        previous_digest = (state.get("result") or {}).get("contentDigest", "")
        corpus_data = self.corpus.load()
        corpus_index = CorpusIndex(corpus_data) if corpus_data["sections"] else None
        for _attempt in range(_MAX_REROLLS):
            ctx = ComposeContext(
                tender=tender,
                profile=profile,
                lots=lots,
                options={**state["selection"], "includeScoreIndex": bool(selection.get("includeScoreIndex", True)),
                         "_corpus": corpus_index},
                certificate_files=certificate_files,
                certificate_check=certificate_check,
            )
            variant_seed = secrets.randbits(32)
            roots = compose_document(ctx, variant_seed)
            digest = content_digest(roots)
            if digest != previous_digest:
                break
        output = folder / "output"
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir()
        chart_count = add_charts(roots, output / "charts")  # 甘特图、架构图、组织机构图、流程图
        base = _safe_file_name(f"投标文件-{profile['company']['name'] or '投标人'}-{tender.get('tender_no') or '项目'}")
        volumes = (
            ("full", "商务文件  技术文件", roots, f"{base}（完整版）.docx"),
            ("business", "商务文件", [r for r in roots if volume_of(r) == "business"], f"{base}（商务文件）.docx"),
            ("technical", "技术文件", [r for r in roots if volume_of(r) == "technical"], f"{base}（技术文件）.docx"),
        )
        files = []
        for key, label, volume_roots, file_name in volumes:
            path = render_document(volume_roots, ctx, output / file_name, label)
            files.append({"key": key, "label": label.replace("  ", " + "), "name": file_name, "size": path.stat().st_size,
                          "estimatedPages": _estimate_pages(volume_roots)})
        analysis = analyze_tender(tender)
        risks = risk_check(tender, profile, roots, ctx, certificate_check)
        report_name = f"{base}（分析与风险自查报告）.docx"
        write_report(output / report_name, tender, analysis, risks, ctx.coverage)
        files.append({"key": "report", "label": "招标文件分析与风险自查报告", "name": report_name,
                      "size": (output / report_name).stat().st_size, "estimatedPages": None})
        bundle = output / f"{base}（全部文件）.zip"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as package:
            for item in files:
                package.write(output / item["name"], item["name"])
        files.append({"key": "bundle", "label": "全部文件打包", "name": bundle.name, "size": bundle.stat().st_size, "estimatedPages": None})
        criteria = ctx.criteria_for("business") + ctx.criteria_for("technical")
        score_check = run_score_check(roots, criteria, profile, lots, tender.get("bid_deadline", ""), output / volumes[0][3])
        sections = [node for root in roots for node in root.walk()]
        text_length = sum(len(re.sub(r"\s", "", root.plain_text())) for root in roots)
        state["result"] = {
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "variantSeed": variant_seed,
            "contentDigest": digest,
            "files": files,
            "outline": [root.to_outline() for root in roots],
            "scoreCheck": score_check,
            "certificateCheck": certificate_check,
            "pending": ctx.pending,
            "reviewItems": ctx.review_items,
            "coverage": ctx.coverage,
            "corpusHits": ctx.corpus_hits,
            "analysis": analysis,
            "riskCheck": risks,
            "stats": {
                "estimatedPages": files[0]["estimatedPages"],
                "charCount": text_length,
                "sectionCount": len(sections),
                "maxDepth": max(node.level for node in sections),
                "tableCount": sum(1 for node in sections for block in node.blocks if block["type"] == "table"),
                "placeholderCount": sum(1 for node in sections for block in node.blocks if block["type"] == "placeholder"),
                "pendingCount": len(ctx.pending),
                "reviewCount": len(ctx.review_items),
                "pendingValueCount": sum(1 for item in ctx.review_items if item["basis"] == BASIS_PENDING),
                "chartCount": chart_count,
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

    @staticmethod
    def _public(state: dict) -> dict:
        """返回给前端的任务视图（字段名 camelCase）。"""
        tender = state["tender"]
        result = state.get("result")
        score_check = result.get("scoreCheck") if result else None
        if score_check and not score_check.get("items"):
            # 已生成任务保留原产物；历史空自查不能因 all([]) 标记通过。
            result = {**result, "scoreCheck": {**score_check, "available": False, "passed": False, "readBack": False}}
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
                        "scoringCategory": lot.get("scoring_category", ""),
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
                        "sourcePath": c.get("source_path", ""),
                        "tiers": c.get("tiers", []),
                    }
                    for c in tender["criteria"]
                ],
                "scoringWeights": tender.get("scoring_weights", {}),
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
                        "submissionMode": f.get("submission_mode", ""),
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
