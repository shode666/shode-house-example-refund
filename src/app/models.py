"""ORM models — see 03-sara-1a.md §2 for schema + AC mapping table.

Constraint -> AC coverage (kept as comments so the mapping travels with the code):
  orders.refunded_amount <= captured_amount   -> AC-03, AC-08 (defense-in-depth)
  NUMERIC(18,2) on every money column          -> AC-04 (DB-level scale; API rejects first)
  refunds.idempotency_key UNIQUE (global)      -> AC-05, AC-06, SEC-02 (service layer adds order_id check)
  ledger_entries.amount > 0 + direction CHECK  -> AC-01, AC-02 (Felix §Q1: no negative amounts)
  ledger_entries.account_code CHECK            -> Felix §Q3 CoA (1010/4010/4090)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("captured_amount > 0", name="ck_orders_captured_positive"),
        CheckConstraint(
            "refunded_amount >= 0 AND refunded_amount <= captured_amount",
            name="ck_orders_refunded_bounds",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    captured_amount: Mapped["Numeric"] = mapped_column(Numeric(18, 2), nullable=False)
    refunded_amount: Mapped["Numeric"] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_refunds_amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    amount: Mapped["Numeric"] = mapped_column(Numeric(18, 2), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String, nullable=False, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint(
            "account_code IN ('1010','4010','4090')", name="ck_ledger_account_code"
        ),
        CheckConstraint(
            "direction IN ('DEBIT','CREDIT')", name="ck_ledger_direction"
        ),
        CheckConstraint("amount > 0", name="ck_ledger_amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    journal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    refund_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("refunds.id"), nullable=True
    )
    account_code: Mapped[str] = mapped_column(String, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped["Numeric"] = mapped_column(Numeric(18, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
