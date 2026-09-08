"""AC-03(b): two overlapping refund requests on the same order must serialize
— only one may succeed when both together would exceed refundable_amount.

Uses a real uvicorn server + real threads + a barrier so the two HTTP
requests genuinely overlap in time (an in-process ASGI transport would not
prove the DB-level row lock actually blocks a second connection).
"""

import threading
import time

import httpx
import pytest
import uvicorn

from app.main import app


@pytest.fixture
def live_server():
    config = uvicorn.Config(app, host="127.0.0.1", port=8811, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 5
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn server did not start in time"

    yield "http://127.0.0.1:8811"

    server.should_exit = True
    thread.join(timeout=5)


def test_ac03_concurrent_refunds_serialize(live_server):
    with httpx.Client(base_url=live_server) as setup_client:
        order = setup_client.post(
            "/orders", json={"captured_amount": "1000.00"}
        ).json()
        setup_client.post(
            f"/orders/{order['id']}/refunds",
            json={"amount": "500.00"},
            headers={"Idempotency-Key": "seed-ac03b"},
        )
        order_id = order["id"]

    # refundable is now 500.00. Each request alone (400.00) is individually
    # valid, but together (800.00) they exceed refundable — this is the
    # actual race: a naive check-then-act (read refundable, then write)
    # would let both pass. The row lock (`SELECT ... FOR UPDATE`) must force
    # the second request to re-read a fresh refunded_amount and reject.
    barrier = threading.Barrier(2)
    results: list[int] = [None, None]  # type: ignore[list-item]

    def fire(index: int, key: str):
        with httpx.Client(base_url=live_server, timeout=10) as c:
            barrier.wait()
            resp = c.post(
                f"/orders/{order_id}/refunds",
                json={"amount": "400.00"},
                headers={"Idempotency-Key": key},
            )
            results[index] = resp.status_code

    t1 = threading.Thread(target=fire, args=(0, "race-a"))
    t2 = threading.Thread(target=fire, args=(1, "race-b"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert None not in results, f"a request never completed: {results}"
    assert results.count(201) == 1, f"expected exactly one 201, got {results}"
    assert results.count(422) == 1, f"expected exactly one 422, got {results}"

    with httpx.Client(base_url=live_server) as verify_client:
        order_after = verify_client.get(f"/orders/{order_id}").json()
        # 500 (seed) + 400 (winner) = 900 -- must never exceed captured_amount
        assert order_after["refunded_amount"] == "900.00"
        assert float(order_after["refunded_amount"]) <= float(
            order_after["captured_amount"]
        )
