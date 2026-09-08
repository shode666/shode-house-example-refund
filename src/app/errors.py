"""Domain exceptions -> HTTP status mapping (used by main.py error handlers)."""


class DomainError(Exception):
    """Base class; carries an HTTP status + a body dict."""

    status_code: int = 400

    def __init__(self, detail: dict | str):
        self.detail = detail
        super().__init__(str(detail))


class OrderNotFound(DomainError):
    status_code = 404


class OverRefundError(DomainError):
    """AC-03(a)/AC-08: refund would exceed refundable_amount or order is terminal."""

    status_code = 422


class IdempotencyKeyMissing(DomainError):
    status_code = 400


class IdempotencyConflict(DomainError):
    """AC-06 (amount mismatch) / SEC-02 (cross-order reuse of the same key)."""

    status_code = 409
