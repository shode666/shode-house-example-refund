"""FastAPI app — routes per 03-sara-1a.md §3.

No auth by design (04-sentinel-1c.md §1) — every endpoint is public. See
README.md "Security notes" before using this as a template for anything
that isn't a local demo.
"""

from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import UUID

from fastapi import FastAPI, Header, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import service
from app.db import SessionLocal, engine
from app.errors import DomainError, IdempotencyKeyMissing, IdempotencyKeyTooLong
from app.models import Base
from app.schemas import (
    CreateOrderRequest,
    CreateRefundRequest,
    LedgerLine,
    LedgerResponse,
    OrderResponse,
    RefundResponse,
)

# C-4 fix (01-chris-3b.md #4 / 08-chris-3b-iter2.md): mirrors
# models.py:69 String(255) so a request-layer reject and the DB column
# bound never drift apart.
MAX_IDEMPOTENCY_KEY_LENGTH = 255


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Partial Refund + Double-Entry Ledger (reference project)",
    lifespan=lifespan,
)


@app.exception_handler(DomainError)
def domain_error_handler(request, exc: DomainError):
    return JSONResponse(status_code=exc.status_code, content=exc.detail)


def _order_to_response(order) -> OrderResponse:
    refundable = order.captured_amount - order.refunded_amount
    return OrderResponse(
        id=order.id,
        captured_amount=str(order.captured_amount),
        refunded_amount=str(order.refunded_amount),
        refundable_amount=str(refundable),
        status=service.derive_status(order.captured_amount, order.refunded_amount),
    )


def _refund_to_response(session: Session, refund, order) -> RefundResponse:
    ledger_ids = service.ledger_entry_ids_for_refund(session, refund.id)
    return RefundResponse(
        id=refund.id,
        order_id=refund.order_id,
        amount=str(refund.amount),
        idempotency_key=refund.idempotency_key,
        created_at=refund.created_at.isoformat(),
        ledger_entry_ids=ledger_ids,
        order_status=service.derive_status(order.captured_amount, order.refunded_amount),
    )


@app.post("/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(body: CreateOrderRequest):
    with SessionLocal() as session:
        order = service.create_order(session, body.captured_amount)
        return _order_to_response(order)


@app.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: UUID):
    with SessionLocal() as session:
        order = service.get_order_or_404(session, order_id)
        return _order_to_response(order)


@app.post("/orders/{order_id}/refunds")
def create_refund(
    order_id: UUID,
    body: CreateRefundRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    # C-4 fix: both the missing-header and oversize-header rejects now raise
    # DomainError subclasses (via domain_error_handler, main.py:42-44) so
    # every API error shares one `{"error": ...}` shape — previously the
    # missing-header case raised a raw HTTPException (framework `{"detail":
    # ...}` shape) and S-01's Header(max_length=...) fix (iter 2) would have
    # added a second, different framework shape for the oversize case.
    if not idempotency_key:
        raise IdempotencyKeyMissing({"error": "Idempotency-Key header is required"})
    if len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise IdempotencyKeyTooLong(
            {
                "error": "Idempotency-Key header exceeds maximum length",
                "max_length": MAX_IDEMPOTENCY_KEY_LENGTH,
            }
        )

    with SessionLocal() as session:
        refund, created = service.create_refund(
            session, order_id, idempotency_key, body.amount
        )
        order = service.get_order_or_404(session, order_id)
        payload = _refund_to_response(session, refund, order)
        return JSONResponse(
            status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            content=payload.model_dump(mode="json"),
        )


@app.get("/orders/{order_id}/refunds", response_model=list[RefundResponse])
def list_refunds(order_id: UUID):
    with SessionLocal() as session:
        order = service.get_order_or_404(session, order_id)
        refunds = service.list_refunds(session, order_id)
        return [_refund_to_response(session, r, order) for r in refunds]


@app.get("/orders/{order_id}/ledger", response_model=LedgerResponse)
def get_ledger(order_id: UUID):
    with SessionLocal() as session:
        service.get_order_or_404(session, order_id)
        entries = service.list_ledger(session, order_id)
        debit_total = sum((e.amount for e in entries if e.direction == "DEBIT"), Decimal("0"))
        credit_total = sum((e.amount for e in entries if e.direction == "CREDIT"), Decimal("0"))
        return LedgerResponse(
            entries=[
                LedgerLine(
                    id=e.id,
                    journal_id=e.journal_id,
                    refund_id=e.refund_id,
                    account_code=e.account_code,
                    direction=e.direction,
                    amount=str(e.amount),
                    created_at=e.created_at.isoformat(),
                )
                for e in entries
            ],
            totals={"debit": str(debit_total), "credit": str(credit_total)},
        )
