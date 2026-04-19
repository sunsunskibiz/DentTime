# DentTime Feature Engineering — Design Spec

**Date:** 2026-04-19
**Author:** Sun (Feature Engineering)
**Status:** Approved — ready for implementation planning
**Input:** `Data Collection/data.csv` (post-ADR-001, ~255K rows, `has_dentist_id` flag included)
**Output:** `features/features.parquet` + `features/feature_stats.json`

---

## Context

DentTime predicts appointment duration class for dental clinics (multi-class classification, target: `duration_class` ∈ {15, 30, 45, 60, 90}). This spec covers the feature engineering pipeline that transforms the anonymized CSV into a model-ready feature matrix.

**Key constraints carried into this design:**
- Post-visit columns (`checkin_delay_min`, `tx_record_offset_min`, `receipt_offset_min`) are post-visit leakage — hard-excluded
- `treatment_recorded` (zero variance) and `receipt_issued` (99.6% = 1) are excluded
- `appointment_pseudo_id` is a PK with no signal — excluded
- `has_dentist_id = 0` (52.2% of rows) is a first-class operational state, not missing data
- `appt_hour_bucket = 0` is a data quality sentinel (no scheduled time), not a midnight slot — remapped to `-1`
- Doctor/clinic profile stats must be computed on train split only (no leakage)
- The same `FeatureTransformer` class is used by both the batch CLI and FastAPI `/predict`

---

## 1. Project Structure

```
DentTime/
├── Data Collection/
│   ├── data.csv                          # input (~255K rows, post-ADR-001)
│   └── anonymize_for_ml.py
├── src/
│   └── features/
│       ├── __init__.py
│       ├── feature_transformer.py        # FeatureTransformer (stateless)
│       ├── treatment_mapper.py           # regex prefix + fuzzy match
│       ├── tooth_parser.py               # tooth_no parser (4 formats)
│       ├── build_profiles.py             # build doctor/clinic JSONs from train split
│       └── artifacts/                    # DVC-tracked
│           ├── doctor_profile.json
│           ├── clinic_profile.json
│           └── treatment_dict.json
├── features/                             # DVC-tracked feature store output
│   ├── features_train.parquet
│   ├── features_test.parquet
│   └── feature_stats.json
├── tests/
│   ├── test_treatment_mapper.py
│   ├── test_tooth_parser.py
│   └── test_feature_transformer.py
└── feature_engineering.py               # CLI entrypoint
```

---

## 2. Architecture: Option B — Stateless Transformer + Versioned Lookup Tables

`build_profiles.py` runs once on the train split and writes `doctor_profile.json` and `clinic_profile.json` to `src/features/artifacts/`. `FeatureTransformer` is stateless — it loads those JSONs at `__init__` and applies all transformations deterministically. FastAPI loads the same transformer with the same JSON paths.

**Why Option B over sklearn fit/transform:**
- Profiles are human-readable and auditable independently of the model artifact
- DVC tracks transformer code and lookup tables separately — cleaner lineage
- Profiles can be inspected or patched without touching transformer code

---

## 3. Data Flow

```
feature_engineering.py --input data.csv --output features/
│
├── 1. Load data.csv (~255K rows)
├── 2. Time-based split (no random split — prevents leakage)
│       train = appt_year_month ≤ 2025-02
│       test  = appt_year_month == 2025-04
│       (2025-03 missing — documented in DVC metadata as known gap)
│
├── 3. build_profiles.py(train_df)
│       → src/features/artifacts/doctor_profile.json
│       → src/features/artifacts/clinic_profile.json
│
├── 4. FeatureTransformer.transform(train_df) → features_train.parquet
├── 5. FeatureTransformer.transform(test_df)  → features_test.parquet
└── 6. Write feature_stats.json (computed from train only)
```

**`FeatureTransformer.transform(df)` column pipeline (in order):**

1. **Leakage guard** — assert `checkin_delay_min`, `tx_record_offset_min`, `receipt_offset_min` are absent. Raise `ValueError` immediately if present (hard stop, not a warning).
2. **`appt_hour_bucket` remap** — `0 → -1` (sentinel for unknown scheduled time). Applied before any other time feature logic.
3. **Treatment mapper** — `treatment_class`, `composite_treatment_flag`
4. **Tooth parser** — `has_tooth_no`, `tooth_count`, `is_area_treatment`
5. **Surface count** — `surface_count`
6. **Doctor lookup** — `doctor_median_duration`, `doctor_pct_long`
7. **Clinic lookup** — `clinic_median_duration`, `clinic_pct_long`
8. **Target binning** — `duration_class` from `scheduled_duration_min`
9. **Column select** — output exactly the 17 feature columns + `scheduled_duration_min` (audit) + `duration_class` (target). Raise if any expected column is missing.

---

## 4. Treatment Features

### 4a. `treatment_mapper.py` — Three-Stage Pipeline

```
Raw treatment string
│
├── Stage 1: Structured prefix regex
│   Pattern: ^([A-Za-z]+)\s*—
│   e.g. "At — ปรับเครื่องมือ" → code "At" → lookup in treatment_dict.json
│   Covers ~5,126 rows exactly. No fuzzy needed.
│
├── Stage 2: Exact string match
│   Lookup raw string directly in treatment_dict.json value lists
│
└── Stage 3: RapidFuzz token_sort_ratio
    FUZZY_MATCH_THRESHOLD = 85  (named constant)
    Match against all dict keys
    Below threshold → treatment_class = "UNKNOWN"
    Log UNKNOWN rate; warn if > 10% (dict needs expansion)
```

### 4b. `treatment_dict.json` — Mandatory Categories

Seeded from top-100 treatment strings by frequency (covers ~80% of rows):

```json
{
  "ORTHO_ADJUST":      ["ปรับเครื่องมือจัดฟัน", "ปรับเครื่องมือ", "At"],
  "SCALING":           ["ขูดหินปูน", "SC"],
  "STERILIZATION_FEE": ["ค่าปลอดเชื้อ", "ค่าปลอดเชื้อครื่องมือ"],
  "EXTRACTION":        ["ถอนฟันแท้", "ถอนฟัน", "Ext"],
  "COMPOSITE_FILL":    ["อุดฟันคอมโพสิท", "อุดฟันคอมโพสิท 1 ด้าน"],
  "CONSULTATION":      ["ปรึกษา"],
  "RUBBER_CHANGE":     ["เปลี่ยนยาง", "เปลี่ยนยางจัดฟัน"],
  "UNKNOWN":           []
}
```

Total: 15–20 categories covering mandatory classes listed above.

### 4c. `tooth_parser.py` — Four Format Handlers

| Input | `has_tooth_no` | `tooth_count` | `is_area_treatment` |
|---|---|---|---|
| `null` | 0 | 0 | 0 |
| `"46"` | 1 | 1 | 0 |
| `"11,12,13"` | 1 | 3 | 0 |
| `"Full mouth"` / `"Upper"` / `"Lower"` | 1 | 0 | 1 |

### 4d. Derived flags

- **`composite_treatment_flag`**: 1 when `treatment_class == "COMPOSITE_FILL"`, else 0
- **`surface_count`**: `len(surfaces.split(","))` when not null, else `0`. Null = non-restorative treatment (informative, not missing — do not impute)

---

## 5. Clinic Profile Features (Phase C)

**Computed from train split only** by `build_profiles.py`.

Stats per `clinic_pseudo_id`:
- `clinic_median_duration`: median of `scheduled_duration_min`
- `clinic_pct_long`: % of appointments ≥ 60 min

**Cold-start threshold: 30 cases.**
Rationale: with 3 months of data across 266 clinics, the long tail of small clinics has fewer than 30 cases. Below 30 cases (~10 days of data), variance is too high to trust the clinic-specific estimate → substitute `__global__` median.

**`clinic_profile.json` shape:**
```json
{
  "C_abc123": { "clinic_median_duration": 30.0, "clinic_pct_long": 0.12, "case_count": 450 },
  "__global__": { "clinic_median_duration": 28.0, "clinic_pct_long": 0.09, "case_count": 255000 }
}
```

---

## 6. Doctor Profile Features (Phase D)

**Computed from train split only** by `build_profiles.py`.

Stats per `dentist_pseudo_id`:
- `doctor_median_duration`: median of `scheduled_duration_min`
- `doctor_pct_long`: % of appointments ≥ 60 min

**Lookup logic (per row):**

```
if has_dentist_id == 0:
    → use __global__ directly  (first-class state, 52.2% of live traffic)
elif dentist_pseudo_id in doctor_profile AND case_count >= 30:
    → use doctor stats
else:
    → use __global__           (cold-start or unknown doctor)
```

`has_dentist_id = 0` is the normal path, not an error. Do not impute a dummy dentist ID.

**`appointment_rank_in_day` handling:**
- `has_dentist_id = 1` → pass through (nullable Int64 per ADR-001 spec)
- `has_dentist_id = 0` → set to `0` (not null — avoids additional missingness in feature matrix)

**`is_first_case`:** computed upstream in `anonymize_for_ml.py` — pass through directly.

---

## 7. Target Binning Rule

Maps `scheduled_duration_min` → `duration_class` ∈ {15, 30, 45, 60, 90}.

| `scheduled_duration_min` range | `duration_class` |
|---|---|
| ≤ 22 | 15 |
| 23–37 | 30 |
| 38–52 | 45 |
| 53–75 | 60 |
| > 75 | 90 |

Covers non-standard durations: 10→15, 20→15, 40→30, 120→90.
Affects ~11.7% of rows (29,500). `scheduled_duration_min` is kept as a separate column in Parquet for auditability — never overwritten.

---

## 8. Final Feature Matrix (17 columns)

| Column | Source | Notes |
|---|---|---|
| `treatment_class` | Phase B | categorical string |
| `composite_treatment_flag` | Phase B | binary |
| `has_tooth_no` | Phase B | binary |
| `tooth_count` | Phase B | integer |
| `is_area_treatment` | Phase B | binary |
| `surface_count` | Phase B | integer |
| `total_amount` | pass-through | float |
| `has_notes` | pass-through | binary |
| `appt_day_of_week` | pass-through | 0–6 |
| `appt_hour_bucket` | remapped | -1 or {4,8,12,16,20} |
| `is_first_case` | pass-through | binary |
| `has_dentist_id` | pass-through | binary |
| `appointment_rank_in_day` | pass-through + 0-fill | integer |
| `clinic_median_duration` | Phase C | float |
| `clinic_pct_long` | Phase C | float |
| `doctor_median_duration` | Phase D | float |
| `doctor_pct_long` | Phase D | float |

Plus: `scheduled_duration_min` (audit only) and `duration_class` (target label).

---

## 9. Feature Stats Sidecar (`feature_stats.json`)

Computed from train split only. Becomes the baseline snapshot for drift monitoring.

```json
{
  "treatment_class": { "null_rate": 0.0, "unknown_rate": 0.08, "top5": {} },
  "appt_hour_bucket": { "null_rate": 0.0, "mean": 10.4, "pct_sentinel": 0.11 },
  "has_dentist_id": { "null_rate": 0.0, "mean": 0.48 }
}
```

---

## 10. DVC Metadata

Parquet registered in DVC with:
- Date range: 2025-01, 2025-02, 2025-04
- Known gap: 2025-03 missing from source data
- Row count: ~255,000 (post-ADR-001)
- Label distribution: documented per `duration_class` bin

---

## 11. FastAPI Inference Contract

`FeatureTransformer` is imported directly into the `/predict` route handler — no inline logic in the route.

**Pydantic request schema must:**
- Exclude `checkin_delay_min`, `tx_record_offset_min`, `receipt_offset_min` at the schema level (impossible to pass accidentally)
- Allow `dentist_pseudo_id = null` (maps to `has_dentist_id = 0`)
- Allow `tooth_no = null` (maps to `has_tooth_no=0, tooth_count=0, is_area_treatment=0`)
- Allow `surfaces = null` (maps to `surface_count=0`)

**Known edge cases handled explicitly (not exceptions):**
- `has_dentist_id = 0` → global fallback (normal path)
- `treatment` below fuzzy threshold → `UNKNOWN`, log raw string
- `appt_hour_bucket = 0` → remap to `-1` (same as batch)
- Unknown `clinic_pseudo_id` → global median fallback, log as cold-start event

---

## 12. Testing Strategy

### Unit Tests

**`test_tooth_parser.py`:**
- null → `{has_tooth_no:0, tooth_count:0, is_area_treatment:0}`
- single FDI integer `"46"` → `tooth_count=1`
- comma-separated `"11,12,13"` → `tooth_count=3`
- area label `"Full mouth"`, `"Upper"`, `"Lower"` → `is_area_treatment=1`

**`test_treatment_mapper.py`:**
- exact match hit → correct class
- structured prefix `"At — ..."` → `ORTHO_ADJUST`
- fuzzy match hit (score ≥ 85) → correct class
- fuzzy match miss (score < 85) → `UNKNOWN`
- null input → `UNKNOWN`

### Integration Test

**`test_feature_transformer.py`** — 50-row fixture CSV:
- leakage column present → `ValueError` raised
- `appt_hour_bucket=0` rows → output has `-1`
- `has_dentist_id=0` rows → global fallback values in doctor columns
- output column set matches exact 17-column allowlist (no extras, no missing)
- `duration_class` binning spot-checks: input 10→class 15, input 120→class 90
- determinism: `transform()` called twice on same input → identical output

---

## 13. Key Risks

| Risk | Mitigation |
|---|---|
| Training-serving skew | Shared `FeatureTransformer` — single code path for batch and FastAPI |
| Data leakage (doctor/clinic stats) | `build_profiles.py` accepts train split only; CLI enforces split before calling it |
| Fuzzy match ambiguity | `FUZZY_MATCH_THRESHOLD = 85` is a named constant; UNKNOWN rate logged and warned if > 10% |
| `appt_hour_bucket=0` silent corruption | Remapped to `-1` in Step 2 of transformer pipeline, before any downstream logic |
| Cold-start for new clinics/doctors | Global fallback via `__global__` key in both profile JSONs |
