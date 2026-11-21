"""Independent cases for chat scope selection, evidence limits and SSE behavior."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api.routers import ai
from api.services.ai.knowledge_routing import (
    COMPANY_LIBRARY_MISMATCH,
    COMPANY_QUESTION,
    NO_SCOPE,
    PROJECT_QUESTION,
    KnowledgeRoute,
    _scope,
    bounded_evidence,
    resolve_knowledge_route,
)

COMPANIES = [
    ("tenderco-a", ["南河电缆有限公司", "南河", "91440300123456789A"]),
    ("tenderco-b", ["北川电缆有限公司", "北川"]),
    ("tenderco-c", ["南河光电有限公司", "南河光电"]),
]
GENERAL = [
    ("policy", "员工制度库", "员工请假、差旅报销", True),
    ("project-a", "东区改造项目库", "招标文件", True),
    ("project-b", "西区布线项目库", "招标文件", True),
    ("internal", "内部资料库", "通用资料", True),
    ("restricted", "经营档案库", "经营数据", False),
]


@pytest.mark.parametrize(
    "question,previous,ids,reply,rewritten",
    [
        ("什么是营业执照？", "", (), "", ""),
        ("解释一下什么是交联聚乙烯", "", (), "", ""),
        ("找一下南河的营业执照", "", ("tenderco-a",), "", "找一下南河的营业执照"),
        ("找南河光电有限公司的检测报告", "", ("tenderco-c",), "", "找南河光电有限公司的检测报告"),
        (
            "找91440300123456789A的营业执照",
            "",
            ("tenderco-a",),
            "",
            "找91440300123456789A的营业执照",
        ),
        ("找一下北川的营业执照", "", (), NO_SCOPE, ""),
        ("未来光电有限公司", "查这家公司的营业执照", (), NO_SCOPE, ""),
        ("南河贸易有限公司", "查这家公司的营业执照", (), NO_SCOPE, ""),
        ("查南河贸易有限公司的营业执照", "", (), NO_SCOPE, ""),
        ("查南河和北川的营业执照", "", (), COMPANY_QUESTION, ""),
        ("查这家公司的营业执照", "", (), COMPANY_QUESTION, ""),
        ("在内部资料库找这家公司的营业执照", "", (), COMPANY_QUESTION, ""),
        ("在内部资料库找这家公司的执照", "", (), COMPANY_QUESTION, ""),
        ("在内部资料库找南河的营业执照", "", (), COMPANY_LIBRARY_MISMATCH, ""),
        ("东区改造项目库里的南河营业执照", "", (), COMPANY_LIBRARY_MISMATCH, ""),
        ("南河", "查这家公司的营业执照", ("tenderco-a",), "", "查这家公司的营业执照\n南河"),
        ("有效期呢？", "查南河的营业执照", ("tenderco-a",), "", "查南河的营业执照\n有效期呢？"),
        ("另一家的营业执照呢？", "查南河的营业执照", (), COMPANY_QUESTION, ""),
        ("那北川的执照呢？", "查南河的营业执照", (), NO_SCOPE, ""),
        ("那北川呢？", "查南河的营业执照", (), NO_SCOPE, ""),
        ("另一家呢？", "查南河的营业执照", (), COMPANY_QUESTION, ""),
        (
            "那检测报告呢？",
            "查南河的营业执照",
            ("tenderco-a",),
            "",
            "南河电缆有限公司\n那检测报告呢？",
        ),
        ("这个项目的招标文件在哪里？", "查南河的营业执照", (), PROJECT_QUESTION, ""),
        (
            "东区改造项目库",
            "对照这个项目的招标文件和南河产品报告",
            ("tenderco-a", "project-a"),
            "",
            "对照这个项目的招标文件和南河产品报告\n东区改造项目库",
        ),
        ("看经营档案库", "", (), NO_SCOPE, ""),
        ("查看员工制度库的请假流程", "", ("policy",), "", "查看员工制度库的请假流程"),
        ("查东区改造项目库的招标要求", "", ("project-a",), "", "查东区改造项目库的招标要求"),
        ("查东区改造项目的招标文件", "", ("project-a",), "", "查东区改造项目的招标文件"),
        (
            "对照东区改造项目库招标文件和南河产品报告",
            "",
            ("tenderco-a", "project-a"),
            "",
            "对照东区改造项目库招标文件和南河产品报告",
        ),
        ("对照内部资料库的招标文件和南河产品报告", "", (), COMPANY_LIBRARY_MISMATCH, ""),
        (
            "对照东区改造项目库、西区布线项目库的招标文件和南河产品报告",
            "",
            (),
            PROJECT_QUESTION,
            "",
        ),
    ],
)
def test_scope(question, previous, ids, reply, rewritten):
    route = _scope(
        question,
        previous,
        companies=COMPANIES,
        usable_company_ids={"tenderco-a", "tenderco-c"},
        general_bases=GENERAL,
        has_attachment=False,
    )
    assert (route.base_ids, route.reply, route.query) == (ids, reply, rewritten)


@pytest.mark.parametrize(
    "prior_questions,question,acl,expected",
    [
        (
            ["查这家公司的营业执照", "南河", "有效期呢？", "有原件吗？"],
            "能续期吗？",
            {1: {"use"}, 2: {"view"}},
            KnowledgeRoute("查这家公司的营业执照\n南河\n能续期吗？", ("tenderco-a",)),
        ),
        (
            ["查南河的营业执照", "有效期呢？", "有原件吗？"],
            "能续期吗？",
            {1: {"view"}, 2: {"use"}},
            KnowledgeRoute(reply=NO_SCOPE),
        ),
        (
            ["查南河的营业执照", "另一家呢？", "北川", "有原件吗？"],
            "有效期呢？",
            {1: {"use"}, 2: {"use"}},
            KnowledgeRoute("另一家呢？\n营业执照\n北川\n有效期呢？", ("tenderco-b",)),
        ),
        (
            ["查南河的营业执照", "什么是交联聚乙烯？"],
            "有原件吗？",
            {1: {"use"}, 2: {"use"}},
            KnowledgeRoute(),
        ),
        (
            [
                "东区改造项目库的招标文件在哪里？",
                "这份有原件吗？",
                "西区布线项目库的招标文件在哪里？",
            ],
            "有原件吗？",
            {1: {"use"}, 2: {"use"}, 3: {"use"}, 4: {"use"}},
            KnowledgeRoute("西区布线项目库的招标文件在哪里？\n有原件吗？", ("project-b",)),
        ),
    ],
)
async def test_query_uses_current_permissions_and_active_topic(
    monkeypatch,
    prior_questions,
    question,
    acl,
    expected,
):
    bases = [
        SimpleNamespace(id=1, public_id="tenderco-a", purpose="asset"),
        SimpleNamespace(id=2, public_id="tenderco-b", purpose="asset"),
        SimpleNamespace(
            id=3,
            public_id="project-a",
            purpose="rag",
            name="东区改造项目库",
            description="招标文件",
        ),
        SimpleNamespace(
            id=4,
            public_id="project-b",
            purpose="rag",
            name="西区布线项目库",
            description="招标文件",
        ),
    ]
    companies = [
        SimpleNamespace(base_id=1, name="南河电缆有限公司", credit_code="", aliases=["南河"]),
        SimpleNamespace(base_id=2, name="北川电缆有限公司", credit_code="", aliases=["北川"]),
    ]
    db = SimpleNamespace(
        scalars=AsyncMock(side_effect=[Mock(all=lambda: bases), Mock(all=lambda: companies)])
    )
    monkeypatch.setattr(ai.kb_access, "perms_for_bases", AsyncMock(return_value=acl))
    history = [{"role": "user", "content": text} for text in prior_questions]
    route = await resolve_knowledge_route(db, Mock(), question, history, has_attachment=False)
    assert route == expected
    ai.kb_access.perms_for_bases.assert_awaited_once()


def test_attachment_only_does_not_replace_company_for_project_comparison():
    route = _scope(
        "对照附件里的招标文件和这家公司的产品报告",
        "",
        companies=COMPANIES,
        usable_company_ids={"tenderco-a"},
        general_bases=GENERAL,
        has_attachment=True,
    )
    assert route == KnowledgeRoute(reply=COMPANY_QUESTION)


def test_evidence_rejects_other_company_and_low_score_and_limits_input():
    chunks = [
        {"kb_id": "tenderco-b", "content": "其他公司", "score": 0.95},
        {"kb_id": "tenderco-a", "content": "弱命中", "score": 0.11},
        *[
            {"kb_id": "tenderco-a", "doc_id": i, "content": "字" * 900, "score": 0.85}
            for i in range(6)
        ],
    ]
    evidence = bounded_evidence(chunks, ("tenderco-a",), min_score=0.12)
    assert [row["doc_id"] for row in evidence] == [0, 1, 2, 3]
    assert [len(row["content"]) for row in evidence] == [600] * 4
    assert len(chunks[2]["content"]) == 900
    small = [{**row, "content": "招标文件原文"} for row in chunks[2:]]
    assert [row["doc_id"] for row in bounded_evidence(small, ("tenderco-a",), min_score=0.12)] == [
        0,
        1,
        2,
        3,
        4,
    ]


@pytest.mark.parametrize(
    "mode,expected", [("auto", []), ("off", []), ("manual", ["policy"]), (None, ["policy"])]
)
async def test_explicit_mode_preserves_legacy_clients(mode, expected):
    body = ai.ChatRequest(sessionId="chat1", knowledgeMode=mode, knowledgeBaseIds=["policy"])
    assert (await ai._resolve_chat_bindings(Mock(), body))["knowledgeBaseIds"] == expected


@pytest.mark.parametrize("case", ["direct", "scope", "hit", "miss"])
async def test_automatic_chat_sse(case, monkeypatch):
    session = SimpleNamespace(public_id="chat1")
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
    user_question = "用户问题"
    route = {
        "direct": KnowledgeRoute(),
        "scope": KnowledgeRoute(reply=COMPANY_QUESTION),
        "hit": KnowledgeRoute("南河营业执照", ("tenderco-a",)),
        "miss": KnowledgeRoute("南河营业执照", ("tenderco-a",)),
    }[case]
    search = AsyncMock(
        return_value=(
            [{"kb_id": "tenderco-a", "name": "营业执照", "content": "有效期限", "score": 0.8}]
            if case == "hit"
            else []
        )
    )
    model = AsyncMock(return_value=SimpleNamespace(fixed_model="test-model"))
    originals = AsyncMock()
    streamed = []

    @asynccontextmanager
    async def lock(*_args, **_kwargs):
        yield Mock(ttl=300)

    async def model_stream(*_args, **kwargs):
        streamed.append(kwargs)
        yield {"event": "delta", "data": json.dumps({"text": "回答"})}
        yield {"event": "__final__", "data": json.dumps({"content": "回答"})}

    monkeypatch.setattr(ai, "EventSourceResponse", lambda events, **_kwargs: events)
    monkeypatch.setattr(ai, "session_lock", lock)
    monkeypatch.setattr(ai, "check_sliding_rate_limit", AsyncMock())
    monkeypatch.setattr(ai.kb_access, "require_usable_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(ai.sessions_svc, "get_session_for_user", AsyncMock(return_value=session))
    monkeypatch.setattr(ai.sessions_svc, "maybe_auto_title", AsyncMock(return_value=None))
    for attr in [
        "ensure_hot_context",
        "append_hot_and_enqueue",
        "bump_session_meta",
        "maybe_roll_trim",
    ]:
        monkeypatch.setattr(ai.memory_svc, attr, AsyncMock())
    monkeypatch.setattr(
        ai.memory_svc,
        "load_hot_messages",
        AsyncMock(return_value=[{"role": "user", "content": user_question}]),
    )
    monkeypatch.setattr(ai, "resolve_knowledge_route", AsyncMock(return_value=route))
    monkeypatch.setattr(ai, "search_chunks", search)
    monkeypatch.setattr(ai, "load_tender_original_blobs", originals)
    monkeypatch.setattr(ai, "build_llm_client", model)
    monkeypatch.setattr(ai, "run_chat_with_mcp", model_stream)

    events = await ai.ai_chat(
        ai.ChatRequest(sessionId="chat1", content=user_question, knowledgeMode="auto"),
        request,
        Mock(),
        Mock(id=7),
    )
    payloads = {row["event"]: json.loads(row["data"]) async for row in events}
    assert payloads["done"]["ok"] is True
    assert payloads["refs"]["knowledgeBaseIds"] == list(route.base_ids)
    assert payloads["refs"]["knowledgeNotice"] is (case in {"scope", "miss"})
    assert [ref["name"] for ref in payloads["refs"]["chunks"]] == (
        ["营业执照"] if case == "hit" else []
    )
    assert search.await_count == int(case in {"hit", "miss"})
    if search.await_count:
        assert search.await_args.kwargs["query"] == "南河营业执照"
        assert search.await_args.kwargs["kb_ids"] == ["tenderco-a"]
        assert search.await_args.kwargs["company_scoped"] is True
    assert model.await_count == int(case in {"direct", "hit"})
    if streamed:
        assert streamed[0]["tools_enabled"] is False
    originals.assert_not_awaited()
    assert ai.memory_svc.bump_session_meta.await_args.kwargs["automatic_knowledge"] is True
