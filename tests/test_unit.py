"""Unit tests — no DB. Pure validation + derivation logic.

Coverage: AC-04 (scale), SEC-01 (bound validation), SEC-03 (extra field),
ADR-001 status derivation.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas import CreateRefundRequest
from app.service import (
    STATUS_PAID,
    STATUS_PARTIALLY_REFUNDED,
    STATUS_REFUNDED,
    derive_status,
)


def test_ac04_scale_validation_rejects_more_than_2_decimals():
    with pytest.raises(ValidationError):
        CreateRefundRequest(amount="100.005")


def test_ac04_scale_validation_accepts_exactly_2_decimals():
    req = CreateRefundRequest(amount="100.01")
    assert req.amount == Decimal("100.01")


def test_ac04_scale_validation_rejects_float_input():
    # Felix §Q2 / AC-04: parse from string only, never float
    with pytest.raises(ValidationError):
        CreateRefundRequest(amount=100.00)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_amount", ["-300.00", "0.00", "99999999999999999.00"])
def test_sec01_bound_validation_rejects_at_schema_layer(bad_amount):
    with pytest.raises(ValidationError):
        CreateRefundRequest(amount=bad_amount)


def test_sec01_bound_validation_accepts_max_valid_amount():
    # 16 integer digits is the boundary for NUMERIC(18,2)
    req = CreateRefundRequest(amount="9999999999999999.00")
    assert req.amount == Decimal("9999999999999999.00")


def test_sec03_extra_field_rejected():
    with pytest.raises(ValidationError):
        CreateRefundRequest(amount="300.00", refunded_amount="0.00")  # type: ignore[call-arg]


def test_status_derivation_paid_partial_refunded_refunded():
    captured = Decimal("1000.00")
    assert derive_status(captured, Decimal("0.00")) == STATUS_PAID
    assert derive_status(captured, Decimal("300.00")) == STATUS_PARTIALLY_REFUNDED
    assert derive_status(captured, Decimal("1000.00")) == STATUS_REFUNDED


# --- Chris (CR) 2026-09-08: SEC-01 gap found while reviewing _parse_money's
# digit-counting for positive Decimal exponents. See 06-chris-3b.md Finding 1.
def test_chris_sec01_rejects_scientific_notation_oversized_amount():
    # "1E+20" == 10**20, 21 integer digits -- must be rejected same as the
    # already-tested "99999999999999999.00" case (AC-04/SEC-01 boundary).
    # Currently ACCEPTED by _parse_money (digit count ignores positive
    # exponent's implied trailing zeros) and reaches the DB, which raises
    # psycopg.errors.NumericValueOutOfRange -> unhandled 500 (verified via
    # live uvicorn + curl, see review notes).
    with pytest.raises(ValidationError):
        CreateRefundRequest(amount="1E+20")


def test_chris_sec01_rejects_scientific_notation_small_oversized_amount():
    # A smaller repro of the same bug: "1E+17" is 18 integer digits (> the
    # 16-digit MAX_INTEGER_DIGITS bound) but is still accepted today.
    with pytest.raises(ValidationError):
        CreateRefundRequest(amount="1E+17")
