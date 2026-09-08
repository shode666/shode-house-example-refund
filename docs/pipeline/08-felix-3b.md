# Phase 3b — Domain Axis Review (Felix)

bd: A5 · iter 1 · read-only · ตรวจ code จริงเทียบ rules ใน `02-felix-1b.md`

> ⚠️ AI persona, training-cutoff knowledge — validate critical claims with CPA/official source

## Rule conformance

| Rule (02-felix-1b.md) | Evidence | ผล |
|---|---|---|
| CoA `1010`/`4010`/`4090` | `[service.py:22-24]` constants; `[models.py:79-81]` `CHECK account_code IN ('1010','4010','4090')` | ✅ |
| Sale = DR 1010 / CR 4010 | `[service.py:48-63]` DEBIT `1010` + CREDIT `4010` amount เดียวกัน journal เดียวกัน | ✅ |
| Refund = DR 4090 / CR 1010 | `[service.py:155-170]` DEBIT `4090` + CREDIT `1010` — ไม่ใช่ `merchant_payable`/`customer_refund` ของ AC-01 เดิม | ✅ |
| Append-only contra entry ไม่ reverse ของเดิม | `[service.py:152]` `journal_id = uuid.uuid4()` ใหม่ต่อ refund; refund #2 ไม่แตะ entry #1 | ✅ |
| ไม่มี UPDATE/DELETE บน `ledger_entries` | `[Grep 'delete\|update(\|UPDATE\|DELETE\|.merge(' src/ -i]` → match เดียวคือ `service.py:118 with_for_update()` (lock บน `orders` ไม่ใช่ ledger) · `[Grep 'LedgerEntry' src/]` → มีแต่ construct + `select` | ✅ |
| amount บวกเสมอ direction แยก column | `[models.py:82-85]` `CHECK direction IN ('DEBIT','CREDIT')` + `CHECK amount > 0` — ไม่มี negative amount | ✅ |
| Reject ไม่เขียน ledger/refund row | `[service.py:127-144]` raise `OverRefundError` ก่อน `session.add(refund)` ที่ :149 → ทั้ง refund และ ledger ไม่ถูกสร้าง | ✅ |
| Scale ≤ 2 · reject ไม่ปัด | `[schemas.py:28-31]` `exponent < -2` → raise "no rounding"; NaN/Inf ตกที่ `not isinstance(exponent, int)` | ✅ |
| Decimal ทุกชั้น ห้าม float | `[schemas.py:19-22]` บังคับ input เป็น string; `[models.py:46,67,100]` `Numeric(18,2)` ทุก money column | ✅ |
| Status derived ห้าม dual-write | `[service.py:31-37]` `derive_status()` pure function; `[main.py:54,67]` เรียกทุกจุด — ไม่มี status column ใน `[models.py:33-52]` | ✅ |
| `REFUNDED` terminal | `[service.py:125-134]` guard `refunded_amount >= captured_amount` ก่อน validate อื่น (AC-08) | ✅ |
| DB guard defense-in-depth | `[models.py:37-40]` `CHECK refunded_amount >= 0 AND <= captured_amount` | ✅ |

**ไม่พบ domain violation** — บัญชี ทิศทาง scale status ตรงกับ `02-felix-1b.md` ทุกข้อ

## Findings (3 · non-blocking ทั้งหมด)

- **F-1 🟡 test evidence อ่อน (Quinn)** — `[test_quinn_edge.py:51]` assert `debit == credit == 2000.00` เท่านั้น. **DR=CR ตรวจ direction inversion ไม่ได้**: ถ้า refund leg สลับเป็น `DR 1010 / CR 4090` ยอดยังบาลานซ์ 2000.00 เท่าเดิมและ test ผ่าน. ต้องเพิ่ม assert **per-account**: `1010` net = 0.00 · `4010` CR = 1000.00 · `4090` DR = 1000.00 — `[Grep '4090' tests/]` ไม่พบ assert ระดับ account เลย
- **F-2 🟡 ไม่มี reconciliation assert** — `orders.refunded_amount` `[service.py:173]` เป็น denormalized cache ที่ update แยกจาก ledger; ไม่มีที่ไหน (code หรือ test) ตรวจว่า `SUM(ledger 4090 DEBIT) == orders.refunded_amount`. โปรเจกต์ที่ธีมคือ ledger ควรมี invariant test ข้อนี้ 1 บรรทัด
- **F-3 🔵 INFO** — append-only เป็น **convention ระดับ code** ไม่ใช่ DB guarantee (ไม่มี trigger / REVOKE UPDATE,DELETE). ยอมรับได้สำหรับ demo แต่ควร note ใน README ไม่ให้คนลอกไปใช้จริงแล้วเข้าใจผิด

## Verdict

**DOMAIN CLEAN** — 0 blocking, 3 non-blocking (F-1/F-2 ส่ง Quinn เพิ่ม assert, F-3 ส่ง Dave เติม README note)
