from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from api.services.quotes.assemble import flatten_bom
from api.services.quotes.contract import default_rates_dict
from api.services.quotes.packs import load_pack
from api.services.quotes.packs import loader
from api.services.quotes.verify import verify_lines
from common.errors import AppError


def test_invalid_pack_reports_field_path(monkeypatch) -> None:
    tmp_path = Path("tmp") / f"bad-pack-{uuid.uuid4().hex[:8]}"
    tmp_path.mkdir(parents=True, exist_ok=True)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"id": "bad", "units": []}), encoding="utf-8")
    monkeypatch.setattr(loader, "_PACK_DIR", tmp_path)
    with pytest.raises(AppError, match="name|units"):
        load_pack("bad")


def test_default_synonyms_drive_display_name_and_unit() -> None:
    lines, _ = flatten_bom({"chargers": [{"code": "dc_160kw", "qty": 2}]})
    assert lines[0]["name"] == "160kW直流充电桩"
    assert lines[0]["unit"] == "台"


def test_spec_patterns_report_generic_conflict() -> None:
    report = verify_lines(
        [
            {"name": "设备", "spec": "10kW", "unit": "台", "qty": 1},
            {"name": "设备", "spec": "20kW", "unit": "台", "qty": 1},
        ]
    )
    assert any(issue["code"] == "spec_conflict" for issue in report["issues"])


def test_fee_defaults_are_loaded_from_pack() -> None:
    rates = default_rates_dict("default_charger")
    assert rates["measureRate"] == 0.03
    assert rates["manageRate"] == 0.05
    assert rates["taxRate"] == 0.13


def test_missing_pack_falls_back_with_warning() -> None:
    warnings: list[str] = []
    rates = default_rates_dict("missing_pack", warnings)
    assert rates["measureRate"] == 0.03
    assert warnings and "回落" in warnings[0]


def test_unit_alias_is_normalized() -> None:
    pack = load_pack("default_charger")
    assert pack.normalize_unit("台套") == "套"
    assert pack.normalize_unit("平方米") == "m2"


def test_demo_pack_works_without_python_changes() -> None:
    lines, warnings = flatten_bom(
        {"items": [{"code": "paint", "name": "乳胶漆", "unit": "平方米", "qty": 12}]},
        pack_id="demo_decoration",
    )
    assert not warnings
    assert lines[0]["name"] == "乳胶漆"
    assert lines[0]["unit"] == "m2"
