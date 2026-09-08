# Threat Model (STRIDE quick pass): Partial Refund + Ledger

bd: A5 · Phase 1c · iter 0 · Owner: Sentinel · Input: `01-bella-1a.md`, `03-sara-1a.md`

## 1. Scope + Out of scope (pinned: NO AUTH)

Asset: `orders.refunded_amount` (money) · `ledger_entries` (append-only ledger) · `refunds.idempotency_key`
Trust boundary เดียว: Client (untrusted) ↔ FastAPI. Project นี้ **ไม่มี authentication/authorization โดยเจตนา** [01-bella-1a.md:19] ดังนั้น **out of scope**: Spoofing (ไม่มี identity ให้ปลอม), Elevation of Privilege (ไม่มี role), Repudiation ระดับ actor (log ไม่รู้ว่าใครยิง), DoS/rate-limit [01-bella-1a.md:101 non-goal], TLS/session/CSRF.
🔴 **README ของ example ต้องระบุชัด**: "no auth by design — ทุก endpoint เปิด public; ห้ามใช้เป็น template production โดยไม่เพิ่ม authn/authz + rate-limit" (มอบ Dave ใส่ตอน Phase 2)

## 2. Abuse cases → coverage

| # | Abuse case (attacker as anonymous client) | STRIDE | ครอบโดย | Verdict |
|---|---|---|---|---|
| A1 | Over-refund: ยิง refund เกิน refundable เอาเงินเกิน capture | T | AC-03 (validate + reject) + DB `CHECK refunded_amount <= captured_amount` [03-sara-1a.md:45] | ✅ covered |
| A2 | Replay: retry key เดิมหวังได้ refund ซ้ำ | T | AC-05 + `idempotency_key UNIQUE` [03-sara-1a.md:51] | ✅ covered |
| A3 | Race: 2 request พร้อมกันบน refundable เดียว | T | AC-03(b) `SELECT FOR UPDATE` + DB CHECK ชั้นสุดท้าย [03-sara-1a.md:93,100] | ✅ covered |
| A4 | Negative amount (`"-300.00"`) ทำให้ refunded_amount ลด / ledger เอียง | T | DB `CHECK (amount > 0)` [03-sara-1a.md:50,61] — แต่ **API layer ไม่มี AC reject ≤ 0** → หลุดไปตาย 500 ที่ DB | ⚠️ SEC-01 |
| A5 | Oversized amount (เกิน precision `NUMERIC(18,2)`, เช่น 10^17) → DB error 500 / overflow behavior ไม่ define | T/D | AC-04 ครอบเฉพาะ scale (ทศนิยม) ไม่ครอบ precision (จำนวนหลัก) | ⚠️ SEC-01 |
| A6 | Idempotency-key collision ข้าม order: ใช้ key K (order A, 300.00) ยิงใส่ order B ด้วย amount 300.00 → pseudo-code ขั้น 1 [03-sara-1a.md:91] lookup ด้วย key อย่างเดียว → **replay ผลของ order A ราวกับสำเร็จบน order B** (client เข้าใจผิดว่า B ถูก refund; hijack ผล transaction ข้ามใบ) | T/I | AC-06 เทียบเฉพาะ amount ไม่เทียบ order_id · ADR-003 key เป็น global scope [03-sara-1a.md:113] | 🔴 SEC-02 (gap จริง) |
| A7 | Mass-assignment: แนบ field เกิน (`refunded_amount`, `status`, `order_id`) ใน JSON body หวัง ORM/schema เขียนทับ | T/E | schema มีแค่ `amount` [03-sara-1a.md:81] แต่ไม่มี AC บังคับ reject extra field — Pydantic default = ignore เงียบ | ⚠️ SEC-03 |
| A8 | Info disclosure ผ่าน GET (ดู refund/ledger ของ order ใคร ก็ได้ถ้ารู้ UUID) | I | ยอมรับ — consequence ตรงของ no-auth pin; UUID v4 = capability token อย่างอ่อน. บันทึกใน README (§1) พอ | ✅ accepted risk |

## 3. Security AC ใหม่ (inject เข้า AC ของ Bella — testable)

- **SEC-01 (bound validation ที่ API layer)**
  Given `POST /orders/{id}/refunds` (และ `POST /orders`)
  When amount ≤ 0 หรือ integer part เกิน 16 หลัก (เกิน `NUMERIC(18,2)`)
  Then reject **422 ที่ Pydantic ก่อนเปิด transaction** (ไม่พึ่ง DB CHECK แล้วตอบ 500) — ทดสอบ: `"-300.00"`, `"0.00"`, `"99999999999999999.00"` ต้องได้ 422 ทุกตัว

- **SEC-02 (idempotency-key ผูกกับ order — ปิด cross-order replay)**
  Given key K ถูกใช้สำเร็จแล้วกับ order A
  When request ใหม่ใช้ key K กับ order B (amount ใด ๆ รวมทั้ง amount ตรงกัน)
  Then reject **409 conflict** — ห้าม return ผลของ order A เป็น replay. Implement: ขั้น replay-check ใน service เทียบทั้ง `order_id` และ `amount` (ADR-003 ต้อง update note นี้)

- **SEC-03 (strict schema — กัน mass-assignment)**
  Given request body มี field นอก schema (เช่น `{"amount":"300.00","refunded_amount":"0.00"}`)
  Then reject 422 (Pydantic `model_config = ConfigDict(extra="forbid")` ทุก request schema) — ห้าม ignore เงียบ; ทดสอบด้วย extra field อย่างน้อย 1 case

## 4. Sign-off / handoff

- Sentinel: ✅ 2026-09-08 — STRIDE (T/I/D แกนที่เหลือหลังตัด no-auth) ครบ 8 abuse case, 3 SEC AC
- ขอ Sara ack SEC-02 (กระทบ pseudo-code §4 ขั้น 1-2 + ADR-003) ก่อน Dave implement

Sentinel ▸ Bella : merge SEC-01..03 เข้า AC set (A5)
Sentinel ▸ Dave  : security AC ready + README no-auth disclaimer (A5)
Sentinel ▸ Sara  : ADR-003 revision — replay check ต้องเทียบ order_id (A5)
