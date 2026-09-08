# Implementation: Partial Refund + Double-Entry Ledger (reference project)

bd: A5 · Phase 2 · iter 0 · Owner: Dave

## Refs Used
- `01-bella-1a.md` (8 AC, RTM)
- `02-felix-1b.md` (ledger rules, CoA 1010/4010/4090, append-only)
- `03-sara-1a.md` (file tree, schema, API, ADR-001..003, test strategy — followed as-is)
- `04-sentinel-1c.md` (SEC-01, SEC-02, SEC-03 — all three implemented + tested; SEC-02 required an ADR-003 logic revision: idempotency replay now checks `order_id` in addition to `amount`, per Sentinel's explicit note)

## Project location
`/home/claude/shode-house-example-refund/`

## Environment evidence

```
$ python3.12 --version
Python 3.12.3

$ ls /usr/lib/postgresql/16/bin | head
clusterdb createdb createuser dropdb dropuser initdb ...

$ psql -h localhost -p 5433 -U postgres -d refund_test -c 'select version();'
 PostgreSQL 16.13 (Ubuntu 16.13-0ubuntu0.24.04.1) on x86_64-pc-linux-gnu, ...
```

Throwaway cluster created (run as unprivileged `claude` user, since `initdb` refuses root):
```
initdb -D /tmp/pg-refund/data -U postgres --auth=trust -E UTF8
pg_ctl -D /tmp/pg-refund/data -l /tmp/pg-refund/server.log -o "-p 5433 -k /tmp/pg-refund" start
createdb -h localhost -p 5433 -U postgres refund_test
```
`DATABASE_URL=postgresql+psycopg://postgres@localhost:5433/refund_test` (matches `tests/conftest.py` default; also matches the port docker-compose's `db_test` service maps to `5433:5432`, so `make test` on macOS uses the same DSN shape).

Dependency versions actually installed in `.venv` (`pip freeze`):
```
fastapi==0.141.1  sqlalchemy==2.0.52  psycopg==3.3.5  psycopg-binary==3.3.5
pydantic==2.13.5  pytest==9.1.1  httpx==0.28.1  uvicorn==0.52.4
```

## Files created (line counts, `wc -l`)

```
src/app/__init__.py       0
src/app/db.py            26
src/app/errors.py        31
src/app/models.py       103
src/app/schemas.py      103
src/app/service.py      209
src/app/main.py          135
tests/conftest.py         81 (updated during verify)
tests/test_unit.py        58
tests/test_refund_api.py 206
tests/test_concurrency.py 84
```
Plus: `pyproject.toml`, `setup.cfg`, `Makefile`, `docker-compose.yml`, `README.md`, `.gitignore`,
`docs/pipeline/{01-bella-1a.md, 02-felix-1b.md, 03-sara-1a.md, 04-sentinel-1c.md}` (copied verbatim from `outputs/A5/`).

8 source files under `src/app/` — within the ≤12 budget from Sara's tree. No files added beyond the tree in `03-sara-1a.md` §1 (kept `service.py`/`errors.py`/`models.py`/`schemas.py`/`main.py`/`db.py` exactly as named there).

## Deviation from Sara's pseudo-code (disclosed, not silent)

`03-sara-1a.md` §4 step 1-2 only compared `amount` on idempotency-key replay. Per Sentinel's SEC-02 finding (`04-sentinel-1c.md` §3, §4 "Sara ack SEC-02... ก่อน Dave implement"), `src/app/service.py::_handle_replay_or_conflict` now checks `order_id` **before** `amount`: same key + different order → 409 unconditionally (cross-order replay hijack blocked), same key + same order + different amount → 409 (AC-06, unchanged). This is the ADR-003 revision Sentinel asked Sara to make; implemented directly since it's a small, testable, non-blocking correction and Sentinel's finding is authoritative on security AC.

## Verify (Philosophy 2) — full pytest run

```
$ cd /home/claude/shode-house-example-refund
$ export DATABASE_URL="postgresql+psycopg://postgres@localhost:5433/refund_test"
$ .venv/bin/pytest -v
============================= test session starts ==============================
collected 23 items

tests/test_concurrency.py::test_ac03_concurrent_refunds_serialize PASSED [  4%]
tests/test_refund_api.py::test_ac01_happy_path_partial_refund PASSED     [  8%]
tests/test_refund_api.py::test_ac02_accumulated_refunds_create_separate_entries PASSED [ 13%]
tests/test_refund_api.py::test_ac03a_single_over_refund_request_rejected PASSED [ 17%]
tests/test_refund_api.py::test_ac04_api_rejects_scale_over_2_decimals PASSED [ 21%]
tests/test_refund_api.py::test_ac05_idempotent_replay_returns_same_result_no_duplicate PASSED [ 26%]
tests/test_refund_api.py::test_ac06_idempotency_key_reused_with_different_amount_conflicts PASSED [ 30%]
tests/test_refund_api.py::test_ac07_refund_history_lists_two_records_in_order PASSED [ 34%]
tests/test_refund_api.py::test_ac08_refund_after_fully_refunded_rejected PASSED [ 39%]
tests/test_refund_api.py::test_sec01_bound_validation_rejects_before_transaction PASSED [ 43%]
tests/test_refund_api.py::test_sec02_idempotency_key_reused_across_orders_conflicts PASSED [ 47%]
tests/test_refund_api.py::test_sec03_extra_field_in_refund_request_rejected PASSED [ 52%]
tests/test_refund_api.py::test_refund_missing_idempotency_key_header_rejected PASSED [ 56%]
tests/test_refund_api.py::test_refund_on_unknown_order_returns_404 PASSED [ 60%]
tests/test_unit.py::test_ac04_scale_validation_rejects_more_than_2_decimals PASSED [ 65%]
tests/test_unit.py::test_ac04_scale_validation_accepts_exactly_2_decimals PASSED [ 69%]
tests/test_unit.py::test_ac04_scale_validation_rejects_float_input PASSED [ 73%]
tests/test_unit.py::test_sec01_bound_validation_rejects_at_schema_layer[-300.00] PASSED [ 78%]
tests/test_unit.py::test_sec01_bound_validation_rejects_at_schema_layer[0.00] PASSED [ 82%]
tests/test_unit.py::test_sec01_bound_validation_rejects_at_schema_layer[99999999999999999.00] PASSED [ 86%]
tests/test_unit.py::test_sec01_bound_validation_accepts_max_valid_amount PASSED [ 91%]
tests/test_unit.py::test_sec03_extra_field_rejected PASSED               [ 95%]
tests/test_unit.py::test_status_derivation_paid_partial_refunded_refunded PASSED [100%]

============================== 23 passed in 1.58s ===============================
```

Concurrency test re-run 3x standalone to check for flakiness — all green (`1 passed in ~0.8s` each run).

`test_ac03_concurrent_refunds_serialize` (`tests/test_concurrency.py`) spins up a **real uvicorn TCP server** (not an in-process ASGI transport) and fires two real HTTP requests from two Python threads synchronized with a `threading.Barrier`, each requesting 400.00 refund against an order with refundable=500.00 (so each request is individually valid but together they'd exceed refundable — the actual race). Result asserted: exactly one `201`, exactly one `422`, and `order.refunded_amount` ends at exactly `900.00` (never exceeds `captured_amount`). This proves the `SELECT ... FOR UPDATE` row lock, not just single-request validation.

## Manual smoke (real HTTP, separate throwaway DB `refund_smoke`)

```
$ curl -s -X POST localhost:8899/orders -d '{"captured_amount":"1000.00"}'
{"id":"6192be8d-...","captured_amount":"1000.00","refunded_amount":"0.00","refundable_amount":"1000.00","status":"PAID"}

$ curl -s -X POST localhost:8899/orders/<id>/refunds -H 'Idempotency-Key: smoke-1' -d '{"amount":"300.00"}'
{"id":"70e8be38-...","order_id":"6192be8d-...","amount":"300.00", ...,"order_status":"PARTIALLY_REFUNDED"}

$ curl -s localhost:8899/orders/<id>/ledger
{"entries":[... 4 lines ...],"totals":{"debit":"1300.00","credit":"1300.00"}}

$ curl -s -X POST localhost:8899/orders/<id>/refunds -H 'Idempotency-Key: smoke-bad' -d '{"amount":"5000.00"}'
{"error":"refund exceeds refundable amount","refundable_amount":"700.00"}
```

## AC / SEC → test mapping

| AC/SEC | Test |
|---|---|
| AC-01 | `test_refund_api.py::test_ac01_happy_path_partial_refund` |
| AC-02 | `test_refund_api.py::test_ac02_accumulated_refunds_create_separate_entries` |
| AC-03(a) | `test_refund_api.py::test_ac03a_single_over_refund_request_rejected` |
| AC-03(b) concurrency | `test_concurrency.py::test_ac03_concurrent_refunds_serialize` |
| AC-04 | `test_unit.py::test_ac04_scale_validation_rejects_more_than_2_decimals` (+ `test_ac04_scale_validation_accepts_exactly_2_decimals`, `test_ac04_scale_validation_rejects_float_input`) and `test_refund_api.py::test_ac04_api_rejects_scale_over_2_decimals` |
| AC-05 | `test_refund_api.py::test_ac05_idempotent_replay_returns_same_result_no_duplicate` |
| AC-06 | `test_refund_api.py::test_ac06_idempotency_key_reused_with_different_amount_conflicts` |
| AC-07 | `test_refund_api.py::test_ac07_refund_history_lists_two_records_in_order` |
| AC-08 | `test_refund_api.py::test_ac08_refund_after_fully_refunded_rejected` |
| SEC-01 | `test_unit.py::test_sec01_bound_validation_rejects_at_schema_layer` (+ `test_sec01_bound_validation_accepts_max_valid_amount`) and `test_refund_api.py::test_sec01_bound_validation_rejects_before_transaction` |
| SEC-02 | `test_refund_api.py::test_sec02_idempotency_key_reused_across_orders_conflicts` |
| SEC-03 | `test_unit.py::test_sec03_extra_field_rejected` and `test_refund_api.py::test_sec03_extra_field_in_refund_request_rejected` |

Additional (not in RTM, added defensively): `test_refund_missing_idempotency_key_header_rejected`, `test_refund_on_unknown_order_returns_404`.

11/11 AC+SEC items have ≥1 named test. Total: 23 passed, 0 failed, 0 skipped.

## What I could not do / did not do

- **Did not run `make test` (docker path)** — no docker in this sandbox (per task brief). Verified instead with the throwaway local PG cluster exactly as instructed; `docker-compose.yml` + `Makefile` are shipped but unexercised here. Flag for whoever runs this on macOS: please confirm `make test` actually works end-to-end there — I could not verify it myself.
- Did not add Alembic — Sara's doc explicitly says `metadata.create_all` is sufficient for this reference project (single schema creation, no migrations needed).
- Left `FastAPI` startup DB init on `lifespan` handler (had to switch from `@app.on_event("startup")` after hitting a `DeprecationWarning`; functionally identical, cleaner).
- No auth/rate-limit implemented — explicitly out of scope (`01-bella-1a.md` §2 OUT + `04-sentinel-1c.md` §1); README states this loudly per Sentinel's ask.

## Decisions + R0/R1/R2

- Idempotency replay check order = look up by key globally, then branch on `order_id` first (SEC-02), then `amount` (AC-06) — R2 (easy, test-covered, reversible).
- Live-uvicorn-thread test strategy instead of ASGITransport for `client` fixture — `httpx.Client` + `ASGITransport` in the installed httpx 0.28.1 only supports async use (`handle_async_request`); switching all HTTP-based tests to a real TCP server made both the ordinary integration tests and the concurrency test consistent and provably real (no in-process shortcut). R2.
- This whole project is R1 at most (reference/demo, reversible, no real money) — no R0 actions taken.

## Hand-off

- Uma: N/A — no frontend/UI in this project (pure backend API).
- Chris: review `service.py` (transaction/locking logic), `schemas.py` (validation), `models.py` (constraints) — 7-dim + mutation testing.
- Quinn: integration/contract test against `/openapi.json` (auto-generated by FastAPI, not hand-written); load/E2E out of scope per Bella's non-goals but worth a sanity pass on concurrency test determinism across repeated CI runs.

---

## Iter 1 — Chris FAIL fix (bd A5, Phase 2, iter 1, from Oliver)

Source: `06-chris-3b.md` verdict FAIL, 1🔴 + 3🟡 + 3🔵. Fixed the 🔴 (must-fix) and one 🟡 cheaply as instructed; left the other 🟡/🔵 items untouched (tracked, not blocking).

### Diff summary

**`src/app/schemas.py`** (`_parse_money`) — 🔴 fix
Bug: when `Decimal.as_tuple().exponent >= 0` (scientific notation like `"1E+20"`), the old code set `integer_digits = len(digits)` and ignored the trailing zeros implied by the positive exponent, so `"1E+20"` (21 integer digits) was scored as 1 digit and passed the 16-digit `MAX_INTEGER_DIGITS` bound. It then reached Postgres and raised `psycopg.errors.NumericValueOutOfRange` → unhandled 500 (exactly Sentinel's SEC-01 abuse case A5).
Fix: `integer_digits = max(num_digits + exp, 0) if exp < 0 else num_digits + exp` — pads by the positive exponent instead of dropping it.

```diff
-    sign, digits, exp = amount.as_tuple()
-    num_digits = len(digits)
-    integer_digits = num_digits + exp if exp < 0 else num_digits
+    sign, digits, exp = amount.as_tuple()
+    num_digits = len(digits)
+    integer_digits = max(num_digits + exp, 0) if exp < 0 else num_digits + exp
```

**`src/app/service.py`** (`create_refund` except block) — 🟠/🟡 cheap fix (≤5 lines requested)
Bug: bare `except IntegrityError` reinterpreted *any* integrity violation (FK, CHECK, etc.) as an idempotency-key race, not just the specific unique-violation it was meant to catch.
Fix: inspect `exc.orig.diag.constraint_name`; only treat it as a replay race if the constraint is `refunds_idempotency_key_key` (verified real constraint name via `psql \d refunds`, see evidence below); anything else re-raises.

```diff
-    except IntegrityError:
-        # Race: two requests with the same idempotency_key committed concurrently.
+    except IntegrityError as exc:
+        # Race: two requests with the same idempotency_key committed concurrently.
+        # Narrowed per Chris (06-chris-3b.md 🟠): only reinterpret the specific
+        # idempotency-key unique-violation as a replay race; any other
+        # integrity error (FK violation, CHECK constraint, etc.) re-raises.
         session.rollback()
+        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
+        if constraint != "refunds_idempotency_key_key":
+            raise
         winner = _existing_refund_by_key(session, idempotency_key)
```

Left as-is (per instruction "leave 🟡 nits" / only handle the cheap-narrow item): logging/observability gap, coverage/mutation/property-based tooling gap, dead `IdempotencyKeyMissing`, `Mapped["Numeric"]` typing, unbounded `idempotency_key` length. Did not edit `tests/test_unit.py::test_chris_sec01_*` (as instructed).

### Constraint-name verification (NO MAGIC — didn't guess the constraint name)

```
$ psql -h localhost -p 5433 -U postgres -d refund_test -c '\d refunds'
...
Indexes:
    "refunds_pkey" PRIMARY KEY, btree (id)
    "refunds_idempotency_key_key" UNIQUE CONSTRAINT, btree (idempotency_key)
```

### Verify (Philosophy 2) — full pytest run, iter 1

```
$ export DATABASE_URL="postgresql+psycopg://postgres@localhost:5433/refund_test"
$ .venv/bin/pytest -v
============================= test session starts ==============================
collected 25 items

tests/test_concurrency.py::test_ac03_concurrent_refunds_serialize PASSED [  4%]
tests/test_refund_api.py::test_ac01_happy_path_partial_refund PASSED     [  8%]
tests/test_refund_api.py::test_ac02_accumulated_refunds_create_separate_entries PASSED [ 12%]
tests/test_refund_api.py::test_ac03a_single_over_refund_request_rejected PASSED [ 16%]
tests/test_refund_api.py::test_ac04_api_rejects_scale_over_2_decimals PASSED [ 20%]
tests/test_refund_api.py::test_ac05_idempotent_replay_returns_same_result_no_duplicate PASSED [ 24%]
tests/test_refund_api.py::test_ac06_idempotency_key_reused_with_different_amount_conflicts PASSED [ 28%]
tests/test_refund_api.py::test_ac07_refund_history_lists_two_records_in_order PASSED [ 32%]
tests/test_refund_api.py::test_ac08_refund_after_fully_refunded_rejected PASSED [ 36%]
tests/test_refund_api.py::test_sec01_bound_validation_rejects_before_transaction PASSED [ 40%]
tests/test_refund_api.py::test_sec02_idempotency_key_reused_across_orders_conflicts PASSED [ 44%]
tests/test_refund_api.py::test_sec03_extra_field_in_refund_request_rejected PASSED [ 48%]
tests/test_refund_api.py::test_refund_missing_idempotency_key_header_rejected PASSED [ 52%]
tests/test_refund_api.py::test_refund_on_unknown_order_returns_404 PASSED [ 56%]
tests/test_unit.py::test_ac04_scale_validation_rejects_more_than_2_decimals PASSED [ 60%]
tests/test_unit.py::test_ac04_scale_validation_accepts_exactly_2_decimals PASSED [ 64%]
tests/test_unit.py::test_ac04_scale_validation_rejects_float_input PASSED [ 68%]
tests/test_unit.py::test_sec01_bound_validation_rejects_at_schema_layer[-300.00] PASSED [ 72%]
tests/test_unit.py::test_sec01_bound_validation_rejects_at_schema_layer[0.00] PASSED [ 76%]
tests/test_unit.py::test_sec01_bound_validation_rejects_at_schema_layer[99999999999999999.00] PASSED [ 80%]
tests/test_unit.py::test_sec01_bound_validation_accepts_max_valid_amount PASSED [ 84%]
tests/test_unit.py::test_sec03_extra_field_rejected PASSED               [ 88%]
tests/test_unit.py::test_status_derivation_paid_partial_refunded_refunded PASSED [ 92%]
tests/test_unit.py::test_chris_sec01_rejects_scientific_notation_oversized_amount PASSED [ 96%]
tests/test_unit.py::test_chris_sec01_rejects_scientific_notation_small_oversized_amount PASSED [100%]

============================== 25 passed in 1.79s ==============================
```

Re-ran full suite 3x standalone to confirm no flakiness introduced: `25 passed in 1.69s` / `1.81s` / `1.70s`.

Live re-repro of Chris's exact exploit (`curl -X POST /orders -d '{"captured_amount":"1E+20"}'` against a fresh throwaway DB `refund_smoke2`):
```
HTTP_STATUS:422
{"detail":[{"type":"value_error","loc":["body","captured_amount"],"msg":"Value error, amount integer part exceeds 16 digits","input":"1E+20","ctx":{"error":{}}}]}
```
(was `HTTP_STATUS:500` / `NumericValueOutOfRange` before the fix, per Chris's evidence in `06-chris-3b.md`).

### Files changed (iter 1)
- `src/app/schemas.py` — 4 lines changed (digit-counting fix + comment)
- `src/app/service.py` — 5 lines added (constraint-name narrowing)
- `tests/test_unit.py` — unchanged (Chris's `test_chris_sec01_*` tests untouched, now pass)

