"""Regression tests for Felix D-1 + Chris C-5 (04-felix-domain.md § D-1,
01-chris-3b.md finding C-5) — promoted from throwaway probes:
  outputs/shode-house-example-refund-d5k/quinn-probe/probe_felix_d1.py
  outputs/shode-house-example-refund-d5k/chris-probe/probe_idem_race.py

D-1: idempotency key was read BEFORE the order row lock (service.py:111-113)
and never re-checked AFTER the lock, before the over-refund cap guard
(service.py:125-144). Two concurrent requests with the SAME idempotency key
and an amount that pushes the loser's freshly-locked refunded_amount over
the cap got (201, 422) instead of the AC-05-mandated (201, 200) replay.

C-5: the IntegrityError-recovery race branch (service.py:178-190) — hit
when the two concurrent requests' amount stays inside the refundable cap
so the loser reaches session.add(refund) and collides on the
idempotency_key UNIQUE constraint instead of hitting the D-1 guard — had
zero test coverage.

Style follows tests/test_concurrency.py (real uvicorn thread, threading
Barrier for genuine overlap) + tests/conftest.py (session-scoped live
server on port 8810, `client` fixture) — Anti-Puppet: no in-process
ASGI-transport shortcuts, a real TCP round-trip proves the DB row lock
actually serializes concurrent connections.
"""

import threading
from decimal import Decimal

import httpx

# Same server tests/conftest.py:26,36-54 starts for the whole session.
BASE_URL = "http://127.0.0.1:8810"


def _create_order(client, captured_amount="1000.00"):
    resp = client.post("/orders", json={"captured_amount": captured_amount})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _fire_same_key_concurrently(order_id: str, amount: str, key: str) -> list[int]:
    """Two threads POST the same refund (same order/amount/idempotency-key)
    through a barrier so the requests genuinely overlap at the DB, then
    return both HTTP status codes."""
    barrier = threading.Barrier(2)
    results: list[int | None] = [None, None]

    def fire(idx: int):
        with httpx.Client(base_url=BASE_URL, timeout=10) as c:
            barrier.wait()
            resp = c.post(
                f"/orders/{order_id}/refunds",
                json={"amount": amount},
                headers={"Idempotency-Key": key},
            )
            results[idx] = resp.status_code

    t1 = threading.Thread(target=fire, args=(0,))
    t2 = threading.Thread(target=fire, args=(1,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert None not in results, f"a request never completed: {results}"
    return results  # type: ignore[return-value]


def test_d1_same_key_over_cap_amount_replays_instead_of_422(client):
    """AC-05: same-key overlapping retry must return the replay (200), not
    422 over-refund, even when the amount pushes the SECOND-to-acquire-lock
    request's freshly-read refunded_amount over the cap (Felix D-1:
    600.00 + 600.00 > 1000.00 captured -> pre-fix the loser hit the
    OverRefundError guard at service.py:139 before ever reaching the
    IntegrityError recovery branch at service.py:178-190).

    >= 10 rounds because the race window is timing-dependent (Quinn
    confirmed 14/25 rounds pre-fix in quinn-probe/probe_felix_d1_run.txt).
    """
    rounds = 10
    for i in range(rounds):
        order = _create_order(client, "1000.00")
        order_id = order["id"]
        key = f"d1-round-{i}"

        statuses = _fire_same_key_concurrently(order_id, "600.00", key)

        assert sorted(statuses) == [200, 201], (
            f"round {i}: AC-05 replay violated, got {statuses} "
            f"(expected exactly one 201 + one 200 — same-key overlap must "
            f"replay, not 422)"
        )

        order_after = client.get(f"/orders/{order_id}").json()
        assert order_after["refunded_amount"] == "600.00", (
            f"round {i}: expected exactly ONE refund applied (600.00), "
            f"got refunded_amount={order_after['refunded_amount']} "
            f"(double-write if 1200.00)"
        )

        refunds = client.get(f"/orders/{order_id}/refunds").json()
        assert len(refunds) == 1, (
            f"round {i}: expected exactly 1 refund row, got {len(refunds)}"
        )

        ledger = client.get(f"/orders/{order_id}/ledger").json()
        assert len(ledger["entries"]) == 4  # sale(2) + refund(2), never 6
        assert Decimal(ledger["totals"]["debit"]) == Decimal(
            ledger["totals"]["credit"]
        ) == Decimal("1600.00")


def test_c5_same_key_within_cap_hits_integrity_error_recovery(client):
    """Chris C-5: same idempotency_key fired concurrently on the same order
    with an amount that stays INSIDE the refundable cap (250.00 on a
    1000.00 order) — both requests pass the cap guard and race on
    session.add(refund); the loser must hit the UNIQUE-violation
    IntegrityError branch (service.py:178-190) and replay cleanly, not
    double-write."""
    order = _create_order(client, "1000.00")
    order_id = order["id"]

    statuses = _fire_same_key_concurrently(order_id, "250.00", "c5-race-same-key")

    assert sorted(statuses) == [200, 201], f"expected one 201 + one 200, got {statuses}"

    order_after = client.get(f"/orders/{order_id}").json()
    assert order_after["refunded_amount"] == "250.00", (
        "no double-write expected, got "
        f"refunded_amount={order_after['refunded_amount']}"
    )

    ledger = client.get(f"/orders/{order_id}/ledger").json()
    assert len(ledger["entries"]) == 4  # sale(2) + refund(2)

    refunds = client.get(f"/orders/{order_id}/refunds").json()
    assert len(refunds) == 1


def test_s01_oversize_idempotency_key_rejected_422(client):
    """Sentinel S-01: unbounded Idempotency-Key header must be rejected at
    the API boundary (422) instead of reaching the DB/btree index limit.

    C-4 (01-chris-3b.md #4 / 08-chris-3b-iter2.md): the reject must come
    through DomainError -> `{"error": ...}`, the same shape as every other
    API error, not FastAPI/Pydantic's framework `{"detail": [...]}` shape.
    """
    order = _create_order(client, "1000.00")
    order_id = order["id"]

    oversize_key = "k" * 256

    resp = client.post(
        f"/orders/{order_id}/refunds",
        json={"amount": "100.00"},
        headers={"Idempotency-Key": oversize_key},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json() == {
        "error": "Idempotency-Key header exceeds maximum length",
        "max_length": 255,
    }

    # boundary itself (255) must still be accepted
    boundary_key = "k" * 255
    ok = client.post(
        f"/orders/{order_id}/refunds",
        json={"amount": "100.00"},
        headers={"Idempotency-Key": boundary_key},
    )
    assert ok.status_code == 201, ok.text


def test_c4_missing_idempotency_key_shares_unified_error_shape(client):
    """C-4: the missing-header reject (400) must also come through
    DomainError -> `{"error": ...}`, not a raw HTTPException `{"detail":
    ...}` shape — same unified shape as the oversize-key case above."""
    order = _create_order(client, "1000.00")
    order_id = order["id"]

    resp = client.post(f"/orders/{order_id}/refunds", json={"amount": "100.00"})
    assert resp.status_code == 400, resp.text
    assert resp.json() == {"error": "Idempotency-Key header is required"}
