"""AI 标书页从投标资料库读取资料的行为回归。"""

from datetime import date
from types import SimpleNamespace

from api.services.bidgen.profile import DEFAULT_PROFILE, DEMO_PROFILE_PATCH, ProfileStore, _merge


def document(doc_id="license1", *, title="营业执照", slot="license", fields=None,
             text="", path=None, role="company", owner="", **extra):
    import json
    return SimpleNamespace(
        public_id=doc_id, name=title, slot=slot, title=title, text=text, path=path,
        summary="FACTS:" + json.dumps({"role": role, "fields": [
            {"name": name, "value": value, "conf": 1} for name, value in (fields or {}).items()
        ]}, ensure_ascii=False),
        tags=[f"bidder:{owner}"] if owner else [], expire_at=extra.get("expire_at"),
        is_perpetual=extra.get("is_perpetual", False),
    )


def match(store, documents):
    from api.services.bidgen.library import match_library_documents
    return match_library_documents(store, documents)


def test_new_profile_and_empty_library_have_no_demo_values(tmp_path):
    store = ProfileStore(tmp_path, 1)
    result = match(store, [])
    assert result["profile"]["company"]["name"] == ""
    assert result["profile"]["equipment"] == []
    assert result["profile"]["certificates"] == []
    assert result["profile"]["service"]["responseHours"] == ""
    assert result["libraryMatch"]["documentCount"] == 0


def test_four_tabs_match_actual_fields_tables_and_certificate_file(tmp_path):
    scan = tmp_path / "original.png"
    scan.write_bytes(b"certificate scan")
    docs = [
        document(fields={"企业名称": "测试电缆有限公司", "统一社会信用代码": "91410181TEST1234567",
                         "注册地址": "测试路8号", "法定代表人": "王某", "注册资本": "5000万元"},
                 path=scan, is_perpetual=True),
        document("staff", title="人员与财务", slot="staff", text=(
            "员工总人数：120\n高级工程师人数：8\n服务热线：4000000000\n"
            "年度\t主营收入（万元）\t研发费（万元）\t研发费占比\n"
            "2025\t1234.5678\t80.1234\t6.49%")),
        document("equipment", title="生产设备清单", slot="equipment", text=(
            "设备名称\t数量\t单位\n交联生产线\t6\t条\n成缆设备\t12\t套")),
        document("patent", title="专利清单", slot="patents", text=(
            "专利名称\t专利号\t类型\n一种电缆\tZL20250001\t发明")),
        document("quality", title="质量管理体系认证证书", slot="iso9001", path=scan,
                 fields={"证书编号": "Q-2025-01"}, expire_at=date(2028, 6, 30)),
    ]
    result = match(ProfileStore(tmp_path, 1), docs)
    profile = result["profile"]
    assert profile["company"]["name"] == "测试电缆有限公司"
    assert profile["company"]["registeredCapital"] == "5000万元"
    assert profile["legalRep"]["name"] == "王某"
    assert profile["staff"]["total"] == "120"
    assert profile["staff"]["seniorEngineers"] == "8"
    assert profile["finance"] == [{"year": "2025", "revenue": "1234.5678", "rdExpense": "80.1234", "rdRatio": "6.49%"}]
    assert profile["equipment"] == [{"name": "交联生产线", "count": "6", "unit": "条"}, {"name": "成缆设备", "count": "12", "unit": "套"}]
    assert profile["patents"] == [{"name": "一种电缆", "number": "ZL20250001", "kind": "发明"}]
    certificate = next(c for c in profile["certificates"] if c["category"] == "quality_system")
    assert certificate["number"] == "Q-2025-01"
    assert certificate["validTo"] == "2028-06-30"
    assert certificate["libraryDocumentId"] == "quality"
    assert ProfileStore(tmp_path, 1).file_path(certificate).read_bytes() == scan.read_bytes()
    assert set(result["libraryMatch"]["groups"]) == {"base", "people", "capability", "certificates"}
    assert all(result["libraryMatch"]["groups"].values())


def test_rematch_updates_auto_values_preserves_manual_and_does_not_duplicate(tmp_path):
    store = ProfileStore(tmp_path, 1)
    docs = [document(fields={"公司名称": "甲公司", "联系电话": "111", "联系人": "张某"}),
            document("equipment", title="生产设备", slot="equipment", text="设备名称\t数量\t单位\n成缆机\t5\t台")]
    first = match(store, docs)["profile"]
    first["company"]["contact"] = "手工联系人"
    first["equipment"][0]["count"] = "7"
    store.save(first)
    docs[0] = document(fields={"公司名称": "甲公司", "联系电话": "222", "联系人": "李某"})
    docs[1].text = "设备名称\t数量\t单位\n成缆机\t6\t台\n挤出机\t3\t台"
    second = match(store, docs)["profile"]
    third = match(store, docs)["profile"]
    assert second == third
    assert second["company"]["phone"] == "222"
    assert second["company"]["contact"] == "手工联系人"
    assert second["equipment"] == [{"name": "成缆机", "count": "7", "unit": "台"}, {"name": "挤出机", "count": "3", "unit": "台"}]


def test_conflicts_and_other_company_are_not_silently_selected(tmp_path):
    store = ProfileStore(tmp_path, 1)
    saved = store.load()
    saved["company"]["name"] = "甲公司"
    store.save(saved)
    docs = [document("a", fields={"企业名称": "甲公司", "联系电话": "111"}),
            document("b", fields={"企业名称": "甲公司", "联系电话": "222"}),
            document("c", fields={"企业名称": "乙公司", "注册地址": "乙公司地址"})]
    result = match(store, docs)
    assert result["profile"]["company"]["phone"] == ""
    assert result["profile"]["company"]["address"] == ""
    assert result["libraryMatch"]["warnings"]


def test_person_phone_does_not_fill_company_phone(tmp_path):
    result = match(ProfileStore(tmp_path, 1), [document(
        title="法定代表人身份证", slot="legal_id", role="person/legal",
        fields={"姓名": "张某", "电话": "13800000000", "身份证号": "TEST-ID"},
    )])
    assert result["profile"]["legalRep"]["name"] == "张某"
    assert result["profile"]["legalRep"]["phone"] == "13800000000"
    assert result["profile"]["company"]["phone"] == ""


def test_real_library_agent_slot_maps_agent_not_legal_representative(tmp_path):
    scan = tmp_path / "agent.png"
    scan.write_bytes(b"agent scan")
    result = match(ProfileStore(tmp_path, 1), [document(
        title="授权代理人身份证及近半年社保缴纳证明", slot="id_agent", role="person/legal",
        fields={"姓名": "代理人甲", "身份证号": "AGENT-ID"}, path=scan,
    )])
    assert result["profile"]["agent"]["name"] == "代理人甲"
    assert result["profile"]["legalRep"]["name"] == ""
    assert result["profile"]["certificates"][0]["category"] == "agent_id"


def test_certificate_file_title_takes_precedence_over_general_library_group(tmp_path):
    scan = tmp_path / "environment.pdf"
    scan.write_bytes(b"environment scan")
    doc = document(title="ISO/质量管理体系认证证书", slot="iso", path=scan)
    doc.name = "ISO14001环境管理体系认证证书"
    result = match(ProfileStore(tmp_path, 1), [doc])
    assert result["profile"]["certificates"][0]["category"] == "environment_system"


def test_manual_uploaded_certificate_is_not_replaced_on_rematch(tmp_path, monkeypatch):
    scan = tmp_path / "original.png"
    scan.write_bytes(b"original")
    store = ProfileStore(tmp_path, 1)
    docs = [document(path=scan, is_perpetual=True)]
    profile = match(store, docs)["profile"]
    identifier = profile["certificates"][0]["id"]
    monkeypatch.setattr("api.services.bidgen.profile.recognize_validity", lambda _: None)
    store.attach_file(identifier, "manual.png", b"manually corrected scan")
    result = match(store, docs)
    certificate = result["profile"]["certificates"][0]
    assert certificate["fileName"] == "manual.png"
    assert store.file_path(certificate).read_bytes() == b"manually corrected scan"


def test_only_unmodified_legacy_demo_is_migrated(tmp_path):
    legacy = _merge(DEFAULT_PROFILE, DEMO_PROFILE_PATCH)
    store = ProfileStore(tmp_path, 1)
    store._write(legacy)
    assert match(store, [])["profile"]["company"]["name"] == ""
    legacy["company"]["contact"] = "已填写联系人"
    store._write(legacy)
    assert match(store, [])["profile"]["company"]["contact"] == "已填写联系人"


def test_removing_imported_certificate_never_deletes_library_original(tmp_path):
    scan = tmp_path / "original.pdf"
    scan.write_bytes(b"original file")
    store = ProfileStore(tmp_path, 1)
    profile = match(store, [document(path=scan, is_perpetual=True)])["profile"]
    assert len(profile["certificates"]) == 1
    profile["certificates"] = []
    store.save(profile)
    assert scan.read_bytes() == b"original file"


def test_manual_clear_is_preserved_on_rematch(tmp_path):
    store = ProfileStore(tmp_path, 1)
    docs = [document(fields={"联系电话": "111"})]
    profile = match(store, docs)["profile"]
    profile["company"]["phone"] = ""
    store.save(profile)
    assert match(store, docs)["profile"]["company"]["phone"] == ""


def test_different_people_are_never_combined(tmp_path):
    docs = [document("person1", title="法定代表人身份证", slot="id_legal", role="person/legal", fields={"姓名": "张三", "电话": "111"}),
            document("person2", title="法定代表人身份证", slot="id_legal", role="person/legal", fields={"姓名": "李四", "身份证号": "LI-ID"})]
    store = ProfileStore(tmp_path, 1)
    profile = match(store, docs)["profile"]
    assert profile["legalRep"] == {"name": "", "title": "", "phone": "", "idNumber": ""}
    profile["legalRep"]["name"] = "李四"
    store.save(profile)
    selected = match(store, docs)["profile"]
    assert selected["legalRep"]["idNumber"] == "LI-ID"
    assert selected["legalRep"]["phone"] == ""


def test_agent_id_back_without_name_is_kept_for_the_single_agent(tmp_path):
    front, back = tmp_path / "front.png", tmp_path / "back.png"
    front.write_bytes(b"front")
    back.write_bytes(b"back")
    docs = [document("front", title="授权代理人身份证", slot="id_agent", role="person/legal",
                     fields={"姓名": "代理人甲", "身份证号": "999999199001010001"}, path=front),
            document("back", title="授权代理人身份证", slot="id_agent", role="person/legal",
                     fields={"签发机关": "某县公安局", "有效期限": "2020.01.01-2040.01.01"}, path=back)]
    result = match(ProfileStore(tmp_path, 1), docs)
    agent_ids = [c["libraryDocumentId"] for c in result["profile"]["certificates"] if c["category"] == "agent_id"]
    assert sorted(agent_ids) == ["back", "front"]
    assert result["profile"]["agent"]["name"] == "代理人甲"


def test_unattributable_id_back_is_not_pasted_when_several_agents(tmp_path):
    scan = tmp_path / "scan.png"
    scan.write_bytes(b"scan")
    docs = [document("a", title="授权代理人身份证", slot="id_agent", role="person/legal", fields={"姓名": "甲某", "身份证号": "A-ID"}, path=scan),
            document("b", title="授权代理人身份证", slot="id_agent", role="person/legal", fields={"姓名": "乙某", "身份证号": "B-ID"}, path=scan),
            document("back", title="授权代理人身份证", slot="id_agent", role="person/legal", fields={"签发机关": "某县公安局"}, path=scan)]
    store = ProfileStore(tmp_path, 1)
    profile = store.load()
    profile["agent"]["name"] = "甲某"
    store.save(profile)
    result = match(store, docs)
    agent_ids = [c["libraryDocumentId"] for c in result["profile"]["certificates"] if c["category"] == "agent_id"]
    assert agent_ids == ["a"]
    assert any("无法确定属于哪一位" in warning for warning in result["libraryMatch"]["warnings"])


def test_multiple_certificates_keep_stable_distinct_ids(tmp_path):
    store = ProfileStore(tmp_path, 1)
    profile = store.load()
    profile["certificates"] = [{"id": "placeholder", "category": "business_license", "name": "营业执照"}]
    store.save(profile)
    scan = tmp_path / "license.png"
    scan.write_bytes(b"scan")
    docs = [document("cert1", fields={"证书编号": "ONE"}, path=scan),
            document("cert2", fields={"证书编号": "TWO"}, path=scan)]
    first = match(store, docs)["profile"]
    second = match(store, docs)["profile"]
    assert first == second
    assert len(first["certificates"]) == 2
    assert {item["number"] for item in first["certificates"]} == {"ONE", "TWO"}
    assert len({item["id"] for item in first["certificates"]}) == 2
    assert all(item["id"] and item["category"] for item in first["certificates"])


def test_read_only_library_loader_uses_page_records_and_cached_text(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from api.services.bidgen.library import load_library_documents
    from common.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))
    folder = tmp_path / "knowledge" / "tenderlib01"
    folder.mkdir(parents=True)
    (folder / "scan1.txt").write_text("企业名称：真实资料库公司", encoding="utf-8")
    base = SimpleNamespace(id=42, public_id="tenderlib01")
    parent = SimpleNamespace(public_id="parent", parent_id=None, name="营业执照", tags=["slot:license"],
                             file_key=None, storage_path=None, review_status="approved", summary="", status="ready")
    child = SimpleNamespace(public_id="scan1", parent_id="parent", name="执照扫描件", tags=[],
                            file_key=None, storage_path=None, review_status="approved", status="ready",
                            summary="", expire_at=None, is_perpetual=False)
    rejected = SimpleNamespace(**{**vars(child), "public_id": "rejected", "review_status": "rejected"})
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalar_one_or_none=lambda: base),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [parent, child, rejected])),
    ]))
    docs = asyncio.run(load_library_documents(db))
    assert len(docs) == 1
    assert docs[0].public_id == "scan1"
    assert docs[0].slot == "license"
    assert docs[0].text == "企业名称：真实资料库公司"
    assert len(db.execute.await_args_list) == 2


def test_company_profile_api_matches_the_same_library_records(tmp_path, monkeypatch):
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from api.deps import get_current_user
    from api.routers.bid_gen import router
    from common.config import get_settings
    from db.base import Base
    from db.models.knowledge import KnowledgeBase, KnowledgeDocument
    from db.session import get_db

    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))
    database = tmp_path / "library.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def seed():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            base = KnowledgeBase(public_id="tenderlib01", name="投标资料库", purpose="asset", status="active")
            db.add(base)
            await db.flush()
            source = document(fields={"企业名称": "页面资料库企业", "联系电话": "12345"})
            db.add(KnowledgeDocument(public_id="real-library-record", base_id=base.id, name="企业基本信息",
                                     summary=source.summary, status="ready", review_status="approved", tags=["slot:company"]))
            await db.commit()

    asyncio.run(seed())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_superuser=True, roles=[])

    async def session_override():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session_override
    with TestClient(app) as client:
        response = client.get("/bid-gen/profile")
        assert response.status_code == 200
        result = response.json()["data"]
        assert result["profile"]["company"]["name"] == "页面资料库企业"
        assert result["libraryMatch"]["documentCount"] == 1
        assert result["libraryMatch"]["sources"]["company.name"][0]["documentId"] == "real-library-record"
        profile = result["profile"]
        profile["company"]["contact"] = "用户补充"
        assert client.put("/bid-gen/profile", json=profile).status_code == 200
        assert client.get("/bid-gen/profile").json()["data"]["profile"]["company"]["contact"] == "用户补充"
    asyncio.run(engine.dispose())


def test_word_library_tables_can_be_matched_before_ocr_finishes(tmp_path, monkeypatch):
    from docx import Document
    from api.services.bidgen.library import _read_document
    from common.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))
    path = tmp_path / "finance.docx"
    word = Document()
    table = word.add_table(rows=2, cols=3)
    for index, text in enumerate(["年度", "主营业务收入（元）", "研发费用（元）"]):
        table.cell(0, index).text = text
    for index, text in enumerate(["2025", "12345678.90", "500000.12"]):
        table.cell(1, index).text = text
    word.save(path)
    record = SimpleNamespace(public_id="finance-record", name="2025财务报告", tags=["slot:finance"],
                             file_key="finance.docx", storage_path="finance.docx", summary="正在解析…")
    source = _read_document(record, None, "tenderlib01")
    result = match(ProfileStore(tmp_path, 1), [source])
    assert result["profile"]["finance"] == [{"year": "2025", "revenue": "1234.567890", "rdExpense": "50.000012"}]


# 人工录入的营业执照 OCR 版面：标签被拆成单字、标签与值之间夹着二维码文字、住所与经营范围折行
LICENSE_OCR = "\n".join([
    "营业执照", "统一社会信用代码", "扫描二维码登录", "91410103MA3XF5E664", "信息公示系统", "(副本)(1-1)",
    "名", "称", "河南测试计算机科技有限公司", "注册资本", "捌佰万圆整", "类", "型", "有限责任公司(自然人投资或控股)",
    "成立日期", "2016年11月15日", "法定代表人", "张某", "住", "所", "河南省郑州市管城回族区郑汴路",
    "76号绿都广场1单元23层2308号", "经营范围", "计算机软件开发，销售，售后服务，技术咨询，技术",
    "转让；计算机硬件及辅助设备的销售；通讯设备，办", "公用品销售。", "登记机关", "2025",
])


def test_license_ocr_layout_is_read_without_model(tmp_path):
    from api.services.bidgen.library_fields import extract_profile_fields

    patch = extract_profile_fields(fields=[], text=LICENSE_OCR, slot="license", title="营业执照", role="")
    assert patch["company"] == {
        "creditCode": "91410103MA3XF5E664",
        "name": "河南测试计算机科技有限公司",
        "registeredCapital": "捌佰万圆整",
        "orgStructure": "有限责任公司(自然人投资或控股)",
        "foundedDate": "2016-11-15",
        "address": "河南省郑州市管城回族区郑汴路76号绿都广场1单元23层2308号",
        "businessScope": "计算机软件开发，销售，售后服务，技术咨询，技术转让；计算机硬件及辅助设备的销售；通讯设备，办公用品销售。",
    }
    assert patch["legalRep"] == {"name": "张某"}
    # 截图里“地址”后面的菜单文字不是地址
    screenshot = extract_profile_fields(fields=[], text="地址\n四销售订单类\n客户名称", slot="safety", title="安全生产许可证", role="")
    assert "address" not in screenshot.get("company", {})


def test_other_company_license_is_shown_as_suggestion_not_filled(tmp_path):
    store = ProfileStore(tmp_path, 1)
    saved = store.load()
    saved["company"]["name"] = "河南测试信息技术有限公司"
    store.save(saved)
    license_doc = document("lic", title="营业执照", slot="license", text=LICENSE_OCR, role="")
    license_doc.summary = ""
    result = match(store, [license_doc])
    company = result["profile"]["company"]
    assert company["registeredCapital"] == "" and company["foundedDate"] == ""  # 主体不一致不自动填入
    suggestions = result["libraryMatch"]["suggestions"]
    assert suggestions["company.registeredCapital"][0]["value"] == "捌佰万圆整"
    assert suggestions["company.registeredCapital"][0]["owner"] == "河南测试计算机科技有限公司"
    assert suggestions["company.name"][0]["value"] == "河南测试计算机科技有限公司"
    assert any("河南测试计算机科技有限公司" in warning for warning in result["libraryMatch"]["warnings"])

    # 采用执照上的企业名称后重新匹配：同一主体，执照内容自动填入，候选值不再重复显示
    saved = store.load()
    saved["company"]["name"] = "河南测试计算机科技有限公司"
    store.save(saved)
    result = match(store, [license_doc])
    assert result["profile"]["company"]["registeredCapital"] == "捌佰万圆整"
    assert result["profile"]["company"]["foundedDate"] == "2016-11-15"
    assert "company.registeredCapital" not in result["libraryMatch"]["suggestions"]
