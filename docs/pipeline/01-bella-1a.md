# BRD: Partial Refund + Double-Entry Ledger (Reference Project)

bd: A5 · Phase 1a · iter 1 · Owner: Bella (BA)

## 1. Problem Statement

Merchant ต้อง refund คำสั่งซื้อบางส่วน (partial refund) ให้ลูกค้า โดยระบบบัญชีต้องบันทึกเป็น double-entry ledger ที่ balance เสมอ. Scenario นี้เหมาะสำหรับ demo shode-house pipeline เพราะ (1) มี business rule ที่ดูง่ายแต่ผิดง่าย (rounding, over-refund, refund ซ้ำ) ซึ่งบังคับให้ domain gate (Felix) ต้อง involve จริง ไม่ใช่ rubber-stamp, (2) มี edge case ที่ AC ตรง ๆ ตามคำถาม user มักเขียนเป็น tautology ("refund แล้วต้องบันทึก refund") ทำให้เห็น value ของ spec-axis review ตอน Phase 3b ชัดเจน, (3) ขอบเขตเล็กพอ (1 aggregate: Order + Ledger) ให้ pipeline เดินจบใน scope ที่ควบคุมได้ และ (4) money-as-Decimal + ledger balance เป็น invariant ที่ verify อัตโนมัติได้ตรงไปตรงมา (sum(debit) == sum(credit)) เหมาะกับ automated test evidence.

## 2. Scope

### IN
- Partial refund ของ order ที่ capture เงินแล้ว (1 refund ต่อ 1 request, ทีละ order)
- Ledger entry แบบ double-entry (debit/credit) ต่อ refund หนึ่งครั้ง
- Validation: refund amount ≤ remaining refundable amount
- Idempotency สำหรับ refund request ซ้ำ (ด้วย idempotency key)
- Query: ดู refund history + ledger entries ของ order หนึ่งใบ

### OUT
- Authentication / authorization / KYC / user role
- Multi-currency (สมมติ THB สกุลเดียว, ไม่มี FX)
- Payment gateway integration จริง (mock/simulate capture เท่านั้น)
- Async queue / event bus / webhook notification
- Full refund (คนละ flow, ไม่ implement ใน scope นี้ — treat เป็น edge ของ partial ถ้า amount = full)
- Chargeback / dispute handling
- Multi-tenant / multi-merchant
- Reporting / reconciliation dashboard
- Refund approval workflow (manual review, threshold-based hold)
- Tax/fee recalculation บน refund
- Batch refund / bulk operation
- Order cancellation (คนละ domain event)
- Audit log UI (เก็บใน ledger table พอ ไม่ต้องมี audit trail service แยก)

## 3. User Stories + Acceptance Criteria

> หมายเหตุ reframing: AC ด้านล่างเขียนใหม่จากคำถามตรง ๆ เพื่อไม่ให้เป็น tautology ("refund แล้ว balance ต้อง balance" ไม่ตรวจอะไร) — เน้น observable state/invariant ที่ query ได้จริง

### US-01: ในฐานะ operator ฉันต้องการ refund บางส่วนของ order เพื่อคืนเงินลูกค้าโดยไม่ยกเลิกทั้งออเดอร์

- **AC-01 (happy path)**
  Given order ที่ capture แล้ว 1,000.00 THB (ledger: DR `1010 cash_clearing` 1,000.00 / CR `4010 sales_revenue` 1,000.00) และยังไม่เคย refund
  When operator ยิง partial refund 300.00 THB
  Then order.refunded_amount = 300.00, order.status = `PARTIALLY_REFUNDED`, order.refundable_amount = 700.00 และมี journal entry ใหม่ (DR `4090 sales_refunds` 300.00 / CR `1010 cash_clearing` 300.00) ที่ sum(debit) = sum(credit) ทั้ง ledger ของ order นี้

- **AC-02 (สะสมหลายครั้ง)**
  Given order 1,000.00 THB ที่ refund ไปแล้ว 300.00 THB
  When operator ยิง refund เพิ่ม 400.00 THB
  Then order.refunded_amount = 700.00 และมี ledger entry ใหม่แยกจาก entry เดิม (ไม่ merge/overwrite) รวม ledger entries ทั้งหมดของ order นี้ balance

### US-02: ในฐานะระบบ ฉันต้องการป้องกัน refund เกินยอดที่จ่ายจริง เพื่อไม่ให้เกิดยอดติดลบทางบัญชี

- **AC-03 (over-refund + concurrent request — easy-to-miss)**
  Given order 1,000.00 THB ที่ refund ไปแล้ว 700.00 THB (refundable เหลือ 300.00)
  When operator ยิง refund 400.00 THB — และแยกกรณี: (a) request เดี่ยว, (b) สอง request เข้าพร้อมกัน (เช่น 600.00 + 600.00 บน refundable 300.00) โดยระบบ lock แถว order (`SELECT ... FOR UPDATE`) ก่อน validate+insert
  Then ทุก request ที่ทำให้ยอดรวมเกิน refundable ถูก reject ด้วย error ที่ระบุ refundable_amount ปัจจุบัน, ไม่มี ledger entry/refund row ใหม่ถูกสร้างสำหรับ request ที่ reject, order.refunded_amount ไม่เปลี่ยน, และในกรณี concurrent มีเพียง request เดียวเท่านั้นที่ผ่าน (ห้ามทั้งคู่ผ่านพร้อมกันจน refunded_amount > captured_amount — DB `CHECK` เป็น defense-in-depth ชั้นสุดท้าย)

- **AC-04 (scale validation, no rounding — easy-to-miss)**
  Given refund amount เป็น input ตรงจาก operator (ไม่ใช่ค่าคำนวณจาก percentage — ไม่มี rounding path ใน scope นี้)
  When operator ส่ง amount ที่มีทศนิยมเกิน 2 ตำแหน่ง (เช่น `100.005`)
  Then request ถูก reject ด้วย 422 ทันที (ห้ามปัดเงียบ ๆ เป็น `100.01`) — validate เฉพาะ scale ≤ 2 ที่ API layer (`Decimal`, parse จาก string ห้ามผ่าน float) และ DB column เป็น `NUMERIC(18,2)`

### US-03: ในฐานะระบบ ฉันต้องการให้ refund request ที่ยิงซ้ำ (retry/network error) ไม่สร้าง ledger entry ซ้ำ

- **AC-05 (idempotency — easy-to-miss)**
  Given refund request ที่เคย process สำเร็จแล้วด้วย idempotency-key = K (refund 300.00 THB)
  When client ยิง request เดิมซ้ำด้วย idempotency-key = K อีกครั้ง (เช่นเพราะ timeout)
  Then ระบบ return ผลลัพธ์เดิม (refund record เดิม) โดยไม่สร้าง ledger entry ใหม่ และ order.refunded_amount ไม่เพิ่มซ้ำ

- **AC-06 (idempotency key ชนแต่ payload ต่าง — easy-to-miss)**
  Given idempotency-key = K ถูกใช้ไปแล้วกับ refund amount 300.00 THB
  When client ยิง request ใหม่ด้วย idempotency-key = K แต่ amount = 400.00 THB
  Then ระบบ reject ด้วย conflict error (ไม่ process เป็น refund ใหม่ และไม่ return ผลลัพธ์ของ K เดิมราวกับ amount ตรงกัน)

### US-04: ในฐานะ operator ฉันต้องการดูประวัติ refund และ ledger entries ของ order เพื่อ trace ธุรกรรม

- **AC-07**
  Given order ที่มี refund มาแล้ว 2 ครั้ง (300.00 และ 400.00 THB)
  When operator query refund history ของ order นั้น
  Then ได้ list 2 records เรียงตามเวลา แต่ละ record มี amount, timestamp, idempotency-key, ledger entry ids ที่เกี่ยวข้อง

- **AC-08 (refund หลัง refund เต็มยอด — easy-to-miss)**
  Given order 1,000.00 THB ที่ refund ครบ 1,000.00 THB แล้ว → order.status = `REFUNDED` (terminal, derived จาก refunded_amount == captured_amount ในทรานแซกชันเดียวกับ ledger insert ห้าม dual-write)
  When operator ยิง refund เพิ่มแม้เพียง 0.01 THB
  Then request ถูก reject ทันทีด้วย 422 ไม่ขึ้นกับว่าจำนวนน้อยแค่ไหน, **ไม่มี refund row และไม่มี ledger entry ใด ๆ ถูกสร้าง** (request ที่ reject ไม่ใช่ธุรกรรม — ไปอยู่ใน application log เท่านั้น), และ order.status ยังคงเป็น `REFUNDED`

**รวม: 4 user stories, 8 AC** (ตรงเงื่อนไข ≤4 stories / ≤8 AC จาก brief)

## 4. Open Questions — resolved (iter 1)

1. Ledger model: **confirmed by Felix → `02-felix-1b.md` §Q1** — contra entry ใหม่ append-only, ห้าม reverse/mutate sale entry เดิม, ทุก amount เป็นบวก ทิศทางอยู่ที่ column `direction`
2. Rounding/scale: **confirmed by Felix → `02-felix-1b.md` §Q2** — ไม่มี rounding ใน scope นี้ (amount เป็น input ตรง ไม่ใช่คำนวณ), validate scale ≤ 2 แทน (reject ถ้าเกิน), scale=2 อ้าง ISO 4217 THB (ยัง unverified primary source — ระบุใน `02-felix-1b.md` แล้ว)
3. Chart of accounts: **confirmed by Felix → `02-felix-1b.md` §Q3** — `1010 cash_clearing` (Asset/DR), `4010 sales_revenue` (Revenue/CR), `4090 sales_refunds` (Contra-revenue/DR); ตัวอย่างเดิมใน draft (`merchant_payable`/`customer_refund`) ถูกยกเลิก
4. Terminal status: **confirmed by Felix → `02-felix-1b.md` §Q4** — explicit status field `PAID → PARTIALLY_REFUNDED → REFUNDED` (derived, ห้าม dual-write) + DB `CHECK (refunded_amount >= 0 AND refunded_amount <= captured_amount)` + row-lock สำหรับ concurrency
5. Repo name: **confirmed by Oliver** — keep `shode-house-example-refund`

ไม่มี open question เหลือค้างที่ block Phase 1a sign-off ณ iter 1

## 5. Non-Goals (สรุปเจตนา ไม่ใช่ scope OUT list ซ้ำ)

- ไม่ตั้งใจ demo scalability/performance — dataset เล็ก, ไม่มี load test
- ไม่ตั้งใจ cover ทุก edge case ทางบัญชีจริง (เช่น multi-leg ledger, journal reversal ข้ามงวดบัญชี) — เลือกเฉพาะ edge ที่ทำให้เห็น domain-gate value
- ไม่ตั้งใจให้ codebase production-grade ครบ (ไม่มี rate limit, ไม่มี observability stack) — โฟกัสที่ "pipeline เดินได้ครบและ evidence ครบ" ไม่ใช่ "software พร้อม deploy จริง"
- ไม่ตั้งใจให้อ่านนานเกิน ~15 นาที — ถ้า scope โตเกินนี้ต้อง cut ไม่ใช่เพิ่ม AC

## 6. Artifact Map (เสนอโครง reference repo)

```
shode-house-example-refund/
├── README.md                          # elevator pitch + how to read the pipeline
├── docs/
│   └── pipeline/
│       ├── 00-oliver-brief.md         # kickoff brief (this bd's origin)
│       ├── 01-bella-1a.md             # this file — BRD/AC (BA)
│       ├── 02-sara-1a.md              # architecture/ADR (SA, parallel กับ 01)
│       ├── 03-uma-1b.md               # ถ้ามี UI (ไม่มีใน scope นี้ — อาจ skip)
│       ├── 02-felix-1b.md             # domain expert response ต่อ open question §4 (already answered)
│       ├── 05-dave-impl.md            # implementation notes (Phase 2)
│       ├── 06-chris-review.md         # code review (Phase 3b)
│       ├── 07-quinn-test.md           # test strategy + evidence (Phase 3b/4)
│       └── 08-triage-close.md         # bd close evidence (Oliver)
├── src/                                # FastAPI app
├── tests/                              # pytest
└── adr/                                 # ถ้า Sara แยก ADR ออกจาก docs/pipeline
```

หมายเหตุ: ลำดับเลขไฟล์ = ลำดับ phase จริงที่เกิดขึ้น ไม่ใช่ลำดับ agent id ตายตัว — ถ้า phase ไหน skip (เช่นไม่มี UI) ให้เว้นเลขว่างไว้พร้อม note สั้น ๆ ว่าทำไม skip เพื่อให้คนอ่านจากนอกไม่งงว่าไฟล์หาย

## 7. RTM (updated หลัง Felix ตอบ §4 — `02-felix-1b.md`)

| BR | FR | AC | Test (Quinn) |
|----|----|----|----|
| BR-01 partial refund ต้องบันทึก ledger balance | FR-01 POST /orders/{id}/refunds | AC-01, AC-02 | pending |
| BR-02 ห้าม refund เกินยอด (รวม concurrency) | FR-02 validate refundable_amount + row-lock | AC-03, AC-08 | pending |
| BR-03 refund amount ต้องมี scale ถูกต้อง (ไม่ปัดเงียบ) | FR-03 scale ≤ 2 validation, reject ไม่ round | AC-04 | pending |
| BR-04 refund request ต้อง idempotent | FR-04 idempotency-key handling | AC-05, AC-06 | pending |
| BR-05 ต้อง trace refund history | FR-05 GET /orders/{id}/refunds | AC-07 | pending |

Orphan check: ทุก FR ผูกกับ BR แล้ว, ทุก BR มี AC อย่างน้อย 1 — ไม่มี orphan ณ จุดนี้
