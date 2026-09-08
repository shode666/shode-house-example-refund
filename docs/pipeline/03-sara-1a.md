# Architecture: Partial Refund + Double-Entry Ledger (Reference Project)

bd: A5 · Phase 1a · iter 0 · Owner: Sara (SA)

```
[Sara — Greenfield project]
- Verified: Glob outputs/A5/** → เจอเฉพาะ 00-oliver-brief.md, 01-bella-1a.md, 02-felix-1b.md (ไม่มี code เดิม)
- Proposing stack ตามที่ user pin: Python 3.12 / FastAPI / SQLAlchemy 2 / PostgreSQL 16 / pytest — ไม่มี stack เดิมให้ inherit
```

Ledger rules ยึดตาม `02-felix-1b.md` (RULES-CONFIRMED) ทั้งหมด — ไม่ re-decide ในเอกสารนี้

## 1. Component View + File Tree

```
Client ──HTTP──> [FastAPI app (single process)]
                   routes ──> service (tx boundary) ──> SQLAlchemy 2 ──> PostgreSQL 16 (docker compose)
```
Modular monolith ไฟล์เดียวต่อ concern — ไม่มี layer ceremony (ตาม brief: อ่านจบ ~15 นาที, ≤ 12 source files):

```
src/app/__init__.py     # empty
src/app/main.py         # FastAPI app + 4 routes + error handler mapping
src/app/db.py           # engine, session factory, create_all (ไม่ใช้ alembic — ดูหมายเหตุ)
src/app/models.py       # ORM: Order, Refund, LedgerEntry + DB constraints
src/app/schemas.py      # Pydantic: request/response (Decimal from string, scale validator)
src/app/service.py      # create_order(), create_refund() — transaction + lock + idempotency
src/app/errors.py       # domain exceptions → HTTP status
tests/conftest.py       # PG fixture (compose) + truncate-per-test
tests/test_unit.py      # unit: ไม่แตะ DB
tests/test_refund_api.py    # integration: happy path + idempotency + query
tests/test_concurrency.py   # integration: AC-03(b) two-session race
docker-compose.yml · pyproject.toml
```
หมายเหตุ migration: reference project ตารางเกิดครั้งเดียว → `metadata.create_all` ที่ startup พอ (SQLAlchemy `CheckConstraint`/`UniqueConstraint` ลง DDL ครบ); alembic = over-engineering ที่ scope นี้

## 2. DB Schema + Constraint → AC mapping

```sql
orders (
  id UUID PK,
  captured_amount NUMERIC(18,2) NOT NULL CHECK (captured_amount > 0),
  refunded_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  CHECK (refunded_amount >= 0 AND refunded_amount <= captured_amount)   -- Felix §Q4
)
refunds (
  id UUID PK,
  order_id UUID NOT NULL REFERENCES orders(id),
  amount NUMERIC(18,2) NOT NULL CHECK (amount > 0),
  idempotency_key TEXT NOT NULL UNIQUE,
  created_at timestamptz NOT NULL
)
ledger_entries (
  id UUID PK,
  journal_id UUID NOT NULL,              -- group 2 lines ของ event เดียวกัน
  order_id UUID NOT NULL REFERENCES orders(id),
  refund_id UUID NULL REFERENCES refunds(id),   -- NULL = sale/capture journal
  account_code TEXT NOT NULL CHECK (account_code IN ('1010','4010','4090')),  -- Felix §Q3
  direction TEXT NOT NULL CHECK (direction IN ('DEBIT','CREDIT')),            -- Felix §Q1
  amount NUMERIC(18,2) NOT NULL CHECK (amount > 0),                           -- positive เสมอ
  created_at timestamptz NOT NULL
)
```

| Constraint | ครอบ AC |
|---|---|
| `orders CHECK refunded_amount <= captured_amount` | AC-03, AC-08 (defense-in-depth ชั้นสุดท้าย) |
| `NUMERIC(18,2)` ทุก money column | AC-04 (scale ระดับ DB; API reject ก่อนถึง — ไม่ปัดเงียบ) |
| `refunds.idempotency_key UNIQUE` | AC-05, AC-06 (กัน insert ซ้ำแม้ race) |
| `ledger amount > 0` + `direction CHECK` | AC-01, AC-02 (Felix §Q1: ห้าม negative amount) |
| `refund_id`/`order_id` FK NOT NULL | AC-07 (trace refund → ledger entry ids) |

Balance invariant `sum(DR)==sum(CR)` ต่อ journal: enforce ที่ service (insert 2 lines ใน tx เดียว, ไม่มี code path เขียน line เดี่ยว) + integration test ตรวจทั้ง ledger — cross-row CHECK/trigger ใน DB = over-engineering ที่ scope นี้ (ระบุใน ADR-002)

## 3. API

| Endpoint | Request | Success | Errors → AC |
|---|---|---|---|
| `POST /orders` (seed helper) | `{"captured_amount": "1000.00"}` | 201 `{id, captured_amount, refunded_amount, refundable_amount, status}` + sale journal (DR 1010 / CR 4010) | 422 scale/≤0 |
| `POST /orders/{id}/refunds` | header `Idempotency-Key` (บังคับ) + `{"amount": "300.00"}` | 201 refund ใหม่ · 200 replay ผลเดิม (AC-05) | 422 over-refund/terminal + body มี `refundable_amount` (AC-03, AC-08) · 422 scale > 2 (AC-04) · 409 key ซ้ำแต่ amount ต่าง (AC-06) · 400 ไม่ส่ง key · 404 order |
| `GET /orders/{id}/refunds` | — | 200 list เรียงเวลา: `{id, amount, created_at, idempotency_key, ledger_entry_ids[]}` (AC-07) | 404 |
| `GET /orders/{id}/ledger` | — | 200 `{entries[], totals: {debit, credit}}` (AC-01/02 verify) | 404 |

Money ทุกตัวใน JSON = **string** (`"300.00"`) กัน float ทั้งขาเข้า-ออก; Pydantic parse เป็น `Decimal` จาก string เท่านั้น. Refund response ระบุ `status` ปัจจุบันของ order ด้วย. ไม่ทำ openapi.yaml แยกไฟล์ — FastAPI generate `/openapi.json` จาก schema เดียวกับ runtime (contract-first เต็มรูปเกิน scope reference project; Quinn ใช้ generated spec ได้เลย)

## 4. Transaction / Concurrency (AC-03) + Idempotency (AC-05/06)

```
create_refund(order_id, key, amount):                    # ทั้งหมดใน DB transaction เดียว
 1  existing = SELECT * FROM refunds WHERE idempotency_key = key
 2  if existing: amount ตรง → return 200 ผลเดิม (AC-05) | ต่าง → 409 (AC-06)
 3  order = SELECT * FROM orders WHERE id = ? FOR UPDATE   # serialize ทุก refund ต่อ order (AC-03b)
 4  if order.refunded_amount == captured_amount → 422 terminal (AC-08)
 5  if amount > captured_amount - refunded_amount → 422 + refundable_amount (AC-03a)  # ไม่มี write ใด ๆ
 6  INSERT refund · INSERT 2 ledger lines (journal_id เดียว) · UPDATE orders.refunded_amount += amount
 7  COMMIT → 201
 8  except IntegrityError(idempotency_key):               # race สอง request key เดียวกันพร้อมกัน
 9      ROLLBACK → อ่าน refund ที่ชนะ → ทำข้อ 2 ซ้ำ (replay หรือ 409)
10  DB CHECK (refunded_amount <= captured_amount) = ชั้นสุดท้ายถ้า logic ข้างบนพลาด
```
Scale validation (AC-04) เกิดที่ Pydantic ก่อนเปิด transaction — reject 422 ไม่ round

## 5. ADR

**ADR-001 — Order status: derived, ไม่มี column ใน DB**
Context: Felix §Q4 บังคับ status derived ห้าม dual-write. Decision: ไม่เก็บ column เลย — compute `PAID/PARTIALLY_REFUNDED/REFUNDED` จาก `refunded_amount` vs `captured_amount` ตอน serialize response. Consequence: dual-write bug เป็นไปไม่ได้เชิงโครงสร้าง; trade-off: query by status ต้องคำนวณ (ไม่มี use case นี้ใน scope)

**ADR-002 — Ledger append-only, balance enforce ที่ service + test**
Context: Felix §Q1 immutable, positive amount + direction column. Decision: ไม่มี UPDATE/DELETE path ใน code; balance ต่อ journal enforce โดย insert คู่ DR/CR ใน tx เดียวจาก helper เดียว + integration test assert `sum(DR)==sum(CR)`; ไม่ทำ DB trigger ตรวจ cross-row. Consequence: อ่านง่าย/ไฟล์น้อย; ยอมรับว่า DB ไม่ block ledger เอียงถ้ามีคนเขียน SQL ตรง (acceptable — reference project)

**ADR-003 — Idempotency เก็บใน refunds table เอง ไม่แยก table**
Context: มี mutating endpoint เดียว, ต้อง detect key ซ้ำ + payload ต่าง (AC-06). Decision: `idempotency_key UNIQUE` (scope global) บน refunds + เทียบ `amount` ที่เก็บไว้ตัดสิน replay vs 409; ไม่ทำ idempotency table แยก/ไม่ hash payload. Consequence: ไฟล์และ join น้อยลง; trade-off: ถ้าอนาคตมีหลาย endpoint ต้อง refactor (out of scope)

## 6. Test Strategy (pyramid ตามจริงของงานนี้)

| ระดับ | AC | เครื่องมือ |
|---|---|---|
| Unit (ไม่แตะ DB) | AC-04 (scale validator), status derivation ของ ADR-001, error mapping | pytest ล้วน |
| Integration (PG จริงจาก docker compose — ไม่ใช้ testcontainers เพิ่ม dependency) | AC-01, 02, 03a, 05, 06, 07, 08 + ledger balance + DB CHECK ยิงตรง | pytest + httpx client + PG fixture |
| Concurrency | AC-03b: 2 session ยิงพร้อมกัน (thread + barrier) → ผ่านแค่ 1 | pytest แยกไฟล์ |

จงใจ integration-heavy: AC ส่วนใหญ่คือ transactional invariant ที่ mock DB แล้วพิสูจน์อะไรไม่ได้ — pyramid 70/20/10 มาตรฐานไม่ honest กับงานนี้ และ SQLite แทน PG ไม่ได้ (FOR UPDATE / NUMERIC semantics ต่าง). ไม่มี E2E/UI (ไม่มี UI)

## 7. Open Questions

ไม่มีข้อ blocking — ทุก decision ปิดด้วย user pin, Felix rules, หรือ ADR-001..003 ข้างบน

## 8. Hand-off

- Dave (Phase 2): implement ตาม §1 tree + §4 pseudo — ห้ามเพิ่มไฟล์เกิน tree โดยไม่แจ้ง
- Quinn (Phase 3b): ใช้ §6 + RTM ใน `01-bella-1a.md` §7; contract check จาก `/openapi.json`
- Cross-ref FR: FR-01→§3+§5 ADR-002 · FR-02→§4 บรรทัด 3-5 + DB CHECK · FR-03→§4 scale note · FR-04→ADR-003 · FR-05→§3 GET endpoints ✅
