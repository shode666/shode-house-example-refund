"""Pydantic request/response schemas.

Money is always JSON string in/out (never float) — 03-sara-1a.md §3.
Validation rules encoded here:
  AC-04    scale <= 2 decimal places, reject (don't round) if exceeded
  SEC-01   amount > 0, integer part <= 16 digits (fits NUMERIC(18,2))
  SEC-03   extra="forbid" on every request schema (anti mass-assignment)
"""

from decimal import Decimal, InvalidOperation
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

MAX_INTEGER_DIGITS = 16  # NUMERIC(18,2) -> 18 total digits, 2 after the point


def _parse_money(value: object) -> Decimal:
    if not isinstance(value, str):
        # AC-04 / Felix §Q2: money must come from a string, never float/int
        raise ValueError("amount must be a JSON string, e.g. \"300.00\"")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"amount is not a valid decimal string: {value!r}") from exc

    # C-1 fix (06-chris-3b.md / 01-chris-3b.md): call as_tuple() ONCE and
    # reuse the same (narrowed) `exponent` below — calling it a second time
    # made mypy lose the isinstance(exponent, int) narrowing (each call
    # returns a fresh `int | Literal['n', 'N', 'F']` union) and produced 6
    # false-positive operand-type errors.
    sign, digits, exponent = amount.as_tuple()
    if not isinstance(exponent, int) or exponent < -2:
        # AC-04: reject if scale > 2, never silently round
        raise ValueError("amount must have at most 2 decimal places (no rounding)")

    if amount <= 0:
        # SEC-01: reject amount <= 0 at API layer, before any DB CHECK
        raise ValueError("amount must be > 0")

    # count digits left of the decimal point. exponent < 0 means some digits
    # are fractional (already scale-checked above); exponent >= 0 means the
    # value is padded by that many trailing zeros (e.g. "1E+20" ->
    # digits=(1,), exponent=20 -> 21 integer digits) — both cases must be
    # counted, not just len(digits) (Chris 06-chris-3b.md Finding 1:
    # positive-exponent Decimals bypassed this bound and reached the DB as
    # NumericValueOutOfRange -> 500).
    num_digits = len(digits)
    integer_digits = (
        max(num_digits + exponent, 0) if exponent < 0 else num_digits + exponent
    )
    if integer_digits > MAX_INTEGER_DIGITS:
        # SEC-01: reject amounts too large for NUMERIC(18,2) instead of a 500 from DB
        raise ValueError(
            f"amount integer part exceeds {MAX_INTEGER_DIGITS} digits"
        )

    return amount


Money = Annotated[Decimal, BeforeValidator(_parse_money)]


class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # SEC-03

    captured_amount: Money


class CreateRefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # SEC-03

    amount: Money


class OrderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    captured_amount: str
    refunded_amount: str
    refundable_amount: str
    status: str


class RefundResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    order_id: UUID
    amount: str
    idempotency_key: str
    created_at: str
    ledger_entry_ids: list[UUID]
    order_status: str


class LedgerLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    journal_id: UUID
    refund_id: UUID | None
    account_code: str
    direction: str
    amount: str
    created_at: str


class LedgerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[LedgerLine]
    totals: dict[str, str] = Field(description='{"debit": "...", "credit": "..."}')
