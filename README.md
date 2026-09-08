# Partial Refund + Double-Entry Ledger (reference project)

This is a **generated example**, produced end-to-end via the [shode-house]
pipeline (BA -> Domain Expert -> Architect -> Threat Model -> Implementation)
to demonstrate the pipeline's output quality, not to be a starting point for
a real payments system. All upstream artifacts that drove this code are in
[`docs/pipeline/`](docs/pipeline/):

| File | Phase | Author | Content |
|---|---|---|---|
| `01-bella-1a.md` | 1a | Bella (BA) | BRD, 4 user stories, 8 acceptance criteria |
| `02-felix-1b.md` | 1b | Felix (Fintech domain expert) | Ledger rules, chart of accounts, rounding/scale policy |
| `03-sara-1a.md` | 1a | Sara (Architect) | File tree, schema, API, ADRs, test strategy |
| `04-sentinel-1c.md` | 1c | Sentinel (Threat model) | STRIDE pass, SEC-01/02/03 |
| `05-dave-2.md` | 2 | Dave (Developer) | Implementation evidence, AC→test map, iter 1 fix |
| `06-chris-3b.md` | 3b | Chris (Code review) | 7-dim review — 1 🔴 found (scientific-notation amount → 500), fixed, re-verified |
| `07-quinn-3b.md` | 3b | Quinn (QA) | Real E2E over uvicorn + Spec axis 11/11 |
| `08-felix-3b.md` | 3b | Felix (Domain) | Ledger code vs rules — DOMAIN CLEAN, 2 test-strength findings fixed |

## What this is not

- **Not production-ready.** No auth, no rate limiting, no observability
  stack — see "Security notes" below.
- **Not a template to copy-paste for real money movement.** The ledger
  rules (append-only, chart of accounts) came from Felix, an AI persona —
  see the disclaimer in `docs/pipeline/02-felix-1b.md` — validate with a
  real accountant/CPA before using anything like this with real funds.

## Security notes (no auth by design)

Every endpoint in this service is **public — there is no authentication or
authorization**. This was an explicit scope decision (`docs/pipeline/01-bella-1a.md`
§2 OUT), not an oversight. Order IDs are UUIDv4 and act as a weak capability
token (`docs/pipeline/04-sentinel-1c.md` §2 A8 "accepted risk").

**Do not deploy this as-is or use it as a template for anything internet-facing
without adding authentication, authorization, and rate limiting.**

## Domain rules (in one paragraph)

Refunds are recorded as new, append-only "contra" ledger journal entries —
never as edits/reversals of the original sale entry. Every ledger amount is
positive; direction (`DEBIT`/`CREDIT`) lives in its own column. Chart of
accounts: `1010 cash_clearing` (asset), `4010 sales_revenue` (revenue),
`4090 sales_refunds` (contra-revenue). Order status (`PAID` ->
`PARTIALLY_REFUNDED` -> `REFUNDED`) is *derived* from `refunded_amount` vs
`captured_amount`, never stored, so it can never drift out of sync
(`docs/pipeline/03-sara-1a.md` ADR-001). Full detail: `docs/pipeline/02-felix-1b.md`.

## Running the API

```bash
docker compose up -d db
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/refund" \
    .venv/bin/uvicorn app.main:app --app-dir src --reload
```

## Running tests

### With docker (macOS / most humans)

```bash
make test
```

That's the only command you need — `make test` creates `.venv` and installs
the package (only if `.venv/bin/pytest` doesn't exist yet), spins up
`db_test` via docker compose, runs pytest, then tears the container down.
Needs Python >=3.12 on `PATH` as `python3.12` or `python3`; if neither is
new enough, `make test` fails with a clear error naming the interpreter it
found instead of a cryptic `No such file or directory`.

**Prerequisites**: docker compose + Python >= 3.12. macOS ships 3.9, so bring
your own interpreter and point `make` at it once (the `.venv` is reused after):

```bash
# Homebrew
brew install python@3.12 && make test
# or conda
conda create -y -n py312 python=3.12 && make test PYTHON="$(conda run -n py312 which python)"
```

Verified on macOS (docker compose + conda 3.12): `28 passed in 1.84s`.

### Without docker (this sandbox has no docker — throwaway local cluster instead)

```bash
export PATH=/usr/lib/postgresql/16/bin:$PATH
initdb -D /tmp/pg-refund/data -U postgres --auth=trust -E UTF8
pg_ctl -D /tmp/pg-refund/data -l /tmp/pg-refund/server.log -o "-p 5433" start
createdb -h localhost -p 5433 -U postgres refund_test

make test-local
```

`make test-local` (like `make test`) creates `.venv` on demand and installs
the package, then runs pytest against whatever `DATABASE_URL` points at.
`tests/conftest.py` defaults `DATABASE_URL` to
`postgresql+psycopg://postgres@localhost:5433/refund_test` — override the
env var if your cluster differs.

## AC -> test mapping

| AC/SEC | Test |
|---|---|
| AC-01 | `tests/test_refund_api.py::test_ac01_happy_path_partial_refund` |
| AC-02 | `tests/test_refund_api.py::test_ac02_accumulated_refunds_create_separate_entries` |
| AC-03(a) | `tests/test_refund_api.py::test_ac03a_single_over_refund_request_rejected` |
| AC-03(b) | `tests/test_concurrency.py::test_ac03_concurrent_refunds_serialize` |
| AC-04 | `tests/test_unit.py::test_ac04_scale_validation_rejects_more_than_2_decimals` + `tests/test_refund_api.py::test_ac04_api_rejects_scale_over_2_decimals` |
| AC-05 | `tests/test_refund_api.py::test_ac05_idempotent_replay_returns_same_result_no_duplicate` |
| AC-06 | `tests/test_refund_api.py::test_ac06_idempotency_key_reused_with_different_amount_conflicts` |
| AC-07 | `tests/test_refund_api.py::test_ac07_refund_history_lists_two_records_in_order` |
| AC-08 | `tests/test_refund_api.py::test_ac08_refund_after_fully_refunded_rejected` |
| SEC-01 | `tests/test_unit.py::test_sec01_bound_validation_rejects_at_schema_layer` + `tests/test_refund_api.py::test_sec01_bound_validation_rejects_before_transaction` |
| SEC-02 | `tests/test_refund_api.py::test_sec02_idempotency_key_reused_across_orders_conflicts` |
| SEC-03 | `tests/test_unit.py::test_sec03_extra_field_rejected` + `tests/test_refund_api.py::test_sec03_extra_field_in_refund_request_rejected` |

The full pytest evidence run for this mapping is recorded in the shode-house
pipeline output directory for this bd (`05-dave-2.md`), not inside this repo.
