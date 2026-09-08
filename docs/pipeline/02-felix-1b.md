# Domain Validation: Partial Refund + Double-Entry Ledger

bd: A5 · Phase 1b · iter 0 · Owner: Felix (Fintech Domain) · ตอบ § 4 ของ `01-bella-1a.md`

> ⚠️ AI persona, training-cutoff knowledge — validate critical claims with accountant/CPA or official source

**Citation status ของเอกสารนี้**: คำตอบ Q1/Q3/Q4 เป็น **accounting convention + system-design practice** ไม่ใช่ regulation ที่ผมอ้าง clause ได้
> ⚠️ **General guidance from training memory** (not source-verified) — ต้อง validate กับ CPA / TFRS-IFRS document ฉบับปัจจุบันก่อน implement ถ้าจะเอาไปใช้ production
> คำตอบ Q2 (scale = 2) อ้าง `ISO 4217 — THB minor unit = 2` — ยังไม่เปิด primary source ตรวจ amendment ล่าสุด, ให้ verify ก่อน

โปรเจกต์นี้เป็น **reference/demo ไม่ใช่ระบบเงินจริง** → เลือก "simplest correct" ทุกข้อ

---

## Q1 — Ledger entry model: reversal หรือ contra entry ใหม่?

**ตอบ: contra entry ใหม่ append-only. ห้าม reverse/mutate entry เดิมของ sale**

เหตุผล:
1. Ledger ต้อง immutable — entry ที่ post แล้วห้ามแก้/ลบ. Partial refund คือ **economic event ใหม่** คนละ event กับ sale ไม่ใช่การ "แก้ sale ให้ถูก"
2. Refund หลายครั้งต่อ order เดียว → ถ้า reverse entry เดิมทีละส่วน จะไม่มี audit trail ว่าคืนกี่ครั้ง เมื่อไหร่ (พัง AC-07 โดยตรง)
3. Reversal (storno) เหมาะกับ **correction ของ entry ที่ลงผิด** เท่านั้น — refund ไม่ใช่ error correction

**กติกาบังคับ**: ทุกจำนวนใน ledger เป็น **บวกเสมอ** ทิศทางอยู่ที่ column `direction ∈ {DEBIT, CREDIT}` — ห้ามใช้ negative amount แทน credit (ทำให้ invariant `sum(debit) == sum(credit)` ตรวจไม่ได้)

---

## Q2 — Rounding rule + scale

**ตอบ: refund path นี้ไม่ควรมี rounding เลย**

- Scope IN (§ 2) ไม่มี percentage-based refund — refund amount เป็น **input จาก operator** ไม่ใช่ค่าที่คำนวณ
- ดังนั้นกติกาที่ถูกคือ **validate scale ไม่ใช่ round**: reject ถ้า amount มีทศนิยม > 2 ตำแหน่ง (เช่น `100.005` → 422 ไม่ใช่ปัดเป็น `100.01`) — ปัดเงินของลูกค้าเงียบ ๆ ห้ามเด็ดขาด
- Scale = 2 สำหรับ THB (`ISO 4217 — THB minor unit = 2`, ยังไม่ verify ฉบับปัจจุบัน)

**Implementation**:
| ชั้น | กติกา |
|---|---|
| API input | `Decimal` เท่านั้น — parse จาก string ห้ามผ่าน float; `-x.as_tuple().exponent <= 2` มิฉะนั้น reject |
| PostgreSQL | `NUMERIC(18,2)` ทุก money column (ห้าม `float8`/`money` type) |
| Python | `decimal.Decimal`; ถ้ามีจุดต้องปัดจริงในอนาคต → `ROUND_HALF_UP` (ไม่ใช่ default `ROUND_HALF_EVEN` ของ Python) |
| Invariant | `sum(refunds.amount) <= order.captured_amount` เป๊ะ ไม่มี tolerance/epsilon |

*(banker's rounding เหมาะกับงาน statistical allocation ไม่ใช่ refund ที่มี counterparty รายเดียว — ผลรวมต้องตรงเป๊ะ ไม่ใช่ unbiased)*

---

## Q3 — Chart of Accounts

`merchant_payable` / `customer_refund` ใน AC-01 **ไม่ถูก** — คู่นั้นเป็น liability ↔ liability ไม่ได้บันทึกว่าเงินสดไหลออก และไม่ลดรายได้ → งบไม่สะท้อนความจริง

**CoA ขั้นต่ำ 3 บัญชี** (พอสำหรับ scope นี้):

| Code | Name | Type | Normal balance |
|---|---|---|---|
| `1010` | `cash_clearing` | Asset | Debit |
| `4010` | `sales_revenue` | Revenue | Credit |
| `4090` | `sales_refunds` | Contra-revenue | Debit |

ทำไม contra-revenue (`4090`) ไม่ใช่ debit `4010` ตรง ๆ: เก็บ **gross sales** ไว้ครบ ทำให้ query "ขายเท่าไร / คืนเท่าไร" แยกกันได้ — ต้นทุนเท่ากัน (1 บัญชีเพิ่ม) ได้ข้อมูลมากกว่า

**Out of scope (ตั้งใจตัด)**: โลกจริง refund แยก 2 ขา — accrue (`DR 4090 / CR 2010 refunds_payable`) แล้วค่อย settle ตอน payout T+n (`DR 2010 / CR 1010`). โปรเจกต์นี้ capture เป็น mock ไม่มี settlement window จริง → **ใช้ขาเดียว ชน `cash_clearing` ตรง ๆ** ถูกต้องพอและอ่านจบเร็วกว่า

---

## Q4 — Order status เมื่อ refund สะสมครบเต็มยอด

**ตอบ: ต้องมี status field explicit ไม่ใช่ปล่อยให้ `refundable_amount = 0` ลอย ๆ**

`PAID` → `PARTIALLY_REFUNDED` → `REFUNDED` (terminal)

- `REFUNDED` เข้าถึงได้ทั้งจาก refund ครั้งเดียวเต็มยอด และจาก partial สะสมครบ — **ไม่แยก flow** (ตรงกับ § 2 OUT ที่ตัด full refund flow ออก)
- **status = derived ห้าม dual-write**: คำนวณจาก `refunded_amount` vs `captured_amount` ตอนอ่าน หรือ update ใน transaction เดียวกับ ledger insert — ห้ามมี code path ที่ set status ได้เองแยก
- Guard ระดับ DB (defense in depth สำหรับ AC-03/AC-08):
  `CHECK (refunded_amount >= 0 AND refunded_amount <= captured_amount)`
- **Concurrency**: `SELECT ... FOR UPDATE` บนแถว order ก่อน validate + insert — ไม่งั้น refund 600 + 600 พร้อมกันบน order 1,000 ผ่านทั้งคู่ (AC-03 ไม่ครอบ race นี้ → ดู § AC changes)

---

## Ledger entries ที่ต้อง implement (order 1,000.00 THB)

**1) Sale / capture**
| Account | DR | CR |
|---|---|---|
| `1010 cash_clearing` | 1,000.00 | |
| `4010 sales_revenue` | | 1,000.00 |
→ `status = PAID`, `refunded_amount = 0.00`, `refundable = 1,000.00`

**2) Partial refund #1 — 300.00**
| Account | DR | CR |
|---|---|---|
| `4090 sales_refunds` | 300.00 | |
| `1010 cash_clearing` | | 300.00 |
→ `status = PARTIALLY_REFUNDED`, `refunded_amount = 300.00`, `refundable = 700.00`

**3) Partial refund #2 — 700.00 (สะสมครบเต็ม)**
| Account | DR | CR |
|---|---|---|
| `4090 sales_refunds` | 700.00 | |
| `1010 cash_clearing` | | 700.00 |
→ `status = REFUNDED` (terminal), `refunded_amount = 1,000.00`, `refundable = 0.00`
→ entry ใหม่แยกจากครั้งแรก **ห้าม merge** — ledger มี 3 journal entries / 6 lines

**4) Over-refund ที่ถูก reject — ยิงเพิ่ม 0.01**
| Account | DR | CR |
|---|---|---|
| *(ไม่มี entry ใด ๆ)* | — | — |
→ HTTP 422 + body ระบุ `refundable_amount: "0.00"`; **ห้ามเขียน ledger row และห้ามเขียน refund row** — ledger บันทึกเฉพาะ event ที่เกิดจริง, request ที่ถูกปฏิเสธไม่ใช่ธุรกรรม (ไปอยู่ใน application log)

**Balance หลังจบ**: `1010` = 0.00 · `4010` = 1,000.00 CR · `4090` = 1,000.00 DR · `sum(DR) == sum(CR) == 2,000.00` ✅

---

## Verdict

**RULES-CONFIRMED** — ทั้ง 4 ข้อตอบได้ครบ ไม่มี blocker ที่ต้องถาม user เพิ่ม
