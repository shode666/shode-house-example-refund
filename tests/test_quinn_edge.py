"""Quinn (QA) adversarial edge-case probes — Phase 3b integration axis, bd A5.

Not modifying product code. These 3 tests close gaps found while reviewing
Chris/Dave's existing suite: no test asserted (1) accumulated partials that
land EXACTLY on the remaining balance transition to REFUNDED, (2) a 409
conflict does not poison a later correct-amount replay of the same key
(no double ledger write), (3) all 3 GET endpoints 404 consistently on an
unknown order id (only the POST /refunds 404 path was tested before).
"""

from decimal import Decimal


def _create_order(client, captured_amount="1000.00"):
    resp = client.post("/orders", json={"captured_amount": captured_amount})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _refund(client, order_id, amount, key):
    return client.post(
        f"/orders/{order_id}/refunds",
        json={"amount": amount},
        headers={"Idempotency-Key": key},
    )


def test_quinn_accumulated_refund_exact_remaining_transitions_to_refunded(client):
    order = _create_order(client, "1000.00")
    order_id = order["id"]

    r1 = _refund(client, order_id, "300.00", "quinn-exact-1")
    assert r1.status_code == 201, r1.text
    assert r1.json()["order_status"] == "PARTIALLY_REFUNDED"

    # second refund amount == exact remaining balance (700.00), not a round number
    # chosen arbitrarily by AC-02 example -- this is the "exactly consumes
    # refundable_amount via accumulation" edge AC-02/Felix's worked example
    # implies but no existing test asserts the terminal-status transition for.
    r2 = _refund(client, order_id, "700.00", "quinn-exact-2")
    assert r2.status_code == 201, r2.text
    assert r2.json()["order_status"] == "REFUNDED"

    final = client.get(f"/orders/{order_id}").json()
    assert final["status"] == "REFUNDED"
    assert final["refunded_amount"] == "1000.00"
    assert final["refundable_amount"] == "0.00"

    ledger = client.get(f"/orders/{order_id}/ledger").json()
    assert len(ledger["entries"]) == 6  # sale(2) + refund1(2) + refund2(2)
    assert Decimal(ledger["totals"]["debit"]) == Decimal(ledger["totals"]["credit"]) == Decimal("2000.00")

    # F-1 (Felix): total balance alone would still pass on a direction inversion
    # -- assert per-account+direction to actually pin DEBIT/CREDIT sides.
    def _sum(account, direction):
        return sum(
            Decimal(e["amount"]) for e in ledger["entries"]
            if e["account_code"] == account and e["direction"] == direction
        )

    assert _sum("1010", "DEBIT") == Decimal("1000.00")   # sale
    assert _sum("4010", "CREDIT") == Decimal("1000.00")  # sale
    assert _sum("4090", "DEBIT") == Decimal("1000.00")   # refunds total
    assert _sum("1010", "CREDIT") == Decimal("1000.00")  # refunds total
    # F-2 (Felix): tie ledger to the aggregate, not just internal balance
    assert _sum("4090", "DEBIT") == Decimal(final["refunded_amount"])


def test_quinn_conflict_then_correct_key_replay_does_not_double_write(client):
    order = _create_order(client, "1000.00")
    order_id = order["id"]

    ok = _refund(client, order_id, "100.00", "quinn-key-c")
    assert ok.status_code == 201

    # reuse same key with a DIFFERENT amount -> 409, per AC-06
    conflict = _refund(client, order_id, "200.00", "quinn-key-c")
    assert conflict.status_code == 409, conflict.text

    # now reuse the SAME key with the ORIGINAL amount again -> must replay
    # cleanly (200 OK, same refund id), not be poisoned by the intervening
    # 409 attempt, and must not create a second ledger entry pair
    replay = _refund(client, order_id, "100.00", "quinn-key-c")
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == ok.json()["id"]

    order_after = client.get(f"/orders/{order_id}").json()
    assert order_after["refunded_amount"] == "100.00"  # not doubled, not tripled

    ledger = client.get(f"/orders/{order_id}/ledger").json()
    assert len(ledger["entries"]) == 4  # sale(2) + exactly one refund(2)


def test_quinn_all_get_endpoints_404_on_unknown_order(client):
    unknown = "00000000-0000-0000-0000-000000000099"

    order_resp = client.get(f"/orders/{unknown}")
    ledger_resp = client.get(f"/orders/{unknown}/ledger")
    refunds_resp = client.get(f"/orders/{unknown}/refunds")

    assert order_resp.status_code == 404, order_resp.text
    assert ledger_resp.status_code == 404, ledger_resp.text
    assert refunds_resp.status_code == 404, refunds_resp.text
