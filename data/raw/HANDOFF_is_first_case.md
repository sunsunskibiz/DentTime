# Handoff Spec: `is_first_case` + `appointment_rank_in_day`

**จาก:** Sun (Feature Engineering) **ถึง:** Natcha (Data Ingestion)
**Version:** v2 (updated 2026-04-18 หลังรับ ADR-001)
**Effort:** ~30 นาที (15 นาที code + 15 นาที test) · **Difficulty:** ★★☆☆☆

> ⛔ **Blocker:** ADR-001 (ยกเลิก hard-drop ของ rows ที่ `dentist_license` เป็น null) ต้อง merge และ deploy ก่อนจึงจะ implement spec นี้ได้ — ดูรายละเอียดใน [ADR-001_dentist_license_feature.md](ADR-001_dentist_license_feature.md)
>
> ⚠️ **Row count note:** ADR-001 ยังมีสถานะ "Proposed" — ตัวเลข ~200K rows ใน spec นี้เป็นค่าประมาณจาก FE ส่วน ADR-001 ประมาณไว้ที่ ~255K rows (Option C table) ตัวเลขจริงจะได้หลัง Upstream rerun k-anonymity filter

## Overview

เพิ่ม 2 columns ใน anonymized output เพื่อ preserve "appointment order within day" signal ที่หายไปจาก 4-hour bucket generalization Progress 3 design diagram ระบุ `is_first_case` เป็น feature input ของโมเดล แต่ schema ปัจจุบัน compute ไม่ได้ → **requirements debt**

## Prerequisite

Spec นี้ assume ว่า ADR-001 (หยุด drop missing license) ถูก implement จริงใน pipeline แล้ว — row count หลัง ADR-001 + k-anonymity filter ≈ **200K rows**

## Implementation

แทรกใน `anonymize_for_ml.py` → `anonymize(df)` **ก่อน** return (หลัง time features block, line ~103)

```python
# Rank within (dentist, date) — ใช้ raw start time ก่อน generalize
dentist_key = df["license_no"].apply(normalize_license)
date_key = start.dt.date

rank_df = pd.DataFrame({
    "_dentist": dentist_key,
    "_date": date_key,
    "_start": start,
    "_aid": df["appointment_id"],   # tie-breaker (deterministic)
}).reset_index()

# dropna=False → null-license rows ยังอยู่ใน output (จะ mask ทีหลัง)
#   ถ้าไม่ใส่ dropna=False, pandas default จะ skip null keys → length mismatch ตอน assign
rank_df = rank_df.sort_values(["_dentist", "_date", "_start", "_aid"])
rank_df["_rank"] = (
    rank_df.groupby(["_dentist", "_date"], dropna=False).cumcount() + 1
)
rank_df = rank_df.sort_values("index")   # restore original row order
# ⚠️ assumes .reset_index() above added an "index" column — if input df already has
#    a column named "index" from a prior operation, rename it first to avoid silent sort on wrong column

out["appointment_rank_in_day"] = rank_df["_rank"].values
out["is_first_case"] = (out["appointment_rank_in_day"] == 1).astype(int)

# ⚠ Null-license handling: rank ของ "unknown dentist partition" ไม่มี semantic meaning
# (จะนับรวม appointments ของทุกหมอที่ license หายในวันนั้น → ไม่ใช่ "case แรกของหมอ")
# → overwrite เป็น NaN/0 เพื่อไม่ให้ feature leak false signal เข้า training
null_license_mask = df["license_no"].isna().values
out.loc[null_license_mask, "appointment_rank_in_day"] = pd.NA
out.loc[null_license_mask, "is_first_case"] = 0
```

**2 จุดที่เปลี่ยนจาก v1**:
1. `groupby(..., dropna=False)` — pandas default คือ `dropna=True` ซึ่งจะ drop null keys ออกจากผลลัพธ์ → ได้ length มาไม่เท่า df เดิม → assign กลับพัง
2. Null-license post-mask — เปลี่ยนจาก "acceptable" เป็น explicit NaN/0

## Output Schema (delta)

| Column | Type | Range | Nulls |
|--------|------|-------|-------|
| `appointment_rank_in_day` | Int64 (nullable) | 1…~30 | null เมื่อ `license_no` missing |
| `is_first_case` | int | {0, 1} | 0 (null license → 0) |

Schema version bump: **v1 → v2** (18 → 20 columns) — ระบุใน `manifest.json`

## Edge Cases

| Scenario | Behavior | Risk |
|----------|----------|------|
| **Same-minute ties** (2 นัด 09:00) | Tie-break ด้วย `appointment_id` alphabetical → stable, reproducible | 🟢 Minor |
| **Null `license_no`** (post-ADR-001) | `appointment_rank_in_day = NaN`, `is_first_case = 0` — downstream **ต้อง handle NaN** (impute 0 หรือ median; **ห้าม drop row** เพราะจะย้อนกลับ ADR-001) | 🔴 Critical |
| **หมอคนเดียว 2 คลินิกในวันเดียว** | Rank แบบ global per-dentist (ถูกตาม semantic "case แรกของหมอ" ไม่ใช่ "case แรกของคลินิก") | 🟡 Moderate — ตรวจสอบกับ Product ว่า semantic ถูกต้อง |
| **Null `appointment_start`** | pandas sort วางท้าย → ได้ rank สูงสุด, `is_first_case = 0` — pipeline ไม่ break | 🟢 Minor |
| **Single appointment ในวัน** | rank = 1, `is_first_case = 1` ✓ | 🟢 None |

## Privacy Impact

**ไม่มี** ✓
- Feature เป็น **ordinal relative rank** ไม่ใช่ absolute timestamp → ไม่ leak wall-clock time
- QI set ของ k-anonymity check ไม่เปลี่ยน (ยังคงเป็น `clinic + year-month + day-of-week + hour_bucket`)
- Rank สูงสุดในคลินิกใหญ่อาจเผย "scale" ของ operation แต่**ต่ำกว่าการเผย clinic_name** ซึ่งไม่มีใน output อยู่แล้ว
- Null-mask strategy: ลำดับ null/non-null ใน output ไม่เรียงตาม timestamp (row order เป็น original input order) → ไม่สร้าง side-channel

## Test Cases (ให้ Natcha run)

```python
# ⚠️ PSEUDOCODE — not runnable Python. Use as spec for writing pytest fixtures.
# Fixture 1: หมอ 1 คน, วัน 1 วัน, 3 นัด (unordered input)
df = [
    (license="D1", start="2025-03-01 09:00", appt_id="A1"),
    (license="D1", start="2025-03-01 14:00", appt_id="A2"),
    (license="D1", start="2025-03-01 10:30", appt_id="A3"),
]
# Expected: rank = [1, 3, 2], is_first_case = [1, 0, 0]

# Fixture 2: same-minute tie
df = [
    (license="D1", start="2025-03-01 09:00", appt_id="B2"),
    (license="D1", start="2025-03-01 09:00", appt_id="B1"),
]
# Expected: rank = [2, 1] (B1 < B2 alphabetical → B1 first)

# Fixture 3: null license (post-ADR-001 scenario) — CRITICAL
df = [
    (license="D1",  start="2025-03-01 09:00", appt_id="A1"),
    (license=None,  start="2025-03-01 10:00", appt_id="A2"),
    (license=None,  start="2025-03-01 11:00", appt_id="A3"),
]
# Expected: rank = [1, NaN, NaN], is_first_case = [1, 0, 0]
# ❌ ไม่ควรได้ rank=[1,1,2] (ซึ่งจะเกิดถ้า group null เป็น partition เดียวกัน)

# Fixture 4: determinism
# รัน anonymize() บน fixture เดียวกัน 2 ครั้ง → assert output เท่ากัน bit-by-bit
```

- [ ] Single dentist, 3 appointments, unordered input → rank ตามเวลา
- [ ] Same-minute tie → ลำดับตาม `appointment_id` alphabetical
- [ ] 2 dentists, same day → แต่ละคนมี own rank=1
- [ ] **Null license → rank=NaN, is_first_case=0** (NEW, critical after ADR-001)
- [ ] **Determinism: รัน 2 ครั้งได้ output identical** (NEW, guard training/serving skew)
- [ ] Integration: รันกับ sample CSV 1,000 rows → assert null เฉพาะ row ที่ `license_no` missing

## Pipeline Impact

| Aspect | Before (v1, pre-ADR-001) | After ADR-001 (v2) |
|--------|--------------------------|---------------------|
| Row count | 120,534 | ~200K |
| Column count | 18 | 20 |
| Runtime | ~8s บน 260k rows | ~10–12s บน 200k (O(n log n) sort) |
| k-anonymity | k≥5 | k≥5 (ไม่กระทบ — QI set ไม่เปลี่ยน) |
| Null rate ใน `is_first_case` | 0% | 0% (null license → 0, ไม่ใช่ NaN) |
| Null rate ใน `appointment_rank_in_day` | 0% | ≈ % ของ rows ที่ license missing |

## Sign-off Needed

- [ ] Natcha: accept spec + schedule implement
- [ ] Sun: update downstream feature pipeline รับ 2 columns ใหม่ + handle nullable `appointment_rank_in_day`
- [ ] Manifest schema version bump v1 → v2
- [ ] Privacy review: confirm null-masking strategy ไม่สร้าง side-channel

## Changelog

- **v2.1 (2026-04-18)**: เพิ่ม blocker callout (ADR-001 ยังมีสถานะ Proposed) + flagged row count discrepancy (~200K vs ~255K), แปลง Edge Cases เป็น table พร้อม severity column, เพิ่ม `# PSEUDOCODE` banner ใน test fixtures block, เพิ่ม safety comment สำหรับ `sort_values("index")`
- **v2 (2026-04-18)**: แก้ null-license handling ให้ explicit (เดิม "acceptable" ไม่พอหลังรับ ADR-001), เพิ่ม `dropna=False` ใน groupby, อัพเดต Pipeline Impact table (row count ~200K หลัง ADR-001), เพิ่ม test fixtures 3–4 (null-license + determinism)
- **v1 (2026-04-11)**: Initial spec (เขียนก่อนรับ ADR-001)
