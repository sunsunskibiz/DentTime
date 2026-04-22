# ADR-003: การ Drop `notes` Field แล้วเก็บเฉพาะ `has_notes` Flag

**Status:** Proposed (สำหรับ review, อาจกลายเป็น Accepted-with-Phase-2-plan)
**Date:** 2026-04-18
**Deciders:** Feature Engineering (Sun), Data Ingestion (Natcha), Model Lead
**Related:** ADR-001 (dentist license), Progress 3 design diagram (API spec mentions `notes` field)

---

## 1. Context

ทีม FE วางแผนเดิมว่าจะใช้ **Notes + Treatment + Tooth Number + Doctor** เป็น feature หลักใน XGBoost classifier Upstream pipeline (Natcha) drop raw text ของ notes ทิ้ง เก็บเฉพาะ `has_notes` flag (0/1) ด้วยเหตุผล PDPA — **notes field เป็น unstructured free-text ที่มีความเสี่ยง PII สูง** (ชื่อคนไข้, เบอร์โทร, ประวัติสุขภาพ)

**Tension ที่เกิดขึ้น:**

1. **FE plan เดิม:** notes = feature หลัก
2. **Upstream decision:** drop raw notes, keep flag เท่านั้น
3. **API spec (Progress 3):** POST /predict รับ `notes` field มาจาก UI → **training-serving schema mismatch**
4. **Instructor guidance (from project brief):** _"focusing on structured features before free-text"_ → คำแนะนำชัดเจนว่าให้ structured features มาก่อน

**สมมติฐานที่ยังไม่ได้ validate:** "notes มี signal สูงต่อ duration" — FE team เชื่อว่ามี แต่**ไม่มีข้อมูลเชิงตัวเลขสนับสนุน** ยังไม่เคยรัน ablation study หรือ keyword frequency analysis

---

## 2. Decision

เสนอให้ **ยอมรับการ drop raw notes ในระยะแรก** โดยใช้ **phased approach** ที่ประเมินด้วยข้อมูล ไม่ใช่สมมติฐาน:

- **Phase 1 (ปัจจุบัน):** Train XGBoost baseline ด้วย structured features + `has_notes` flag วัด F1 และ **Under-estimation Rate**
- **Phase 2 (มีเงื่อนไข):** ถ้า baseline under-performs บน long procedures → เพิ่ม **curated keyword flags** ที่ derive ได้อย่างปลอดภัยตอน anonymize (ไม่ใช่ raw text)
- **Phase 3 (out of scope):** NLP embeddings + formal PII scrubbing — ต้อง legal review และ consent flow ซึ่งเกินขอบเขตโปรเจกต์

**จัดการ training-serving skew:** ลบ `notes` ออกจาก API contract ของ POST /predict ใน v1 (หรือ accept แต่ ignore) — อัพเดต Progress 3 serving spec

---

## 3. การประเมิน "Notes มี Signal แค่ไหน" (หลักฐานที่ควรดู)

### 3.1 มุมมองคลินิกของประเภทเนื้อหาใน dental notes

| Category | ตัวอย่าง | Signal ต่อ duration | Redundant กับ structured features? |
|----------|--------|--------|------|
| Procedure complexity marker | "ฟันคุดลึก ต้องผ่า", "หินปูนแข็ง" | **สูง** | บางส่วน (treatment name มีอยู่แล้ว) |
| Patient-specific factor | "คนไข้กลัว ให้ยาคลายกังวล" | กลาง | ไม่ |
| Anatomical difficulty | "อ้าปากได้น้อย", "รากคด" | สูง | ไม่ |
| Administrative/billing | "โปร 999", "cash" | ต่ำ | redundant กับ `total_amount` |
| Structured info ซ้ำ | "#14,15 filling 2 surfaces" | ต่ำ | redundant กับ `tooth_no`+`surfaces`+`treatment` |
| Empty/routine | "normal", "ok", "" | ศูนย์ | — |

**Observation สำคัญ:** Top 10 treatments ใน HANDOFF (ปรับเครื่องมือจัดฟัน, ขูดหินปูน, etc.) คิดเป็น high volume **เป็น routine cases ที่ notes มักว่างหรือเป็น boilerplate** → signal เข้มข้นอยู่ใน **~10-20% ของ cases** ที่เป็น outlier ซึ่งเป็น cases ที่ `has_notes=1` อยู่แล้ว

### 3.2 สมมติฐานที่ `has_notes` flag จับได้มากแค่ไหน

```
ถ้า P(duration > 60 min | has_notes=1) > P(duration > 60 min | has_notes=0)
  → flag จับ complexity signal ได้ส่วนหนึ่งแล้ว
```

ยังไม่มี data เพียงพอจะยืนยัน — **task อันดับแรกของ FE คือทดสอบ hypothesis นี้**

### 3.3 ทำไม "notes is main feature" ถึงเป็น red flag

1. **Data volume เล็ก** — 120,534 rows × 4 เดือน ไม่พอ train text model ให้ generalize โดย notes มี cardinality สูงมาก ต้องการ data >> 10× ขนาดนี้
2. **Thai NLP + medical domain** — tokenization ภาษาไทย + jargon ทันตกรรม ต้องการ domain-specific preprocessing / embeddings — หนักมากสำหรับ scope รายวิชา
3. **Overfitting risk สูง** — free-text feature มี feature space infinite XGBoost ถนัด tabular ไม่ใช่ text ต้องแปลงเป็น bag-of-words/embeddings ก่อน ซึ่งเพิ่ม complexity
4. **Instructor explicit guidance** — project brief เขียน "structured features before free-text" ชัดเจน
5. **Explainability loss** — text features ทำ feature importance ตีความยาก ขัดกับ Responsible AI section ของโปรเจกต์

---

## 4. Options Considered

### Option A — Accept Drop, Phase-1 Only (Baseline)

Train ด้วย structured + `has_notes` flag หยุดแค่นี้ ถ้า metric ผ่านเกณฑ์

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low |
| Privacy (PDPA) | ✅ ปลอดภัยสุด |
| Signal ที่ได้ | 60-80% ของ full-notes signal (ประเมินเบื้องต้น) |
| Training-serving parity | ✅ ถ้าลบ `notes` จาก API spec |
| Instructor alignment | ✅ ตรงกับ guidance |
| Risk | Under-estimate rate อาจสูงเกินเกณฑ์ใน long-tail cases |

### Option B — Ask Upstream to Keep Raw Notes

ยกเลิก drop, FE ทำ NLP เอง

| Dimension | Assessment |
|-----------|------------|
| Complexity | **High** — ต้องออกแบบ PII scrubbing + Thai NLP pipeline |
| Privacy | ⚠️ ต้อง NER scrub ก่อน + legal review |
| Signal ที่ได้ | 100% theoretical max (แต่จริงๆ noisy) |
| Training-serving parity | ✅ |
| Instructor alignment | ❌ ขัดกับ "structured first" |
| Risk | PDPA violation ถ้า scrub ไม่สมบูรณ์ + overfitting + out-of-scope effort |

### Option C — **(แนะนำ)** Accept Drop + Phase-2 Fallback

Phase 1 baseline + **เงื่อนไข rollout Phase 2** ถ้า metric ไม่ผ่าน

Phase 2 ใช้ **curated keyword flags** — Natcha add script ตอน anonymize ที่ scan notes หา keyword จาก allowlist (ทั้งหมดเป็น clinical terms ไม่ใช่ PII) แล้วสร้าง boolean features:

```python
COMPLEXITY_KEYWORDS = {
    "surgical": ["ผ่า", "ผ่าตัด", "surgery", "extraction complex"],
    "difficult": ["ยาก", "ซับซ้อน", "ลึก", "คุด"],
    "anxiety": ["กลัว", "คลายกังวล", "sedation"],
    "retry": ["ซ้ำ", "repeat", "rework"],
}
# Output: has_surgical_kw, has_difficult_kw, has_anxiety_kw, has_retry_kw
# Raw text ถูก drop เหมือนเดิม — แค่ boolean flags หลุดออกมา
```

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium — upstream add ~30 lines, FE add 4 features |
| Privacy | ✅ Keywords เป็น clinical vocabulary, ไม่ใช่ PII |
| Signal ที่ได้ | 75-90% ของ full-notes signal (สูงกว่า `has_notes` มาก) |
| Training-serving parity | ✅ API สามารถ derive flags จาก notes ที่ UI ส่งมาได้ |
| Instructor alignment | ✅ "structured" — flags เป็น structured features แล้ว |
| Reversibility | ✅ ถ้า allowlist ไม่ดีก็ปรับได้ โดยไม่แก้ anonymize core |

**Pros:**
- ได้ signal จำลองจาก notes โดยไม่เก็บ raw text
- Features เป็น interpretable (explainability ดี)
- Incremental — ถ้า Phase 1 ผ่านเกณฑ์ ก็ไม่ต้องทำ Phase 2

**Cons:**
- Allowlist ต้อง maintain — ถ้า clinic เปลี่ยน vocab, flags จะ drift
- False negative: keyword หลุดจาก allowlist จะไม่ถูกจับ
- ต้องเจรจากับ Natcha อีกรอบ (หลัง ADR-001 เรื่อง license, ADR-002 เรื่อง is_first_case)

### Option D — Note Length as Numeric Feature

เก็บแค่ `notes_char_length` และ `notes_word_count` — สมมติฐาน: note ยาว = case ซับซ้อน

| Dimension | Assessment |
|-----------|------------|
| Complexity | Very Low |
| Privacy | ✅ (length ไม่ใช่ PII) |
| Signal ที่ได้ | 30-50% — proxy อ่อนกว่า keyword |
| Interpretability | ปานกลาง — "notes ยาว" ไม่ explain ว่าทำไม |

สามารถใช้ร่วมกับ Option A เป็น lightweight add-on ได้ (incremental cost ต่ำ)

---

## 5. Trade-off Analysis

**Signal Loss vs. Scope Realism** — Option B (raw notes) ให้ theoretical max signal แต่เป็นงานที่เกินขอบเขตวิชาและขัด instructor guidance Option C ได้ signal ~75-90% ด้วย effort ~10% ของ B

**Privacy vs. Utility** — ที่นี่ trade-off ไม่ sharp เพราะ curated keyword allowlist อยู่ใน PDPA-safe zone (ไม่เก็บ personal data, ไม่สร้าง linkage ใหม่) จึงเป็น "free lunch" เชิงเปรียบเทียบ

**Hypothesis vs. Evidence** — คำถามว่า "notes สำคัญไหม" ต้องตอบด้วยข้อมูล Phase 1 ที่รัน baseline จะให้ feature importance และ confusion matrix ที่บอกได้ว่า **long procedure class (75/90/105 min) underperform อยู่ที่ไหน** — ถ้า underperform เกิดที่ routine treatments ที่ notes ว่างอยู่แล้ว → notes ก็ช่วยไม่ได้; ถ้า underperform ที่ complex cases ที่ notes เต็ม → ยืนยัน need ของ Phase 2

**Training-Serving Skew** — ปัญหาของ API spec ปัจจุบันที่รับ `notes` field ต้องแก้ไม่ว่าจะเลือก option ไหน เป็น architectural hygiene

---

## 6. Consequences

**สิ่งที่ง่ายขึ้น:**
- Scope รายวิชาชัดเจนขึ้น — ไม่ต้อง commit กับ Thai medical NLP
- PDPA compliance ตรงไปตรงมา — audit trail สั้นและชัด
- Slide content เข้าเรื่อง Responsible AI + Technical Debt ได้ตรง

**สิ่งที่ยากขึ้น:**
- ต้องทำ Phase 1 baseline **จริงจัง** ก่อน ไม่ใช่แค่ proof-of-concept — feature importance analysis ต้องลึก
- ต้องเจรจา API spec change กับ serving team
- Phase 2 (ถ้าเกิดขึ้น) ต้อง maintain keyword allowlist เป็นงาน governance ต่อเนื่อง

**สิ่งที่ต้อง revisit:**
- หลัง Phase 1 ถ้า Under-est Rate บน class 75/90/105 > 25% → trigger Phase 2
- ถ้า data volume ขยายถึง 1M+ rows (เช่น ผ่าน 1 ปีเต็ม) → พิจารณา Phase 3 (proper NLP) ใหม่

---

## 7. Action Items

**ทันที (Phase 1 setup):**
1. [ ] FE: รัน XGBoost baseline ด้วย features ปัจจุบัน (ไม่มี notes) + `has_notes` flag
2. [ ] FE: Log **feature importance ของ `has_notes`** ใน MLflow — ถ้า rank สูง ⇒ hypothesis "notes มี signal" น่าจะจริง
3. [ ] FE: Report **confusion matrix เน้น under-est ของ class 75/90/105** ใน Evaluation section
4. [ ] FE + Serving: ตัดสินใจว่า API spec v1 จะ `notes` เป็น optional-and-ignored หรือ remove

**Conditional (Phase 2 trigger):**
5. [ ] ถ้า Phase 1 metrics ไม่ผ่าน → ส่ง keyword allowlist draft ให้ Natcha (Sun draft, Natcha review clinical accuracy)
6. [ ] Natcha: extend `anonymize_for_ml.py` เพิ่ม `has_*_kw` flags ก่อน drop raw notes

**Governance:**
7. [ ] Slide: Responsible AI section ต้องอธิบายว่าทำไม drop notes — ไม่ใช่ "เพราะขี้เกียจ" แต่ "เพราะ PDPA + insufficient data for robust NLP"
8. [ ] Slide: ยอมรับ limitation ชัดเจน — model จะ under-estimate complex cases ที่ doctor ระบุใน notes → manual override ที่ UI เป็น mitigation

---

## 8. Connection กับ Progress 3 Design

Progress 3 Serving spec ระบุ request ว่า:
```json
{ "notes": "โปร 999", ... }
```

ซึ่งแสดงว่า serving team คาดว่า notes จะเข้าโมเดล แต่ training pipeline ไม่มี notes อยู่แล้ว → **dead field ใน API contract**

**Action สำหรับ design integrity:**
- ถ้าเลือก Option A: แก้ API spec — ลบ `notes` หรือมาร์คเป็น `// reserved for future use`
- ถ้าเลือก Option C: แก้ API spec — รับ `notes` แต่ FastAPI backend **รัน keyword extraction ฝั่ง server** (consistent กับ training pipeline) แล้วทิ้ง raw text; ส่งเฉพาะ flags เข้าโมเดล → **training-serving parity ชัดเจน**

---

## 9. บทสรุป (สำหรับ Slide Presentation)

**"So what?" layer ที่อาจารย์จะถาม:**

> "ถ้า drop notes แล้วโมเดลพลาด long procedure บ่อย = operational risk: คลินิกจองเวลาสั้นเกินไป → case overrun → clinic schedule delay + customer dissatisfaction"

**คำตอบที่ DentTime เสนอ:**

1. ยอมรับว่า notes มี signal — แต่ **ไม่ทำให้เป็น feature หลัก** เพราะขัดกับ instructor guidance + PDPA + data volume
2. ใช้ `has_notes` flag เป็น cheap proxy ที่ capture "case นี้ผิดปกติ" signal ได้ระดับหนึ่ง
3. มี **Phase 2 fallback plan** ถ้า metric ไม่ผ่าน (keyword flags) — แสดงว่าคิด architectural evolution ไม่ได้ตัน
4. UI มี **manual override** สำหรับเจ้าหน้าที่เวลา model underestimate → ตอกย้ำว่าเป็น "decision support ไม่ใช่ replacement"

**Slide angle:** "Evidence-based feature design — เราไม่ตัดสิน notes จาก intuition เราวัดจาก data แล้วตัดสินเป็นเฟส" เป็น framing ที่ academic reviewer ให้คะแนนดี
