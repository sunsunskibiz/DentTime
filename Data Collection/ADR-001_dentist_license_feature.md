# ADR-001: การใช้ `dentist_license` (dentist_pseudo_id) ในฐานะ ML Feature

**Status:** Proposed (รอ sign-off จากทีม Data Ingestion + Feature Engineering)
**Date:** 2026-04-18
**Deciders:** Feature Engineering (Sun), Data Ingestion/Upstream, Model Training Lead
**Supersedes:** การ drop 50% ของ raw data ใน `anonymize_for_ml.py` (post-filter step)

---

## 1. Context

DentTime เป็น supervised multi-class classification ทำนายช่วงเวลาการรักษา 7 classes (15/30/45/60/75/90/105 นาที) ให้คลินิกทันตกรรม โดยโมเดลหลักคือ XGBoost และ business metric ที่สำคัญที่สุดคือ **Under-estimation Rate** สำหรับหัตถการที่ใช้เวลานาน

**สถานะข้อมูลปัจจุบัน (จาก Progress 3 Data Ingestion Report):**

| Stage                                       | Row Count | % of previous |
|---------------------------------------------|----------:|--------------:|
| Raw CSV exports (3 files merged)            |   424,053 |          100% |
| After deduplication on appointment_id       |   261,407 |         61.6% |
| **After dropping missing dentist license**  | **125,740** |       **48.1%** |
| After k-anonymity filter (k=5)              |   120,534 |         95.9% |

**ข้อสังเกต 3 ข้อ:**

1. Row loss ครั้งใหญ่ที่สุดอยู่ที่ **"dropping missing dentist license"** — หายไป 135,667 rows (51.9% ของ post-dedup data)
2. Root cause คือ data quality ของ source system (DentCloud): พนักงานไม่กรอกเลขใบประกอบวิชาชีพ
3. Upstream แก้ปัญหาด้วยวิธี **hard-drop** ซึ่งเป็นการตัดสินใจที่ **blocks downstream options** โดยไม่ให้ FE team ได้เห็น raw distribution ก่อน

**สิ่งที่ทำให้เกิดความสับสน (Requirements Debt):** Overview design diagram ของ Progress 3 ระบุไว้ว่า FE จะใช้ `Doctor Profile Stats` พร้อม **"Cold-start → Global Median fallback"** ซึ่งโดย definition แล้วต้องรองรับกรณี dentist ไม่ทราบตัว — **แต่ data ที่เข้า FE pipeline ไม่มีเคสนี้อยู่เลย** เพราะ upstream drop ออกไปก่อน

---

## 2. Decision

เสนอให้ **รักษา `dentist_pseudo_id` ไว้เป็น feature แบบ optional (nullable)** และ**ยกเลิกการ hard-drop ที่ upstream** โดยปรับเป็น:

- **ที่ Upstream:** ไม่ drop rows — ส่ง `dentist_pseudo_id = NULL` เข้า downstream พร้อม flag `has_dentist_id` (boolean)
- **ที่ Feature Engineering:** ใช้ strategy แบบ **Two-Tier Lookup**
  - Tier 1: ถ้ามี `dentist_pseudo_id` → join กับ Doctor Profile Stats table (dentist_avg_duration, case_count, etc.)
  - Tier 2: ถ้าไม่มี → ใช้ **clinic-level aggregate** เป็น fallback ก่อน global median
  - Tier 3: ถ้าไม่มีทั้งคู่ → Global Median fallback (per treatment class)

> **คำตอบตรงๆ ต่อคำถาม:** "license ยังน่าใช้" — **ไม่ใช่ตัว license เอง** ซึ่งเป็นแค่ identifier แต่ **dentist-level features ที่ derive จาก license** (เช่น avg duration, case volume per treatment type) เป็น signal ที่น่าจะมีประโยชน์สูงต่อการทำนาย โดยเฉพาะสำหรับ Under-estimation Rate เพราะหมอแต่ละคนมีความเร็วต่างกันจริงในทางปฏิบัติ การทิ้ง 50% ของ data เพียงเพื่อให้ทุกแถวมี identifier ครบเป็น **trade-off ที่ไม่คุ้ม** ในเชิง ML

---

## 3. Options Considered

### Option A — คงสถานะเดิม (Hard-drop at Upstream)

Upstream drop rows ที่ license เป็น null ทั้งหมด → FE ใช้ dentist เป็น required feature

| Dimension          | Assessment                                                           |
|--------------------|----------------------------------------------------------------------|
| Data volume        | ❌ เหลือ 120,534 rows (28% ของ raw, 48% ของ post-dedup)                |
| Feature signal     | ✅ ทุกแถวมี dentist features ครบ                                       |
| Production parity  | ❌ Training data ไม่มี null case — แต่ inference time ต้องมี fallback (ดู API spec: `doctor_id` is required แต่โมเดลอาจเจอ unknown doctor) |
| Bias risk          | ⚠️ Drop ไม่ random — คลินิกเล็ก/หมอใหม่ใส่ license น้อยกว่า → model bias ต่อคลินิกใหญ่ |
| Sign-off ความยาก   | ต่ำ (ทำไปแล้ว)                                                        |

**Pros:**
- ง่ายที่สุด ไม่ต้องแก้ code
- Doctor features ไม่มี missingness

**Cons:**
- **Training data halved** — ผลโดยตรงคือ high variance, โดยเฉพาะ minority classes (75/90/105 นาที)
- **Selection bias** — ถ้า missing license สัมพันธ์กับประเภทคลินิก (สมมติฐาน: คลินิกเล็กหรือคลินิก chain ที่มี SOP หลวม) โมเดลจะ underrepresent cohort นั้น
- **Requirements debt:** ขัดกับ design intent ที่เขียน cold-start fallback ไว้
- **Production deploy จะ fail silent:** Progress 3 API spec รับ `doctor_id` เป็น required field, แต่ในทางปฏิบัติ UI อาจส่ง null ได้เวลาเจ้าหน้าที่ยังไม่ได้ assign หมอ → model ไม่มีทางรู้จะ handle ยังไง เพราะไม่เคยเห็นใน training

### Option B — ทิ้ง `dentist_pseudo_id` ทั้งก้อน

Drop feature dentist ทั้งหมด ใช้เฉพาะ clinic-level + treatment + time features

| Dimension          | Assessment                                                           |
|--------------------|----------------------------------------------------------------------|
| Data volume        | ✅ กลับมาที่ 261,407 rows (pre-k-anonymity)                            |
| Feature signal     | ❌ สูญเสีย dentist-specific speed signal                               |
| Production parity  | ✅ ไม่ต้อง handle missing dentist แล้ว                                  |
| Under-est Rate     | ❌ น่าจะแย่ลง — dentist experience เป็น predictor ของ "case overrun"    |
| Sign-off ความยาก   | ต่ำ                                                                  |

**Pros:**
- Simple feature space, ง่ายต่อ monitoring
- ไม่มี cold-start problem ตอน production
- Training data เพิ่มขึ้น 2x

**Cons:**
- **ปิดประตู signal สำคัญ** — Under-estimation rate ของ long procedures สัมพันธ์กับประสบการณ์หมอ การทิ้ง dentist features ทั้งหมดน่าจะทำ business metric หลักแย่ลง
- **ตัดตัวเลือกก่อนทดลอง** — เรายังไม่มี feature importance จาก XGBoost ที่แสดงว่า dentist ไม่สำคัญ การทิ้งโดยไม่ทดลองคือ uninformed decision
- **Cold-start logic เสียของ** — Design ทั้ง diagram ต้องรื้อ

### Option C — **(แนะนำ)** Optional Feature with Two-Tier Fallback

Upstream ส่ง dentist_pseudo_id as nullable + FE implement hierarchical fallback

| Dimension          | Assessment                                                           |
|--------------------|----------------------------------------------------------------------|
| Data volume        | ✅ 261,407 → ~255,000 rows (หลัง k-anonymity filter ที่ปรับใหม่)          |
| Feature signal     | ✅ ใช้ dentist signal ได้เมื่อมี, fallback เมื่อไม่มี                       |
| Production parity  | ✅ Training distribution สะท้อน production (ซึ่งจะมีบาง case ไม่มี doctor) |
| Bias risk          | ✅ ลดลงมาก — clinic เล็กกลับเข้าสู่ training set                         |
| Complexity         | ⚠️ ต้อง maintain Doctor Profile + Clinic Profile lookup tables           |
| Sign-off ความยาก   | กลาง — ต้องคุยกับ upstream                                             |

**Pros:**
- Training data ครอบคลุม production distribution จริง (รวมเคสไม่มีหมอ)
- Dentist signal ยังใช้ได้ใน majority of cases
- **Matches stated design intent** (cold-start fallback ที่ Progress 3 ระบุไว้)
- สร้าง feature importance study ได้เพื่อ validate ว่า dentist มี signal จริง

**Cons:**
- ต้องออกแบบ **imputation contract ให้ชัดเจน**: `has_dentist_id` flag + fallback feature values ต้อง deterministic
- ถ้า doctor profile lookup ใช้ target encoding ต้อง fit เฉพาะบน train split เท่านั้น (leakage risk)
- ต้อง coordinate schema change กับ upstream (task เพิ่ม ~0.5 day)

---

## 4. Trade-off Analysis

**แกน Data Volume vs. Feature Completeness** — Option A บังคับคุณเลือกอย่างเดียว (completeness) โดยสละอีกอย่าง (volume) Option C ยอมรับว่า real-world data ไม่สมบูรณ์และสร้าง graceful degradation แทน

**แกน Training-Serving Skew** — ที่สำคัญกว่าเรื่อง data volume คือ Option A สร้าง **training-serving skew** โดยตรง: training set ไม่มี null case แต่ production จะมี ถ้าโมเดล deploy ด้วย Option A และเจอ null `doctor_id` เข้ามา ผลลัพธ์จะ undefined (อาจ predict ด้วย encoding ของ unknown category ซึ่งไม่มี statistical meaning)

**แกน Business Risk (Under-est Rate)** — ทีม instructor ย้ำเรื่อง "structured features before free-text" — dentist_id เป็น structured feature ที่มี signal สูงมาก การทิ้งโดยไม่ทดลองคือ requirements debt ชัดๆ Option C ให้คุณทดลองและ rollback ได้ถ้าข้อมูลบอกว่า dentist ไม่มีผล

**แกน PDPA / k-anonymity** — การเก็บ dentist_pseudo_id เพิ่มเติมไม่ทำให้ k-anonymity แย่ลงเพราะ upstream ใช้ ephemeral HMAC key อยู่แล้ว (irreversible) และ pseudo_id ไม่ได้เป็น quasi-identifier ใน QI set ปัจจุบัน (`clinic + year-month + day-of-week + hour bucket`)

---

## 5. Consequences

**สิ่งที่ง่ายขึ้น:**
- Training distribution สะท้อน production distribution → lower risk of silent failure
- Feature importance study จาก XGBoost จะให้ข้อมูล data-driven ว่า dentist มี signal แค่ไหน (ผลนี้จะเอาไปใช้เป็น slide content ได้ตรงๆ)
- Cold-start fallback ที่ออกแบบไว้ใน Progress 3 จะทำงานได้จริง (ไม่เป็น dead code)

**สิ่งที่ยากขึ้น:**
- ต้องเพิ่ม column `has_dentist_id` ใน output schema ของ upstream (จาก 18 → 19 columns)
- Feature Engineering ต้อง maintain **2 lookup tables**: `doctor_profile_stats` และ `clinic_profile_stats` (เผื่อ fallback)
- Target encoding ต้อง guard ด้วย time-based split ให้เข้มงวด (อย่าให้ test set leak เข้า encoding)

**สิ่งที่ต้อง revisit:**
- หลังรัน XGBoost feature importance แล้ว → ถ้า dentist features rank ต่ำจริง พิจารณากลับไป Option B ได้ (แต่ต้องมีหลักฐาน ไม่ใช่สมมติฐาน)
- Monitoring: PSI ของ `has_dentist_id` ratio ต้องเข้า drift dashboard (ถ้า DentCloud แก้ data quality แล้วสัดส่วน null ลดลงฉับพลัน = distribution shift)

---

## 6. Action Items

1. [ ] **Upstream:** แก้ `anonymize_for_ml.py` post-filter ให้ไม่ drop rows ที่ `dentist_pseudo_id IS NULL` — เพิ่ม column `has_dentist_id` แทน *(ต้องคุยกับทีม Data Ingestion ก่อน)*
2. [ ] **Upstream:** Rerun k-anonymity check โดยพิจารณาว่า null dentist ใน QI group เล็กจะกลับมาทำให้ row ถูก drop ด้วย k=5 หรือไม่ (คาด: < 5% row loss เพิ่ม)
3. [ ] **FE (ฉัน):** สร้าง `build_doctor_profile_stats(train_df)` ใช้ **train split เท่านั้น** (อิง `appt_year_month` ≤ month 10)
4. [ ] **FE:** สร้าง `build_clinic_profile_stats(train_df)` เป็น Tier-2 fallback
5. [ ] **FE:** Feature imputation contract ชัดเจน — ถ้า cold-start, ใช้ clinic stats; ถ้า clinic ก็ unknown, ใช้ global median per treatment class
6. [ ] **FE:** เพิ่ม `has_dentist_id` เป็น feature ให้ XGBoost (signal ว่า "record นี้หมอไม่ระบุ" เอง ก็อาจเป็น predictor)
7. [ ] **Model Training:** รัน ablation 3 แบบ (with dentist / without dentist / with fallback) — report F1, Under-est Rate
8. [ ] **Slides (Evaluation section):** ทำ chart เปรียบเทียบ 3 scenarios พร้อมคำอธิบาย "So what" สำหรับคลินิก
9. [ ] **Governance slide:** Flag ว่า data quality ของ dentist license คือ **upstream technical debt** — model mitigates แต่ไม่ fix root cause (DentCloud ต้องบังคับ required field ที่ source)

---

## 7. Slide Recommendations (สำหรับ Presentation)

สำหรับสไลด์ FE ของคุณ ผมแนะนำโครงต่อไปนี้ (มันก็ตอบคำถามของ instructor เรื่อง "structured features before free-text" + "reproducibility" ไปในตัว):

**Slide: "Feature Design — จาก Raw Record สู่ Structured Features"**
- แสดง feature taxonomy 4 กลุ่ม: Treatment (structured), Temporal (derived), Clinic (entity), Dentist (entity + fallback)
- ระบุ "Dentist ID treated as **optional** feature" พร้อมเหตุผลเรื่อง data quality + training-serving parity

**Slide: "Handling Missing Dentist — A Case Study in Requirements Debt"**
- Table เปรียบเทียบ Option A/B/C (ย่อ)
- Metric: Under-est Rate ของ 3 configurations (หลัง ablation)
- "So what": ถ้าเลือก Option A เสียข้อมูล 50% คลินิกเล็กหายจาก training set — bias ต่อผลการตัดสินใจในคลินิกเล็ก

**Slide: "Technical Debt Register"**
- Upstream: DentCloud ไม่บังคับ required field → source system fix (ไม่ใช่ ML fix)
- Model: Pseudonymized IDs rotate ทุกรอบ anonymize → cross-version join เป็นไปไม่ได้ (already documented ใน Progress 3 limitations)

---

## 8. Note on Upstream Script

`anonymize_for_ml.py` ตัวอย่างทำหน้าที่ pseudonymization ด้วย HMAC-SHA256 + ephemeral key — **เป็น privacy engineering ที่ถูกต้อง** (irreversible by design) การตัดสินใจ drop rows อยู่ใน **post-filter step** (line 185) ซึ่งเป็น **business logic choice** ไม่ใช่ส่วนของ privacy protection สามารถแก้ได้โดยไม่กระทบ PDPA compliance เลย
