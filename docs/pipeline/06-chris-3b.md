# Code Review (Standards axis): A5 Partial Refund + Ledger

bd: A5 · Phase 3b · iter 0 · Owner: Chris (CR) · Scope: whole tree `/home/claude/shode-house-example-refund/`

## Verdict: **FAIL** — 1 🔴 Critical (must-fix before merge)

## Own-run evidence

- venv: `/home/claude/shode-house-example-refund/.venv` (python3.12, pytest 9.1.1)
- PG cluster already running at `/tmp/pg-refund` port 5433 (started as OS user `claude`, not root)
- `su claude -c "unset SSL_CERT_FILE; .venv/bin/pytest -v"` (before adding my probes): **`23 passed, 1 warning in 1.59s`**
- After adding 2 `test_chris_*` probe tests (see below): **`2 failed, 23 passed, 2 warnings in 1.53s`** — the 2 failures are my own new tests, confirming the bug below with real output, not "should be fine."
- Live-server exploit repro (uvicorn on `refund_smoke` DB, `curl -X POST /orders -d '{"captured_amount":"1E+20"}'`) → `HTTP_STATUS:500`, server log: `sqlalchemy.exc.DataError: (psycopg.errors.NumericValueOutOfRange) numeric field overflow`.

Dave's `05-dave-2.md` evidence claim (23/23 green) is **verified independently** — matches my own run before I added probes.

## Findings

### 🔴 Critical — SEC-01 bound validation bypass via scientific-notation Decimal (exactly Sentinel's A5 abuse case)

`src/app/schemas.py:38-41` `_parse_money`:
```python
sign, digits, exp = amount.as_tuple()
num_digits = len(digits)
integer_digits = num_digits + exp if exp < 0 else num_digits
```
When `exp >= 0` (i.e. the client sends scientific notation, e.g. `"1E+20"`), `integer_digits` is set to `len(digits)` and silently **ignores the trailing zeros the positive exponent implies**. `Decimal("1E+20").as_tuple()` → `digits=(1,), exponent=20`, so `integer_digits = 1`, well under `MAX_INTEGER_DIGITS = 16`, even though the real value has 21 integer digits. The amount also passes the `<= -2` scale check (`exp=20` is not `< -2`) and the `> 0` check.

This is the **exact abuse case Sentinel flagged as SEC-01** (`04-sentinel-1c.md` §2 A5 "Oversized amount ... DB error 500 / overflow behavior ไม่ define"). Sara's ADR says API should reject before opening a transaction; instead this input reaches Postgres and blows up:

```
sqlalchemy.exc.DataError: (psycopg.errors.NumericValueOutOfRange) numeric field overflow
DETAIL: A field with precision 18, scale 2 must round to an absolute value less than 10^16.
```
→ unhandled 500 (confirmed live, `curl` output above), not the clean 422 SEC-01 requires. Also breaks the "money always JSON string, `Decimal` from string" contract in spirit: `Decimal("1E+2")` round-trips as `"1E+2"` in JSON, not `"100.00"`, if it ever got past validation on a value the DB *could* store.

**Fix** (concrete): compute integer digit count from the *value*, not just `len(digits)`:
```python
# after computing `amount` and confirming exponent <= -2 already rejected:
sign, digits, exp = amount.as_tuple()
if exp < 0:
    integer_digits = max(len(digits) + exp, 0)
else:
    integer_digits = len(digits) + exp   # exp>=0: pad by the positive exponent
if integer_digits > MAX_INTEGER_DIGITS:
    raise ValueError(...)
```
Or simpler/more robust: reject any input string containing `e`/`E` outright before `Decimal()` parses it (scientific notation is never a legitimate refund-amount format), or normalize with `amount = amount.normalize()` and re-derive digits, or just compare `abs(amount) >= Decimal(10) ** MAX_INTEGER_DIGITS` instead of counting tuple digits.

Added regression probes (fail today, prove the gap — remove `test_chris_*` prefix once Dave fixes and keep as permanent regression tests):
- `tests/test_unit.py::test_chris_sec01_rejects_scientific_notation_oversized_amount` (`"1E+20"`)
- `tests/test_unit.py::test_chris_sec01_rejects_scientific_notation_small_oversized_amount` (`"1E+17"`, 18 digits, still over the 16-digit bound)

Route: → Phase 2 Dave (implementation fix in `schemas.py`), then re-run `tests/test_unit.py -k chris` + full suite.

### 🟡 Medium — broad `except IntegrityError` in `create_refund` can mask unrelated integrity errors

`src/app/service.py:178-184`: the `try` block wraps refund insert, ledger inserts, *and* the `order.refunded_amount` update. `except IntegrityError` is meant to catch only the idempotency-key race (SEC-02/AC-05/06 replay), but it will also swallow, e.g., an FK violation or the `ck_orders_refunded_bounds` CHECK firing from a real bug elsewhere, then silently try to resolve it as "someone else won the idempotency race." The `winner is None: raise` fallback limits blast radius, but a *coincidental* existing row with that same key (from an unrelated call) could return a wrong 200/409 instead of surfacing the real bug. Narrow the catch (e.g. inspect `exc.orig.diag.constraint_name == "refunds_idempotency_key_key"` before treating it as a replay) or move the ledger/refund inserts that don't need this reinterpretation outside the same `except` umbrella.

### 🟡 Medium — zero logging/observability anywhere in the app

`grep -rn "logging\|logger" src/` → no hits. `01-bella-1a.md` AC-08 explicitly says rejected over-refund/idempotency-conflict requests "ไปอยู่ใน application log เท่านั้น" (go into the application log) — but there is no log statement in `service.py` or `errors.py` for any rejected path (`OverRefundError`, `IdempotencyConflict`, `OrderNotFound`). There's also no log line for the unhandled-exception path shown above, other than FastAPI/uvicorn's default traceback dump (which is fine for this reference project but not "log context enough to trace" per dim 7). Track for next iteration; not a merge-blocker for a no-auth demo repo, but the spec makes an explicit claim the code doesn't back up.

### 🟡 Medium — Mandatory Test Quality gates not met (mutation / property-based / coverage tooling absent)

`pyproject.toml` has no `pytest-cov`/coverage config; `.venv` has no `mutmut`/`hypothesis` installed (`pip list | grep -iE 'coverage|mutmut|hypothesis'` → empty). Sara's `03-sara-1a.md` §6 deliberately scoped this as an integration-heavy pyramid for a small reference repo, and the AC/SEC → test RTM in the README is complete and each test is meaningfully specific (not tautological — see below), but per Chris's mandatory bar this is a gap: no measured mutation-kill rate, no property-based test for the pure `_parse_money`/`derive_status` functions (both are good property-test candidates: "any Decimal string with ≤2 decimal places, 1 ≤ integer-digits ≤16, value>0 round-trips" / "derive_status is monotonic in refunded_amount"). Track, don't block — this is a demo repo per Bella's non-goals (`01-bella-1a.md` §5), but flag so it isn't silently treated as "fine because AC count is high."

### 🔵 Low — dead code / cosmetic

- `errors.py:24-25` `IdempotencyKeyMissing` is defined but never raised (`main.py:92` raises `HTTPException` directly instead) — pick one path.
- `models.py:46-49,67,100` use `Mapped["Numeric"]` (string forward-ref to the SQLAlchemy type, not the Python type) where `Mapped[Decimal]` is correct and would let type-checkers actually verify usage.
- `refunds.idempotency_key` has no max length (`String` unbounded) — not exploitable given no auth/rate-limit is already accepted risk, but free to bound (e.g. `String(255)`) as defense-in-depth.

## What's solid (own-run verified, not just read)

- Decimal end-to-end: `grep -rn float src/ tests/` → only comments/docstrings and one `float()` cast **inside a test assertion** (`tests/test_concurrency.py:82`, comparing already-Decimal-exact strings) — no float in any money code path. That one test-side `float()` is harmless here (values are exact) but is a style smell against the "no float anywhere" invariant; nit only.
- Transaction boundary + `FOR UPDATE` placement (`service.py:118`) matches Sara's pseudocode exactly: lock happens after the idempotency-replay short-circuit and before the over-refund/terminal checks, inside the same transaction as the refund+ledger insert+order update — verified live via `test_ac03_concurrent_refunds_serialize` (real uvicorn + 2 threads + barrier, not ASGI in-process shortcut): exactly 1×201 / 1×422, final `refunded_amount == "900.00"`. Not tautological — the test genuinely races two `400.00` refunds against a `500.00` refundable window, which only serializing row-lock logic can pass consistently.
- SEC-02 idempotency correctly order-bound: global-unique `idempotency_key` at DB + service-layer `_handle_replay_or_conflict` checks `order_id` before `amount`, matching Sentinel's SEC-02 fix — `test_sec02_idempotency_key_reused_across_orders_conflicts` passes for real (409, order B untouched).
- DB constraints match Felix's rules 1:1: CoA `CHECK account_code IN ('1010','4010','4090')`, `direction IN ('DEBIT','CREDIT')`, all amount columns `CHECK (... > 0)`, `orders.refunded_amount` bounds CHECK as defense-in-depth.
- AC-08/derive_status: status is genuinely derived (no stored column), computed in the same read path every time — no dual-write path exists to drift.

## Action items

- **Block merge**: fix `_parse_money` integer-digit counting for non-negative-exponent Decimals (🔴). Re-run `pytest tests/test_unit.py -k chris` + full suite until green.
- Track (P2/P3): narrow `except IntegrityError` (🟡), add logging for rejected/error paths (🟡), add coverage/mutation/property-based tooling if this repo's role expands beyond "demo" (🟡).
- Low/nitpick: dead `IdempotencyKeyMissing`, `Mapped["Numeric"]` typing, idempotency-key length bound (🔵).

**Standards axis: 4 findings (1🔴 / 0🟠 / 3🟡) + 3🔵 nit. Worst: 🔴 SEC-01 scientific-notation bound-check bypass (unhandled 500, matches Sentinel A5 abuse case).**

---

## Iter 1 re-check (bd A5, Phase 3b, iter 1, from Oliver)

Source: Dave's fix in `05-dave-2.md` "Iter 1 — Chris FAIL fix" section. Re-verified independently (own-run, not trusting Dave's pasted output).

### Code diff verified in place

- `src/app/schemas.py:43-45` — `integer_digits = max(num_digits + exp, 0) if exp < 0 else num_digits + exp` (was `... else num_digits`). Read directly from disk, matches Dave's claimed diff.
- `src/app/service.py:178-190` — `except IntegrityError as exc:` now inspects `exc.orig.diag.constraint_name` and only treats it as an idempotency replay race when the constraint is `refunds_idempotency_key_key`; anything else re-raises. Read directly from disk, matches Dave's claimed diff.
- Constraint name independently confirmed (not taken on trust): `su claude -c "psql -h localhost -p 5433 -U postgres -d refund_chris_recheck -c '\d refunds'"` → `"refunds_idempotency_key_key" UNIQUE CONSTRAINT, btree (idempotency_key)`. Matches the hardcoded string exactly — no typo/mismatch risk.

### pytest — own run

```
$ su claude -c "unset SSL_CERT_FILE; .venv/bin/pytest -v"
...
tests/test_unit.py::test_chris_sec01_rejects_scientific_notation_oversized_amount PASSED [ 96%]
tests/test_unit.py::test_chris_sec01_rejects_scientific_notation_small_oversized_amount PASSED [100%]
======================== 25 passed, 1 warning in 1.49s =========================
```
Both of my own `test_chris_sec01_*` regression probes (which failed pre-fix) now pass. `test_ac03_concurrent_refunds_serialize` (the race that exercises this exact `except IntegrityError` path) still passes — the narrowed catch did not break the genuine replay-race handling.

### curl repro — own run, fresh throwaway DB (`refund_chris_recheck`, not reused from iter 0)

```
$ curl -s -X POST http://127.0.0.1:8900/orders -d '{"captured_amount":"1E+20"}' -w '\nHTTP_STATUS:%{http_code}\n'
{"detail":[{"type":"value_error","loc":["body","captured_amount"],"msg":"Value error, amount integer part exceeds 16 digits","input":"1E+20","ctx":{"error":{}}}]}
HTTP_STATUS:422
```
Was `HTTP_STATUS:500` / `NumericValueOutOfRange` in iter 0. **Closed, confirmed independently.**

Also checked the boundary Dave didn't mention (18-digit case, `"1E+17"`) and a normal happy-path order to rule out over-tightening:
```
$ curl ... -d '{"captured_amount":"1E+17"}'  -> HTTP_STATUS:422 (correctly rejected, 18 integer digits > 16)
$ curl ... -d '{"captured_amount":"1000.00"}' -> HTTP_STATUS:201 (normal path unaffected)
```

### Narrowed `except IntegrityError` — no new hole found

- Verified the hardcoded constraint name against the live schema (above) rather than trusting Dave's paste — matches.
- The only other integrity constraints on `refunds`/`orders`/`ledger_entries` (`ck_refunds_amount_positive`, FK to `orders`, the CHECK bounds on `orders.refunded_amount`) are all guarded well before this transaction reaches the insert (schema-layer SEC-01 validation + `FOR UPDATE` + refundable-amount check), so in practice this except block should only ever see the idempotency-key unique violation in normal operation — the narrowing is a correct defense-in-depth tightening, not a new gap.
- Did not find a driver-portability concern worth blocking on: project is Postgres/psycopg-only (`db.py:14` DSN hardcodes `postgresql+psycopg`), so `exc.orig.diag` is always populated for `psycopg.Error`.

### Remaining open items (unchanged, not re-litigated this iter)

Per Dave's note, the other 🟡/🔵 from iter 0 (logging/observability gap, coverage/mutation/property-based tooling, dead `IdempotencyKeyMissing`, `Mapped["Numeric"]` typing, unbounded idempotency-key length) were intentionally left untouched — still open, still non-blocking (track P2-P3).

### Verdict: 7-DIM CLEAN

🔴 closed with own-run evidence, no regressions, no new hole in the narrowed except. No remaining Critical/High blockers on the Standards axis for this bd.
