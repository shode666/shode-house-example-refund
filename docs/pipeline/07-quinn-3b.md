# QA Review (Integration/E2E + Spec axis): A5 Partial Refund + Ledger

bd: A5 · Phase 3b · iter 1 · Owner: Quinn (QA) · Scope: `/home/claude/shode-house-example-refund/`

Verdict default = FAIL until own-run evidence proves otherwise (per adversary stance). Did not trust Dave's/Chris's pasted output — re-ran everything myself against a fresh DB.

## (a) Integration / E2E — own-run evidence

Env: PG 16 cluster already running `/tmp/pg-refund` port 5433 (owner `claude`). Booted real `uvicorn` (not pytest ASGI shortcut, not in-process) against a **fresh throwaway DB `refund_quinn`** on port 8901, drove the full flow with `curl`.

```
1. POST /orders {captured_amount:1000.00} -> 201 {refundable:1000.00, status:PAID}
2. POST /orders/{id}/refunds key=q-r1 amount=300.00 -> 201 {order_status:PARTIALLY_REFUNDED}
3. POST /orders/{id}/refunds key=q-r2 amount=700.00 -> 201 {order_status:REFUNDED}
4. POST /orders/{id}/refunds key=q-r3 amount=0.01   -> 422 {"error":"order already fully refunded","refundable_amount":"0.00"}
5. Replay key=q-r1 amount=300.00 (same key/amount)  -> 200, SAME refund id (5cf23c00...), no new ledger row
6. GET /orders/{id}/ledger ->
   totals {"debit":"2000.00","credit":"2000.00"}  -- sum(DR)==sum(CR) confirmed
   6 entries, exactly matching Felix's worked example (02-felix-1b.md §"Ledger entries ที่ต้อง implement"):
     1010 DEBIT 1000.00 / 4010 CREDIT 1000.00      (sale, journal_id A)
     4090 DEBIT  300.00 / 1010 CREDIT  300.00      (refund #1, journal_id B, refund_id=5cf23c00)
     4090 DEBIT  700.00 / 1010 CREDIT  700.00      (refund #2, journal_id C, refund_id=31e6c7d8)
7. GET /orders/{id} -> {refunded_amount:1000.00, refundable_amount:0.00, status:REFUNDED}
8. GET /orders/{id}/refunds -> 2 records, amount 300.00 then 700.00, ordered, each with idempotency_key + ledger_entry_ids(2)
```

Ledger shape/amounts/account codes/journal grouping = **exact match** to Felix's spec (`02-felix-1b.md`), not just AC-01/02 wording.

**Verdict (a): E2E GREEN** — real uvicorn, real Postgres, real HTTP, full journey seed→300→700→reject-1→replay→ledger-verify, no mocks.

## (b) Spec axis — AC-01..08 + SEC-01..03, adversarial

| # | Covered by | Adversarial check | Verdict |
|---|---|---|---|
| AC-01 | `test_ac01_happy_path...` + my E2E step 1-2 | Assertion checks `debit==credit`, not tautological | ✅ |
| AC-02 | `test_ac02_accumulated...` | Checks 6 ledger lines, distinct refund ids, no merge | ✅ |
| AC-03 | `test_ac03a_...` + `test_concurrency.py` (real 2-thread barrier race, real TCP) | Race genuinely exercises `FOR UPDATE`: exactly 1×201/1×422, final=900.00 never >captured | ✅ |
| AC-04 | `test_ac04_...` + `test_unit.py` (3 variants incl. float-input reject) | 100.005 → 422 confirmed; boundary (exactly 2 decimals) also tested accept-path | ✅ |
| AC-05 | `test_ac05_...` + my E2E step 5 | Replay returns identical id, no dup ledger row — verified with real HTTP round-trip, not in-process | ✅ |
| AC-06 | `test_ac06_...` | Same key + diff amount → 409, refunded_amount unaffected | ✅ |
| AC-07 | `test_ac07_...` | Order preserved, ledger_entry_ids present per record | ✅ |
| AC-08 | `test_ac08_...` + my E2E step 3-4 | 0.01 over full-refunded order → 422, zero new rows, status stays REFUNDED | ✅ |
| SEC-01 | `test_sec01_...` + `test_unit.py` + Chris's scientific-notation regression (`test_chris_sec01_*`, now closed) | Negative/zero/oversized all 422 pre-transaction; scientific notation bypass (Chris's 🔴) confirmed fixed live: `curl 1E+20` → 422 (was 500) | ✅ |
| SEC-02 | `test_sec02_...` | Cross-order key hijack → 409, order B untouched | ✅ |
| SEC-03 | `test_sec03_...` + `test_unit.py` | Extra field (`refunded_amount` in body) → 422 via `extra="forbid"` | ✅ |

**11/11 covered.** No missing requirement found. No scope creep found (extra tests `test_refund_missing_idempotency_key_header_rejected`, `test_refund_on_unknown_order_returns_404` are defensive, match FastAPI's own 422/404 semantics, not undocumented behavior).

### Edge cases the existing suite skipped — verified myself (own-run, `curl` + 3 added tests)

- **Refund exactly == remaining balance via accumulation** (not tested before: AC-02 accumulates to 700/1000, never exactly to 1000 via two partials) → manual E2E (300+700=1000→REFUNDED) + new `tests/test_quinn_edge.py::test_quinn_accumulated_refund_exact_remaining_transitions_to_refunded` — PASS.
- **0.01 smallest-unit refund on a fresh order** → curl EDGE B, 201, correct PARTIALLY_REFUNDED — no off-by-scale bug.
- **Idempotency-key reuse after a 409 conflict, then correct-amount replay of the same key** (does the 409 attempt poison the key or corrupt state?) → curl EDGE C: 201→409→200(clean replay, same id, ledger still 4 entries, not 6) + new `test_quinn_conflict_then_correct_key_replay_does_not_double_write` — PASS. **No bug found**, but this was genuinely untested before (Chris/Dave's suite never interleaves a 409 attempt then re-replays the original).
- **GET on unknown order** — `create_refund` already had a named 404 test, but `GET /orders/{id}`, `GET /orders/{id}/ledger`, `GET /orders/{id}/refunds` did not → curl EDGE D + new `test_quinn_all_get_endpoints_404_on_unknown_order` — all three 404. **No bug found**, gap now closed with evidence.
- Malformed UUID in path → FastAPI/Pydantic returns 422 automatically (not a custom `DomainError`), consistent, no gap.

### "Looks done but wrong" check
None found. Cross-checked Chris's iter-1 re-verify (scientific-notation fix, narrowed `except IntegrityError`) against my own curl repro — both hold under my independent run, not just re-reading Chris's paste.

## Tests added (≤3, no product code touched)
`tests/test_quinn_edge.py` — 3 tests: `test_quinn_accumulated_refund_exact_remaining_transitions_to_refunded`, `test_quinn_conflict_then_correct_key_replay_does_not_double_write`, `test_quinn_all_get_endpoints_404_on_unknown_order`.

## pytest — full suite, own run

```
$ export DATABASE_URL="postgresql+psycopg://postgres@localhost:5433/refund_test"
$ .venv/bin/pytest -v
======================== 28 passed, 1 warning in 1.84s =========================
```
(25 Dave/Chris + 3 Quinn, 0 failed, 0 skipped, 0 flaky — no retry needed)

## Verdict

- **(a) Integration/E2E: GREEN** — real uvicorn + real Postgres, full flow + ledger invariant verified live, matches Felix's spec exactly.
- **(b) Spec axis: 11/11 covered**, 0 missing requirement, 0 scope creep, 0 "looks done but wrong". 3 previously-untested edge cases probed and closed with new evidence + tests — no new bug found, but coverage gap on "409-then-correct-replay" and "GET 404 on ledger/refunds paths" is now closed.

**Combined bd A5 3b: both Standards (Chris, 7-DIM CLEAN) and QA (this report) are clean. No route-back needed.**

Quinn ▸ Oliver : Phase 3b QA axis PASS, ready for Phase 4 close (A5)

## Follow-up (Felix F-1/F-2, `08-felix-3b.md`)

`test_quinn_accumulated_refund_exact_remaining_transitions_to_refunded` now asserts per-account+direction (1010 DEBIT=1000.00, 4010 CREDIT=1000.00, 4090 DEBIT=1000.00, 1010 CREDIT=1000.00 — a direction inversion would now fail) plus `SUM(4090 DEBIT) == orders.refunded_amount`. Product code untouched.

`pytest`: `28 passed, 1 warning in 1.71s`
