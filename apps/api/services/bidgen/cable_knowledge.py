"""电缆类技术参数“投标人保证值”推导规则。

来源分四级，生成后在核对清单中区分展示：
  copy     —— 招标方给了确定值，直接响应同值
  bound    —— 招标方给了上下限，响应同限值
  formula  —— 按公式由截面、材质计算
  typical  —— 行业典型值，必须由投标人按本厂工艺文件 / 型式试验报告核对
  pending  —— 无法推导，留待投标人填写
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

BASIS_COPY = "copy"
BASIS_BOUND = "bound"
BASIS_FORMULA = "formula"
BASIS_TYPICAL = "typical"
BASIS_PENDING = "pending"

BASIS_LABELS = {
    BASIS_COPY: "同招标要求值",
    BASIS_BOUND: "按招标限值响应",
    BASIS_FORMULA: "公式计算",
    BASIS_TYPICAL: "行业典型值（需核对）",
    BASIS_PENDING: "待投标人填写",
}

PENDING_TEXT = "【待填】"

# 紧压圆形导体外径近似值（mm），按标称截面
_CONDUCTOR_DIAMETER = {
    35: 7.0, 50: 8.3, 70: 10.0, 95: 11.6, 120: 13.0, 150: 14.6, 185: 16.2,
    240: 18.4, 300: 20.6, 400: 23.8, 500: 26.6, 630: 30.2,
}
# 8.7/15kV 三芯钢带铠装电缆近似外径（mm）
_CABLE_DIAMETER_3_CORE = {
    35: 51, 50: 54, 70: 58, 95: 62, 120: 66, 150: 69, 185: 73, 240: 79, 300: 84, 400: 92,
}
# 8.7/15kV 单芯电缆近似外径（mm）
_CABLE_DIAMETER_1_CORE = {
    70: 30, 95: 32, 120: 34, 150: 35, 185: 37, 240: 40, 300: 42, 400: 46, 500: 50, 630: 54,
}
_INNER_COVERING = {35: 1.2, 50: 1.3, 70: 1.4, 95: 1.5, 120: 1.5, 150: 1.6, 185: 1.6, 240: 1.7, 300: 1.8, 400: 1.9}
# 短路电流密度系数 K（A·s^0.5/mm²）：起始 90℃、终止 250℃
_SHORT_CIRCUIT_FACTOR = {"铜": 143.0, "铝": 94.0}

_OPERATORS = (
    (re.compile(r"^(?:大于等于|不小于|不低于|≥|>=)\s*"), "≥"),
    (re.compile(r"^(?:小于等于|不大于|不超过|≤|<=)\s*"), "≤"),
    (re.compile(r"^(?:大于|>)\s*"), ">"),
    (re.compile(r"^(?:小于|<)\s*"), "<"),
)
_TRAILING_NOTE = re.compile(r"\s*[（(][^（()）]*[)）]\s*$")
_BIDDER_SUPPLIED = re.compile(r"[（(]?\s*投标人(?:提供|填写)\s*[)）]?")


@dataclass
class CableFeatures:
    model: str = ""
    conductor: str = ""
    cores: int = 0
    section: int = 0
    voltage: str = ""

    @property
    def steel_tape_armoured(self) -> bool:
        return bool(re.search(r"2[23]$|6[23]$", self.model))

    @property
    def label(self) -> str:
        spec = f"{self.cores}×{self.section}" if self.cores and self.section else ""
        return " ".join(part for part in (self.model, self.voltage, spec) if part)


def parse_cable_features(model: str, conductor: str, core_section: str, voltage: str) -> CableFeatures:
    matched = re.search(r"(\d+)\s*[×xX*]\s*(\d+)", core_section or "")
    return CableFeatures(
        model=(model or "").strip(),
        conductor=(conductor or "").strip(),
        cores=int(matched.group(1)) if matched else 0,
        section=int(matched.group(2)) if matched else 0,
        voltage=(voltage or "").strip(),
    )


def _nearest(table: dict[int, float], section: int) -> float | None:
    if not section or not table:
        return None
    if section in table:
        return table[section]
    return None


def _format_number(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".") if value % 1 else str(int(value))


def _typical_value(name: str, features: CableFeatures) -> tuple[str, str] | None:
    """招标方要求“投标人提供”的参数：给出公式值或典型值。"""
    section, single_core = features.section, features.cores == 1
    if "导体外径" in name:
        value = _nearest(_CONDUCTOR_DIAMETER, section)
        return (_format_number(value), BASIS_TYPICAL) if value else None
    if "绕包半导电带" in name:
        return ("1×0.14", BASIS_TYPICAL) if section >= 500 else ("不适用（标称截面小于500mm²，不绕包）", BASIS_TYPICAL)
    if "挤包导体屏蔽平均厚度" in name or "绝缘屏蔽平均厚度" in name:
        return "0.8", BASIS_TYPICAL
    if "挤包导体屏蔽最薄点" in name or "绝缘屏蔽最薄点" in name:
        return "0.6", BASIS_TYPICAL
    if "铜丝" in name:
        return "不适用（采用铜带屏蔽）", BASIS_TYPICAL
    if "铜带宽度" in name:
        return "35", BASIS_TYPICAL
    if "铜带厚度" in name:
        return ("0.12" if single_core else "0.10"), BASIS_TYPICAL
    if "填充层材料" in name:
        return ("不适用（单芯电缆）" if single_core else "阻燃聚丙烯网状撕裂填充绳"), BASIS_TYPICAL
    if "隔离套材料" in name:
        return "聚氯乙烯（PVC）", BASIS_TYPICAL
    if "内衬层厚度" in name:
        value = _nearest(_INNER_COVERING, section)
        return (_format_number(value), BASIS_TYPICAL) if value else None
    if "钢带层数" in name:
        return ("2", BASIS_TYPICAL) if features.steel_tape_armoured else ("不适用", BASIS_TYPICAL)
    if "钢带宽度" in name:
        return ("45" if section <= 150 else "60"), BASIS_TYPICAL
    if "钢带厚度" in name:
        return ("0.5" if section < 240 else "0.8"), BASIS_TYPICAL
    if "钢丝" in name:
        return ("不适用（采用钢带铠装）", BASIS_TYPICAL) if features.steel_tape_armoured else None
    if "外护套颜色" in name:
        return "黑色", BASIS_TYPICAL
    if "外护套特殊要求" in name:
        return "无特殊要求，可按项目单位需求提供防白蚁、防鼠型护套", BASIS_TYPICAL
    if "电缆外径" in name:
        table = _CABLE_DIAMETER_1_CORE if single_core else _CABLE_DIAMETER_3_CORE
        value = _nearest(table, section)
        return (f"约{_format_number(value)}", BASIS_TYPICAL) if value else None
    if "铠装层外径" in name:
        table = _CABLE_DIAMETER_1_CORE if single_core else _CABLE_DIAMETER_3_CORE
        value = _nearest(table, section)
        sheath = 3.0 if single_core else 3.6
        return (f"约{_format_number(value - 2 * sheath)}", BASIS_TYPICAL) if value else None
    if "导体允许短路电流" in name:
        factor = _SHORT_CIRCUIT_FACTOR.get(features.conductor)
        seconds = re.search(r"(\d+(?:\.\d+)?)\s*s", name)
        if factor and section:
            duration = float(seconds.group(1)) if seconds else 1.0
            return f"{factor * section / math.sqrt(duration) / 1000:.1f}", BASIS_FORMULA
    return None


def derive_guarantee(name: str, required: str, features: CableFeatures) -> tuple[str, str]:
    """返回 (投标人保证值, 来源级别)。"""
    name = (name or "").lstrip("※△ ").strip()
    required = (required or "").strip()
    if not required or required in ("-", "—", "/"):
        return "—", BASIS_COPY
    if _BIDDER_SUPPLIED.search(required):
        typical = _typical_value(name, features)
        if typical is None:
            return PENDING_TEXT, BASIS_PENDING
        value, basis = typical
        prefix = _BIDDER_SUPPLIED.sub("", required).strip()
        operator = next((symbol for pattern, symbol in _OPERATORS if pattern.match(prefix)), "")
        if operator and re.match(r"^约?\d", value):
            value = f"{operator}{value.lstrip('约')}"
        return value, basis
    # “0.8（对于3×240mm2截面及以上）”这类带适用条件的要求值
    conditional = re.search(r"对于\s*\d+\s*[×xX*]\s*(\d+)\s*mm.{0,6}及以上", required)
    if conditional and features.section and features.section < int(conditional.group(1)):
        typical = _typical_value(name, features)
        return typical if typical else (PENDING_TEXT, BASIS_PENDING)
    value = required
    while _TRAILING_NOTE.search(value) and len(_TRAILING_NOTE.sub("", value)) >= 1:
        value = _TRAILING_NOTE.sub("", value)
    for pattern, symbol in _OPERATORS:
        if pattern.match(value):
            return f"{symbol}{pattern.sub('', value)}", BASIS_BOUND
    if re.fullmatch(r"-?\d+(?:\.\d+)?\s*[-~]\s*-?\d+(?:\.\d+)?", value):
        return value.replace("-", "~", 1) if not value.startswith("-") else value, BASIS_BOUND
    return value, BASIS_COPY


# 组件材料配置表：元件名称关键词 → 投标人响应的型式规格
_COMPONENT_RESPONSES = (
    ("交联聚乙烯绝缘电力电缆", "按 GB/T 12706.2-2020 生产的交联聚乙烯绝缘电力电缆，型号规格同技术参数特性表"),
    ("导体", "符合 GB/T 3956-2008 第2种紧压圆形绞合导体（铜：TR 型软圆铜线；铝：LY4/LY6 型硬圆铝线）"),
    ("绝缘料", "10kV 级过氧化物交联聚乙烯绝缘料（XLPE），符合 JB/T 10437-2004"),
    ("半导电屏蔽料", "过氧化物交联型半导电屏蔽料（导体屏蔽、可剥离绝缘屏蔽），符合 JB/T 10738-2007"),
    ("铜带", "软铜带（T2），符合 GB/T 11091-2014，重叠绕包"),
    ("钢带", "镀锌钢带，符合 YB/T 024-2021，双层间隙绕包；单芯电缆采用非磁性不锈钢带"),
    ("PVC", "聚氯乙烯护套料（ST2 型，阻燃），符合 GB/T 8815-2008"),
    ("PE", "聚乙烯护套料（ST7 型），符合 GB/T 15065-2009"),
)


def derive_component_response(component_name: str) -> str:
    for keyword, response in _COMPONENT_RESPONSES:
        if keyword in component_name:
            return response
    return PENDING_TEXT
