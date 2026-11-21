"""Persistent opt-out for virtual tender-library entries; no file deletion."""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class TenderCompany(Base):
    __tablename__ = "tender_companies"

    public_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    base_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(110), nullable=False)
    credit_code: Mapped[str] = mapped_column(String(18), unique=True, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class TenderLibraryDismissal(Base):
    __tablename__ = "tender_library_dismissals"

    base_public_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.now(), nullable=False
    )
