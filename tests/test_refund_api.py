"""Integration tests against real PostgreSQL (see conftest.py + Makefile).

Each test name maps to one AC/SEC item from 01-bella-1a.md / 04-sentinel-1c.md.
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


def _ledger_totals(client, order_id):
    resp = client.get(f"/orders/{order_id}/ledger")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_ac01_happy_path_partial_refund(client):
    order = _create_order(client, "1000.00")
    order_id = order["id"]

    resp = _refund(client, order_id, "300.00", "key-ac01")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["amount"] == "300.00"
    assert body["order_status"] == "PARTIALLY_REFUNDED"
    assert len(body["ledger_entry_ids"]) == 2

    order_after = client.get(f"/orders/{order_id}").json()
    assert order_after["refunded_amount"] == "300.00"
    assert order_after["refundable_amount"] == "700.00"
    assert order_after["status"] == "PARTIALLY_REFUNDED"

    ledger = _ledger_totals(client, order_id)
    debit = Decimal(ledger["totals"]["debit"])
    credit = Decimal(ledger["totals"]["credit"])
    assert debit == credit  # sum(debit) == sum(credit) invariant


def test_ac02_accumulated_refunds_create_separate_entries(client):
    order = _create_order(client, "1000.00")
    order_id = order["id"]

    r1 = _refund(client, order_id, "300.00", "key-ac02-a")
    r2 = _refund(client, order_id, "400.00", "key-ac02-b")
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]  # not merged/overwritten

    order_after = client.get(f"/orders/{order_id}").json()
    assert order_after["refunded_amount"] == "700.00"

    ledger = _ledger_totals(client, order_id)
    # 1 sale journal (2 lines) + 2 refund journals (2 lines each) = 6 lines
    assert len(ledger["entries"]) == 6
    assert Decimal(ledger["totals"]["debit"]) == Decimal(ledger["totals"]["credit"])


def test_ac03a_single_over_refund_request_rejected(client):
    order = _create_order(client, "1000.00")
    order_id = order["id"]
    _refund(client, order_id, "700.00", "key-ac03a-seed")

    resp = _refund(client, order_id, "400.00", "key-ac03a-over")
    assert resp.status_code == 422, resp.text
    assert resp.json()["refundable_amount"] == "300.00"

    order_after = client.get(f"/orders/{order_id}").json()
    assert order_after["refunded_amount"] == "700.00"  # unchanged

    ledger = _ledger_totals(client, order_id)
    assert len(ledger["entries"]) == 4  # sale (2) + first refund (2), no new lines


def test_ac04_api_rejects_scale_over_2_decimals(client):
    order = _create_order(client, "1000.00")
    resp = _refund(client, order["id"], "100.005", "key-ac04")
    assert resp.status_code == 422, resp.text


def test_ac05_idempotent_replay_returns_same_result_no_duplicate(client):
    order = _create_order(client, "1000.00")
    order_id = order["id"]

    first = _refund(client, order_id, "300.00", "key-ac05")
    assert first.status_code == 201
    first_body = first.json()

    replay = _refund(client, order_id, "300.00", "key-ac05")
    assert replay.status_code == 200, replay.text
    replay_body = replay.json()
    assert replay_body["id"] == first_body["id"]

    order_after = client.get(f"/orders/{order_id}").json()
    assert order_after["refunded_amount"] == "300.00"  # not doubled

    ledger = _ledger_totals(client, order_id)
    assert len(ledger["entries"]) == 4  # sale (2) + one refund (2), no duplicate


def test_ac06_idempotency_key_reused_with_different_amount_conflicts(client):
    order = _create_order(client, "1000.00")
    order_id = order["id"]

    ok = _refund(client, order_id, "300.00", "key-ac06")
    assert ok.status_code == 201

    conflict = _refund(client, order_id, "400.00", "key-ac06")
    assert conflict.status_code == 409, conflict.text

    order_after = client.get(f"/orders/{order_id}").json()
    assert order_after["refunded_amount"] == "300.00"  # unaffected


def test_ac07_refund_history_lists_two_records_in_order(client):
    order = _create_order(client, "1000.00")
    order_id = order["id"]
    _refund(client, order_id, "300.00", "key-ac07-a")
    _refund(client, order_id, "400.00", "key-ac07-b")

    resp = client.get(f"/orders/{order_id}/refunds")
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) == 2
    assert records[0]["amount"] == "300.00"
    assert records[1]["amount"] == "400.00"
    for record in records:
        assert "idempotency_key" in record
        assert "created_at" in record
        assert len(record["ledger_entry_ids"]) == 2


def test_ac08_refund_after_fully_refunded_rejected(client):
    order = _create_order(client, "1000.00")
    order_id = order["id"]
    _refund(client, order_id, "1000.00", "key-ac08-full")

    order_after = client.get(f"/orders/{order_id}").json()
    assert order_after["status"] == "REFUNDED"

    resp = _refund(client, order_id, "0.01", "key-ac08-extra")
    assert resp.status_code == 422, resp.text

    still = client.get(f"/orders/{order_id}").json()
    assert still["status"] == "REFUNDED"
    assert still["refunded_amount"] == "1000.00"

    ledger = _ledger_totals(client, order_id)
    assert len(ledger["entries"]) == 4  # sale (2) + full refund (2), nothing extra


def test_sec01_bound_validation_rejects_before_transaction(client):
    order = _create_order(client, "1000.00")
    order_id = order["id"]
    for bad_amount in ["-300.00", "0.00", "99999999999999999.00"]:
        resp = _refund(client, order_id, bad_amount, f"key-sec01-{bad_amount}")
        assert resp.status_code == 422, f"{bad_amount} -> {resp.status_code} {resp.text}"

    ledger = _ledger_totals(client, order_id)
    assert len(ledger["entries"]) == 2  # only the sale journal, no refund lines


def test_sec02_idempotency_key_reused_across_orders_conflicts(client):
    order_a = _create_order(client, "1000.00")
    order_b = _create_order(client, "1000.00")

    ok = _refund(client, order_a["id"], "300.00", "shared-key")
    assert ok.status_code == 201

    hijack = _refund(client, order_b["id"], "300.00", "shared-key")
    assert hijack.status_code == 409, hijack.text

    order_b_after = client.get(f"/orders/{order_b['id']}").json()
    assert order_b_after["refunded_amount"] == "0.00"  # order B untouched


def test_sec03_extra_field_in_refund_request_rejected(client):
    order = _create_order(client, "1000.00")
    resp = client.post(
        f"/orders/{order['id']}/refunds",
        json={"amount": "300.00", "refunded_amount": "0.00"},
        headers={"Idempotency-Key": "key-sec03"},
    )
    assert resp.status_code == 422, resp.text


def test_refund_missing_idempotency_key_header_rejected(client):
    order = _create_order(client, "1000.00")
    resp = client.post(f"/orders/{order['id']}/refunds", json={"amount": "300.00"})
    assert resp.status_code == 400


def test_refund_on_unknown_order_returns_404(client):
    resp = _refund(client, "00000000-0000-0000-0000-000000000000", "300.00", "key-404")
    assert resp.status_code == 404
