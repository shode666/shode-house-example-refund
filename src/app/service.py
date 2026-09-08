"""Transaction boundary: create_order / create_refund / status derivation.

Pseudo-code source: 03-sara-1a.md §4, revised per Sentinel SEC-02
(04-sentinel-1c.md §3) — idempotency replay must check order_id, not just
amount, otherwise a key used on order A can be replayed against order B.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import (
    IdempotencyConflict,
    OrderNotFound,
    OverRefundError,
)
from app.models import LedgerEntry, Order, Refund

ACCOUNT_CASH_CLEARING = "1010"
ACCOUNT_SALES_REVENUE = "4010"
ACCOUNT_SALES_REFUNDS = "4090"

STATUS_PAID = "PAID"
STATUS_PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
STATUS_REFUNDED = "REFUNDED"


def derive_status(captured_amount: Decimal, refunded_amount: Decimal) -> str:
    """ADR-001: status is derived, never stored — no dual-write possible."""
    if refunded_amount <= 0:
        return STATUS_PAID
    if refunded_amount >= captured_amount:
        return STATUS_REFUNDED
    return STATUS_PARTIALLY_REFUNDED


def create_order(session: Session, captured_amount: Decimal) -> Order:
    order = Order(captured_amount=captured_amount, refunded_amount=Decimal("0"))
    session.add(order)
    session.flush()

    journal_id = uuid.uuid4()
    session.add_all(
        [
            LedgerEntry(
                journal_id=journal_id,
                order_id=order.id,
                refund_id=None,
                account_code=ACCOUNT_CASH_CLEARING,
                direction="DEBIT",
                amount=captured_amount,
            ),
            LedgerEntry(
                journal_id=journal_id,
                order_id=order.id,
                refund_id=None,
                account_code=ACCOUNT_SALES_REVENUE,
                direction="CREDIT",
                amount=captured_amount,
            ),
        ]
    )
    session.commit()
    session.refresh(order)
    return order


def get_order_or_404(session: Session, order_id: uuid.UUID) -> Order:
    order = session.get(Order, order_id)
    if order is None:
        raise OrderNotFound({"error": "order not found", "order_id": str(order_id)})
    return order


def _existing_refund_by_key(session: Session, idempotency_key: str) -> Refund | None:
    stmt = select(Refund).where(Refund.idempotency_key == idempotency_key)
    return session.execute(stmt).scalar_one_or_none()


def _handle_replay_or_conflict(
    existing: Refund, order_id: uuid.UUID, amount: Decimal
) -> Refund:
    if existing.order_id != order_id:
        # SEC-02: same key reused across orders -> always reject, never replay
        raise IdempotencyConflict(
            {
                "error": "idempotency_key already used on a different order",
                "idempotency_key": existing.idempotency_key,
            }
        )
    if existing.amount != amount:
        # AC-06: same key, same order, different amount -> reject
        raise IdempotencyConflict(
            {
                "error": "idempotency_key already used with a different amount",
                "idempotency_key": existing.idempotency_key,
            }
        )
    # AC-05: identical replay -> return the original result, no new writes
    return existing


def create_refund(
    session: Session, order_id: uuid.UUID, idempotency_key: str, amount: Decimal
) -> tuple[Refund, bool]:
    """Returns (refund, created). created=False means AC-05 replay."""

    existing = _existing_refund_by_key(session, idempotency_key)
    if existing is not None:
        return _handle_replay_or_conflict(existing, order_id, amount), False

    try:
        # AC-03(b): lock the order row so concurrent refunds on the same
        # order serialize; the loser re-reads a fresh refunded_amount.
        stmt = select(Order).where(Order.id == order_id).with_for_update()
        order = session.execute(stmt).scalar_one_or_none()
        if order is None:
            raise OrderNotFound(
                {"error": "order not found", "order_id": str(order_id)}
            )

        # D-1 fix (04-felix-domain.md § D-1): re-check the idempotency key
        # INSIDE the critical section, immediately after the row lock is
        # acquired. The read at :111 happens before the lock and can miss a
        # concurrent same-key request that is still in flight; without this
        # re-check the loser falls through to the over-refund cap guards
        # below and gets 422 instead of the AC-05-mandated replay (200).
        existing = _existing_refund_by_key(session, idempotency_key)
        if existing is not None:
            session.rollback()
            return _handle_replay_or_conflict(existing, order_id, amount), False

        if order.refunded_amount >= order.captured_amount:
            # AC-08: order already fully refunded (terminal) -> reject, no writes
            raise OverRefundError(
                {
                    "error": "order already fully refunded",
                    "refundable_amount": str(
                        order.captured_amount - order.refunded_amount
                    ),
                }
            )

        refundable = order.captured_amount - order.refunded_amount
        if amount > refundable:
            # AC-03(a): would exceed refundable_amount -> reject, no writes
            raise OverRefundError(
                {
                    "error": "refund exceeds refundable amount",
                    "refundable_amount": str(refundable),
                }
            )

        refund = Refund(
            order_id=order_id, amount=amount, idempotency_key=idempotency_key
        )
        session.add(refund)
        session.flush()

        journal_id = uuid.uuid4()
        session.add_all(
            [
                LedgerEntry(
                    journal_id=journal_id,
                    order_id=order_id,
                    refund_id=refund.id,
                    account_code=ACCOUNT_SALES_REFUNDS,
                    direction="DEBIT",
                    amount=amount,
                ),
                LedgerEntry(
                    journal_id=journal_id,
                    order_id=order_id,
                    refund_id=refund.id,
                    account_code=ACCOUNT_CASH_CLEARING,
                    direction="CREDIT",
                    amount=amount,
                ),
            ]
        )
        order.refunded_amount = order.refunded_amount + amount
        session.commit()
        session.refresh(refund)
        return refund, True

    except IntegrityError as exc:
        # Race: two requests with the same idempotency_key committed concurrently.
        # Narrowed per Chris (06-chris-3b.md 🟠): only reinterpret the specific
        # idempotency-key unique-violation as a replay race; any other
        # integrity error (FK violation, CHECK constraint, etc.) re-raises.
        session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint != "refunds_idempotency_key_key":
            raise
        winner = _existing_refund_by_key(session, idempotency_key)
        if winner is None:
            raise
        return _handle_replay_or_conflict(winner, order_id, amount), False


def list_refunds(session: Session, order_id: uuid.UUID) -> list[Refund]:
    get_order_or_404(session, order_id)
    stmt = (
        select(Refund)
        .where(Refund.order_id == order_id)
        .order_by(Refund.created_at.asc())
    )
    return list(session.execute(stmt).scalars().all())


def ledger_entry_ids_for_refund(session: Session, refund_id: uuid.UUID) -> list[uuid.UUID]:
    stmt = select(LedgerEntry.id).where(LedgerEntry.refund_id == refund_id)
    return list(session.execute(stmt).scalars().all())


def list_ledger(session: Session, order_id: uuid.UUID) -> list[LedgerEntry]:
    get_order_or_404(session, order_id)
    stmt = (
        select(LedgerEntry)
        .where(LedgerEntry.order_id == order_id)
        .order_by(LedgerEntry.created_at.asc())
    )
    return list(session.execute(stmt).scalars().all())
