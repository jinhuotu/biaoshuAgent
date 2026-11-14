from api.services.bidgen.library_fields import extract_profile_fields


def test_transposed_finance_table_creates_one_row_per_year():
    text = "\n".join(
        [
            "项目 | 2023年末 | 2024年度 | 2025年",
            "主营业务收入 | 182340.60 | 198762.10 | 221863.30",
            "资产负债率 | 47.61% | 47.28% | 45.81%",
        ]
    )

    result = extract_profile_fields(fields=[], text=text, slot="finance", title="财务状况表", role="")

    assert result["finance"] == [
        {"year": "2023", "revenue": "182340.60"},
        {"year": "2024", "revenue": "198762.10"},
        {"year": "2025", "revenue": "221863.30"},
        {"year": "2023", "debtRatio": "47.61%"},
        {"year": "2024", "debtRatio": "47.28%"},
        {"year": "2025", "debtRatio": "45.81%"},
    ]


def test_finance_prefers_main_business_revenue_over_total_revenue():
    text = "\n".join(
        [
            "科目 | 2023年度 | 2024年度",
            "营业收入 | 186512.40 | 203184.75",
            "主营业务收入 | 182340.60 | 198762.10",
        ]
    )

    result = extract_profile_fields(fields=[], text=text, slot="finance", title="财务信息", role="")

    assert result["finance"] == [
        {"year": "2023", "revenue": "182340.60"},
        {"year": "2024", "revenue": "198762.10"},
    ]


def test_service_network_does_not_treat_header_pairs_as_values():
    text = "\n".join(
        [
            "序号 | 服务网点 | 覆盖区域 | 地址 | 服务电话 | 常驻人员",
            "1 | 郑州服务中心 | 河南全省 | 郑州 | 0371-6789 | 18",
            "2 | 武汉服务站 | 湖北全省 | 武汉 | 027-8566 | 6",
            "2小时内响应，24小时内到达现场",
        ]
    )

    result = extract_profile_fields(fields=[], text=text, slot="service", title="售后服务保障及承诺", role="")

    assert result["service"] == {
        "provinces": "河南全省、湖北全省",
        "provinceCount": "2",
        "hotline": "0371-6789、027-8566",
        "responseHours": "2",
        "arrivalHours": "24",
    }


def test_four_column_company_table_keeps_both_label_value_pairs():
    result = extract_profile_fields(
        fields=[],
        text="注册地址 | 河南省郑州市高新区 | 邮政编码 | 450001",
        slot="company",
        title="投标人基本情况表",
        role="company",
    )

    assert result["company"] == {
        "address": "河南省郑州市高新区",
        "postcode": "450001",
    }


def test_explicit制度_titles_fill_declaration_flags():
    result = extract_profile_fields(
        fields=[],
        text="",
        slot="",
        title="供应链保障措施及风险应对预案",
        role="",
    )

    assert result == {"declarations": {"supplyChainMeasures": True}}


def test_finance_merge_prefers_audited_financial_statement():
    import shutil
    from tempfile import mkdtemp
    from types import SimpleNamespace
    from pathlib import Path
    from copy import deepcopy

    from api.services.bidgen.library import match_library_documents
    from api.services.bidgen.profile import DEFAULT_PROFILE

    def source(doc_id, name, title, text):
        return SimpleNamespace(
            public_id=doc_id,
            name=name,
            title=title,
            slot="finance" if "财务状况表" in name else "",
            summary="",
            text=text,
            path=None,
            expire_at=None,
            is_perpetual=False,
        )

    explanation = source(
        "explanation",
        "B07_财务信息说明",
        "财务信息与股权结构说明书",
        "科目 | 2023年度 | 2024年度\n主营业务收入 | 182340.60 | 198762.10",
    )
    audited = source(
        "audited",
        "B08_财务状况表",
        "近三年财务审计报告 / 完税证明",
        "科目 | 2023年度 | 2024年度\n营业收入 | 186512.40 | 203184.75",
    )

    class Store:
        def __init__(self, folder):
            self.folder = Path(folder)
            self.folder.mkdir(parents=True, exist_ok=True)
            self.profile = deepcopy(DEFAULT_PROFILE)

        def load(self):
            return deepcopy(self.profile)

        def _write(self, profile):
            self.profile = deepcopy(profile)

    folder = Path("storage") / "bidgen-library-test"
    try:
        result = match_library_documents(Store(folder), [explanation, audited])
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    assert result["profile"]["finance"] == [
        {"year": "2023", "revenue": "186512.40"},
        {"year": "2024", "revenue": "203184.75"},
    ]
    assert not any("finance /" in warning for warning in result["libraryMatch"]["warnings"])
