"""投标人企业资料与证件库：本地 JSON 存储，证件有效期校验。"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from common.config import get_settings

CERT_STATUS_VALID = "valid"
CERT_STATUS_EXPIRING = "expiring"
CERT_STATUS_EXPIRED = "expired"
CERT_STATUS_INSUFFICIENT = "insufficient"
CERT_STATUS_UNKNOWN = "unknown"

CERT_STATUS_LABELS = {
    CERT_STATUS_VALID: "有效",
    CERT_STATUS_EXPIRING: "即将到期",
    CERT_STATUS_EXPIRED: "已过期",
    CERT_STATUS_INSUFFICIENT: "不能覆盖投标有效期",
    CERT_STATUS_UNKNOWN: "未登记有效期",
}

# 证件类别：key → (名称, 是否投标必备)
CERT_CATEGORIES = {
    "business_license": ("营业执照", True),
    "legal_id": ("法定代表人身份证", True),
    "agent_id": ("委托代理人身份证", True),
    "tax": ("税务登记证明", False),
    "quality_system": ("质量管理体系认证证书", False),
    "environment_system": ("环境管理体系认证证书", False),
    "safety_system": ("职业健康安全管理体系认证证书", False),
    "energy_system": ("能源管理体系认证证书", False),
    "high_tech": ("高新技术企业证书", False),
    "specialized": ("专精特新企业证书", False),
    "green_factory": ("绿色工厂认证", False),
    "green_supply_chain": ("绿色供应链管理企业认证", False),
    "carbon_footprint": ("产品碳足迹证书", False),
    "green_power": ("绿色电力证书", False),
    "production_license": ("全国工业产品生产许可证", False),
    "ccc": ("CCC 强制性产品认证证书", False),
    "type_test": ("型式试验报告", False),
    "prequalification": ("资格预审结果通知书", False),
    "other": ("其他证明材料", False),
}

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".gif")
_DATE_PATTERN = re.compile(r"(\d{4})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})")

DEFAULT_PROFILE: dict = {
    "company": {
        "name": "",
        "creditCode": "",
        "address": "",
        "postcode": "",
        "contact": "",
        "phone": "",
        "fax": "",
        "website": "",
        "email": "",
        "orgStructure": "",
        "foundedDate": "",
        "registeredCapital": "",
        "qualificationLevel": "",
        "bank": "",
        "bankAccount": "",
        "businessScope": "",
        "intro": "",
        "honors": "",
    },
    "legalRep": {"name": "", "title": "", "phone": "", "idNumber": ""},
    "techLead": {"name": "", "title": "", "phone": ""},
    "agent": {"name": "", "title": "", "phone": "", "idNumber": ""},
    "staff": {"total": "", "projectManagers": "", "senior": "", "middle": "", "junior": "", "technicians": "", "seniorEngineers": ""},
    "shareholders": [],
    "finance": [],
    "equipment": [],
    "testEquipment": [],
    "performances": [],
    "patents": [],
    "standards": [],
    "awards": [],
    "systems": [],
    "service": {"provinceCount": "", "provinces": "", "hotline": "", "responseHours": "", "arrivalHours": ""},
    "declarations": {
        "greenPlan": False,
        "innovationIncentive": False,
        "supplyChainMeasures": False,
        "emergencyProof": False,
        "greenRecycling": False,
        "esgReport": False,
        "eiaReport": False,
        "wasteReport": False,
        "processDocsComplete": False,
        "materialDocsComplete": False,
        "noAdverseRecord": False,
        "noStateGridRelation": False,
        "nameChanged": False,
    },
    "certificates": [],
}

# 仅用于识别旧版未编辑的演示档案，新建资料不得使用这些值。
DEMO_PROFILE_PATCH: dict = {
    "company": {
        "name": "人民电缆集团有限公司",
        "creditCode": "914101816634362943",
        "address": "河南省郑州市巩义市永定东路5号",
        "postcode": "452100",
        "website": "www.rmcable.com",
        "orgStructure": "有限责任公司（其他有限责任公司），下辖五个核心子公司和两个控股子公司",
        "foundedDate": "2007-06-25",
        "registeredCapital": "32800万元人民币",
        "businessScope": "许可项目：电线、电缆制造。一般项目：金属材料制造，有色金属压延加工，配电开关控制设备制造，"
        "电力电子元器件制造，塑料制品制造，电线电缆经营，货物进出口，技术进出口等（详见营业执照）。",
        "intro": "公司成立于2007年6月，是以电线电缆为主业，集研发、制造、销售为一体的全国无区域性高新技术企业，"
        "拥有覆盖全国的营销服务中心和1000多个销售网点，年生产能力150多万公里。主要产品为35kV及以下交联聚乙烯绝缘电力电缆、"
        "低烟无卤环保电缆、矿物质绝缘防火电缆、架空绝缘电缆、控制电缆等100多个品种、6000多个规格；"
        "拥有生产设备120余台、检测设备50余套（台），中高级职称人员30余人。",
        "honors": "国家级专精特新“小巨人”企业、国家高新技术企业、国家级绿色工厂、国家知识产权优势企业、"
        "河南省技术创新示范企业、省级企业技术中心；通过CCC认证及ISO9001、ISO14001、ISO45001体系认证；"
        "拥有国家专利124项，参与制定国家标准5项、团体标准2项、地方标准7项。",
    },
    "legalRep": {"name": "曹明举", "title": "高级工程师"},
    "shareholders": [
        {"name": "河南仁民智盛管理有限公司", "amount": "22800", "ratio": "69.51", "kind": "企业法人"},
        {"name": "曹明举", "amount": "6000", "ratio": "18.29", "kind": "自然人"},
        {"name": "周电茹", "amount": "4000", "ratio": "12.20", "kind": "自然人"},
    ],
    "equipment": [
        {"name": "导体绞制设备", "count": "", "unit": "套"},
        {"name": "塑料挤出设备", "count": "", "unit": "套"},
        {"name": "成缆设备", "count": "", "unit": "套"},
        {"name": "交联生产线", "count": "", "unit": "条"},
        {"name": "带材绕包设备", "count": "", "unit": "套"},
        {"name": "成铠设备", "count": "", "unit": "套"},
    ],
    "systems": ["MES生产管理系统", "智能仓储系统"],
    "service": {"provinceCount": "", "provinces": "", "hotline": "", "responseHours": "2", "arrivalHours": "24"},
    "declarations": {
        "greenPlan": True,
        "innovationIncentive": True,
        "supplyChainMeasures": True,
        "emergencyProof": True,
        "greenRecycling": True,
        "processDocsComplete": True,
        "materialDocsComplete": True,
        "noAdverseRecord": True,
        "noStateGridRelation": True,
    },
    "certificates": [
        {"category": "business_license", "name": "营业执照"},
        {"category": "legal_id", "name": "法定代表人身份证（曹明举）"},
        {"category": "agent_id", "name": "委托代理人身份证"},
        {"category": "quality_system", "name": "ISO9001:2015 质量管理体系认证证书"},
        {"category": "environment_system", "name": "ISO14001:2015 环境管理体系认证证书"},
        {"category": "safety_system", "name": "ISO45001:2018 职业健康安全管理体系认证证书"},
        {"category": "high_tech", "name": "国家高新技术企业证书"},
        {"category": "specialized", "name": "国家级专精特新“小巨人”企业证书"},
        {"category": "green_factory", "name": "国家级绿色工厂"},
        {"category": "production_license", "name": "全国工业产品生产许可证（电线电缆）"},
        {"category": "ccc", "name": "CCC 强制性产品认证证书"},
        {"category": "type_test", "name": "10kV 电力电缆型式试验报告"},
        {"category": "prequalification", "name": "资格预审结果通知书"},
    ],
}


def _merge(base: dict, patch: dict) -> dict:
    """按默认结构深度合并，丢弃未知字段类型不一致的值。"""
    result = deepcopy(base)
    for key, value in (patch or {}).items():
        if key not in result:
            continue
        if isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        elif isinstance(result[key], list) and isinstance(value, list):
            result[key] = deepcopy(value)
        elif isinstance(result[key], bool):
            result[key] = bool(value)
        elif isinstance(result[key], str) and isinstance(value, str | int | float):
            result[key] = str(value).strip()
    return result


def normalize_certificate(raw: dict) -> dict:
    category = str(raw.get("category") or "other")
    if category not in CERT_CATEGORIES:
        category = "other"
    identifier = str(raw.get("id") or "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", identifier):
        identifier = uuid4().hex[:12]
    return {
        "id": identifier,
        "category": category,
        "name": str(raw.get("name") or CERT_CATEGORIES[category][0]).strip(),
        "number": str(raw.get("number") or "").strip(),
        "issuer": str(raw.get("issuer") or "").strip(),
        "validFrom": normalize_date(raw.get("validFrom")),
        "validTo": normalize_date(raw.get("validTo")),
        "perpetual": bool(raw.get("perpetual")),
        "fileName": str(raw.get("fileName") or ""),
        "storedFile": str(raw.get("storedFile") or ""),
        "libraryDocumentId": str(raw.get("libraryDocumentId") or ""),
        "librarySource": str(raw.get("librarySource") or ""),
    }


def normalize_date(value: object) -> str:
    matched = _DATE_PATTERN.search(str(value or ""))
    if not matched:
        return ""
    try:
        return date(int(matched.group(1)), int(matched.group(2)), int(matched.group(3))).isoformat()
    except ValueError:
        return ""


class ProfileStore:
    """每个用户一份企业资料；证件扫描件与 JSON 同目录保存。"""

    def __init__(self, storage_root: str | Path, user_id: int | str) -> None:
        self.folder = Path(storage_root).resolve() / "bidgen" / "profiles" / str(user_id)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._file = self.folder / "profile.json"

    def load(self) -> dict:
        if not self._file.is_file():
            profile = deepcopy(DEFAULT_PROFILE)
            self._write(profile)
            return profile
        profile = _merge(DEFAULT_PROFILE, json.loads(self._file.read_text(encoding="utf-8")))
        profile["certificates"] = [normalize_certificate(c) for c in profile["certificates"]]
        # 仅整份档案仍与旧演示完全相同时迁移；任何人工填写都保留。
        legacy = _merge(DEFAULT_PROFILE, DEMO_PROFILE_PATCH)
        legacy["certificates"] = [normalize_certificate(c) for c in legacy["certificates"]]
        def without_ids(value: dict) -> dict:
            result = deepcopy(value)
            for certificate in result["certificates"]:
                certificate.pop("id", None)
            return result
        if without_ids(profile) == without_ids(legacy):
            backup = self.folder / "legacy-demo-profile.json"
            if not backup.exists():
                backup.write_bytes(self._file.read_bytes())
            profile = deepcopy(DEFAULT_PROFILE)
            self._write(profile)
        return profile

    def save(self, patch: dict) -> dict:
        current = self.load()
        stored_files = {c["id"]: c for c in current["certificates"]}
        profile = _merge(DEFAULT_PROFILE, patch)
        certificates = []
        for raw in profile["certificates"]:
            certificate = normalize_certificate(raw)
            # 扫描件只能通过上传接口变更，不信任前端回传的存储文件名
            previous = stored_files.get(certificate["id"], {})
            certificate["storedFile"] = previous.get("storedFile", "")
            certificate["fileName"] = previous.get("fileName", "")
            certificate["libraryDocumentId"] = previous.get("libraryDocumentId", "")
            certificate["librarySource"] = previous.get("librarySource", "")
            certificates.append(certificate)
        kept = {c["storedFile"] for c in certificates if c["storedFile"]}
        for previous in stored_files.values():
            if previous["storedFile"] and previous["storedFile"] not in kept:
                (self.folder / previous["storedFile"]).unlink(missing_ok=True)
        profile["certificates"] = certificates
        self._write(profile)
        return profile

    def attach_file(self, certificate_id: str, file_name: str, content: bytes) -> dict:
        profile = self.load()
        certificate = next((c for c in profile["certificates"] if c["id"] == certificate_id), None)
        if certificate is None:
            raise KeyError("证件不存在，请先保存资料后再上传扫描件")
        suffix = Path(file_name).suffix.lower()
        if suffix not in (*IMAGE_SUFFIXES, ".pdf"):
            raise ValueError("扫描件仅支持 PNG / JPG / BMP / GIF 图片或 PDF")
        if certificate["storedFile"]:
            (self.folder / certificate["storedFile"]).unlink(missing_ok=True)
        stored = f"{certificate_id}_{uuid4().hex[:8]}{suffix}"
        (self.folder / stored).write_bytes(content)
        certificate["storedFile"] = stored
        certificate["fileName"] = Path(file_name.replace("\\", "/")).name
        certificate["libraryDocumentId"] = ""
        certificate["librarySource"] = ""
        recognized = recognize_validity(self.folder / stored)
        if recognized and not certificate["validTo"] and not certificate["perpetual"]:
            certificate.update(recognized)
        self._write(profile)
        return {"profile": profile, "recognized": recognized}

    def file_path(self, certificate: dict) -> Path | None:
        if not certificate.get("storedFile"):
            return None
        path = (self.folder / certificate["storedFile"]).resolve()
        return path if path.is_file() and path.is_relative_to(self.folder) else None

    def _write(self, profile: dict) -> None:
        self._file.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")


def recognize_validity(path: Path) -> dict | None:
    """尝试从扫描件识别有效期：PDF 读文字层，图片走本机 OCR（未安装则跳过）。"""
    text = ""
    try:
        if path.suffix.lower() == ".pdf":
            from pypdf import PdfReader

            text = "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages[:3])
        else:
            from rapidocr import RapidOCR  # 本机离线 OCR，可选依赖

            result = RapidOCR()(str(path))
            text = "\n".join(getattr(result, "txts", None) or [])
    except Exception:  # noqa: BLE001 - 识别是增强能力，失败时退回人工登记
        return None
    return parse_validity_text(text)


def parse_validity_text(text: str) -> dict | None:
    """从证件文字中取有效期。支持“长期”“2020.01.01-2040.01.01”“有效期至 2027年3月1日”。"""
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return None
    scope = re.search(r"(?:有效期限?|营业期限|有效期至|有效日期)[:：]?(.{0,40})", compact)
    target = scope.group(1) if scope else compact
    dates = [normalize_date("-".join(found)) for found in _DATE_PATTERN.findall(target)]
    dates = [found for found in dates if found]
    if "长期" in target or "永久" in target:
        return {"validFrom": dates[0] if dates else "", "validTo": "", "perpetual": True}
    if not scope or not dates:
        return None
    return {"validFrom": dates[0] if len(dates) > 1 else "", "validTo": dates[-1], "perpetual": False}


def check_certificates(profile: dict, bid_deadline: str, validity_end: str, warn_days: int | None = None) -> dict:
    """逐证件校验有效期，并检查必备证件是否齐全。

    判定基准：今天、投标截止日、投标有效期届满日。
    """
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if warn_days is None:
        settings = get_settings()
        warn_days = max(settings.expire_warn_days, settings.expire_danger_days)
    deadline = datetime.fromisoformat(bid_deadline).date() if bid_deadline else None
    validity = date.fromisoformat(validity_end) if validity_end else None
    items = []
    for certificate in profile.get("certificates", []):
        label, required = CERT_CATEGORIES.get(certificate["category"], CERT_CATEGORIES["other"])
        status, message, days_left = CERT_STATUS_VALID, "长期有效", None
        if not certificate["perpetual"]:
            if not certificate["validTo"]:
                status, message = CERT_STATUS_UNKNOWN, "未登记有效期，无法校验，请补录“有效期至”或勾选长期有效"
            else:
                valid_to = date.fromisoformat(certificate["validTo"])
                days_left = (valid_to - today).days
                if valid_to < today:
                    status, message = CERT_STATUS_EXPIRED, f"已于 {valid_to} 过期（已过期 {-days_left} 天）"
                elif deadline and valid_to < deadline:
                    status, message = CERT_STATUS_EXPIRED, f"{valid_to} 到期，早于投标截止日 {deadline}，开标时已失效"
                elif validity and valid_to < validity:
                    status = CERT_STATUS_INSUFFICIENT
                    message = f"{valid_to} 到期，早于投标有效期届满日 {validity}，建议提前换证或附续期说明"
                elif days_left <= warn_days:
                    status, message = CERT_STATUS_EXPIRING, f"{valid_to} 到期，剩余 {days_left} 天"
                else:
                    message = f"有效期至 {valid_to}，剩余 {days_left} 天"
        items.append(
            {
                "id": certificate["id"],
                "category": certificate["category"],
                "categoryLabel": label,
                "name": certificate["name"],
                "required": required,
                "validTo": certificate["validTo"],
                "perpetual": certificate["perpetual"],
                "hasFile": bool(certificate["storedFile"]),
                "status": status,
                "statusLabel": CERT_STATUS_LABELS[status],
                "message": message,
                "daysLeft": days_left,
            }
        )
    present = {item["category"] for item in items}
    missing = [
        {"category": key, "categoryLabel": label}
        for key, (label, required) in CERT_CATEGORIES.items()
        if required and key not in present
    ]
    blocking = [i for i in items if i["status"] == CERT_STATUS_EXPIRED]
    summary = {status: sum(1 for i in items if i["status"] == status) for status in CERT_STATUS_LABELS}
    return {
        "checkedAt": datetime.now().isoformat(timespec="seconds"),
        "bidDeadline": bid_deadline,
        "validityEnd": validity_end,
        "items": items,
        "missing": missing,
        "summary": summary,
        "passed": not blocking and not missing,
    }


def usable_certificates(profile: dict, bid_deadline: str = "") -> list[dict]:
    """投标截止日仍有效（或未登记有效期）的证件；过期证件不计入评分支撑。"""
    deadline = datetime.fromisoformat(bid_deadline).date() if bid_deadline else date.today()
    result = []
    for certificate in profile.get("certificates", []):
        if certificate["perpetual"] or not certificate["validTo"]:
            result.append(certificate)
        elif date.fromisoformat(certificate["validTo"]) >= max(deadline, date.today()):
            result.append(certificate)
    return result
