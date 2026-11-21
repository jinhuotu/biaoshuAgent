"""Request-local company library for the authenticated old tender flow."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.models.knowledge import KnowledgeBase

_company_base: ContextVar[KnowledgeBase | None] = ContextVar("tender_company_base", default=None)


def current_company_base() -> KnowledgeBase | None:
    return _company_base.get()


@contextmanager
def use_company_base(base: KnowledgeBase) -> Iterator[None]:
    token = _company_base.set(base)
    try:
        yield
    finally:
        _company_base.reset(token)
