# Treatment Class Encoding — Design Spec

**Date:** 2026-04-20
**Author:** Sun (Feature Engineering)
**Status:** Approved — ready for implementation planning
**Scope:** Extend `FeatureTransformer` with deterministic label encoding for `treatment_class`, making the Parquet feature matrix fully numeric and model-ready for tree-based classifiers (XGBoost, LightGBM, RandomForest).

---

## Context

The current `FeatureTransformer.transform()` output contains one string column: `treatment_class`. Tree-based ML libraries do not accept raw strings. All other preprocessing concerns for tree-based models are already handled:

- **Missing values**: 0 nulls in output — already handled by existing pipeline
- **Scaling/normalization**: not needed — tree splits are scale-invariant
- **One-hot encoding**: not needed — label/ordinal encoding is correct and efficient for trees

The only gap is encoding `treatment_class` string → integer.

---

## Architecture: Option A — Extend FeatureTransformer (Step 10)

A new `treatment_encoding.json` artifact is added to `src/features/artifacts/`. `FeatureTransformer` loads it at `__init__` and applies it as **Step 10** in `transform()`, after the existing column allowlist check. The Parquet output becomes fully numeric — no string columns remain.

### Why this approach

- Consistent with the existing stateless-transformer + versioned-JSON-artifacts pattern
- FastAPI inference contract unchanged — `FeatureTransformer` already used at `/predict`
- DVC tracks the encoding artifact alongside `treatment_dict.json` and the profile JSONs
- No extra preprocessing object needed at model training or serving time

### What is explicitly out of scope

| Concern | Reason skipped |
|---|---|
| Scaling `total_amount` (0–652K) | Tree splits are scale-invariant |
| Normalizing numeric features | Not needed for tree-based models |
| One-hot encoding `treatment_class` | Label encoding is correct and more efficient for trees |
| Missing value imputation | Already 0 nulls in Parquet output |
| Cyclic encoding `appt_day_of_week` | Only needed for linear/distance-based models |

---

## Encoding Artifact: `treatment_encoding.json`

### Generation

`feature_engineering.py` generates `treatment_encoding.json` by:

1. Loading `treatment_dict.json`
2. Sorting all category keys alphabetically (deterministic, reproducible)
3. Assigning integer codes 0…N-1

This runs **before** `build_and_save()` and `FeatureTransformer.transform()` so the artifact exists before it is consumed.

**No train-data dependency** — the category set is fully determined by `treatment_dict.json`, not by observed training values. This means the encoding is stable across data refreshes as long as the dict doesn't change.

### Format

```json
{
  "BRACKET_REPLACE": 0,
  "COMPOSITE_FILL": 1,
  "CONSULTATION": 2,
  "CROWN": 3,
  "DENTURE": 4,
  "EXTRACTION": 5,
  "FLUORIDE": 6,
  "IMPLANT": 7,
  "IMPRESSION": 8,
  "MEDICAL_FEE": 9,
  "MEDICATION": 10,
  "MISC_FEE": 11,
  "ORTHO_ACCESSORY": 12,
  "ORTHO_ADJUST": 13,
  "RETAINER": 14,
  "ROOT_CANAL": 15,
  "RUBBER_CHANGE": 16,
  "SCALING": 17,
  "STERILIZATION_FEE": 18,
  "SUTURE_REMOVAL": 19,
  "WHITENING": 20,
  "UNKNOWN": 21
}
```

Total: 22 classes (matches current `treatment_dict.json`).

---

## FeatureTransformer Changes

### `__init__` signature

```python
def __init__(
    self,
    doctor_profile_path: str,
    clinic_profile_path: str,
    treatment_dict_path: str,
    treatment_encoding_path: str,   # NEW
):
```

Loads `treatment_encoding.json` into `self._treatment_encoding: dict[str, int]`.

### Step 10 (new)

After Step 9 (column allowlist enforcement), before returning:

```python
# Step 10: Encode treatment_class string → integer (tree-model ready)
out["treatment_class"] = out["treatment_class"].map(self._treatment_encoding)
```

`map()` produces `NaN` for any string not in the encoding. This cannot happen in practice — the treatment mapper already collapses all unknowns to `"UNKNOWN"`, which is always in the encoding. A post-map assertion confirms no nulls are introduced.

### Named constant

```python
TREATMENT_ENCODING_PATH_DEFAULT = "src/features/artifacts/treatment_encoding.json"
```

### Output dtype

`treatment_class` column dtype becomes `int64` in Parquet. No other columns change.

---

## OOV (Out-of-Vocabulary) Handling

At inference (FastAPI), any raw treatment string flows through `treatment_mapper` first. All strings not matched by prefix regex, exact match, or fuzzy threshold map to `"UNKNOWN"`. `"UNKNOWN"` is always present in the encoding map (integer 21). No new OOV case can reach Step 10 — no error handling needed there.

---

## DVC

`treatment_encoding.json` is added to DVC tracking alongside the other artifacts:

```bash
dvc add src/features/artifacts/treatment_encoding.json
```

---

## Testing Strategy

### Unit tests (`tests/test_treatment_encoding.py`)

- Encoding map is deterministic: given same `treatment_dict.json`, always produces same map
- All 22 treatment classes present in map
- `UNKNOWN` maps to a valid integer (not null, not negative)
- Map values are unique (no two classes share the same integer)
- Map is contiguous 0…N-1 (no gaps)

### Integration tests (extend `tests/test_feature_transformer.py`)

- `transform()` output has **no string columns** — all dtypes are numeric
- `treatment_class` dtype is `int64`
- `treatment_class` values are all in range 0…21
- Encoding is stable: `transform()` called twice on same input → identical `treatment_class` integers
- Existing 34 tests still pass (no regression)

---

## File Changes

| Action | File |
|---|---|
| New | `src/features/artifacts/treatment_encoding.json` |
| Modify | `src/features/feature_transformer.py` — add `treatment_encoding_path` param + Step 10 |
| Modify | `feature_engineering.py` — generate encoding before transform |
| New | `tests/test_treatment_encoding.py` — unit tests for encoding map |
| Modify | `tests/test_feature_transformer.py` — extend integration assertions |
| DVC | `src/features/artifacts/treatment_encoding.json.dvc` |
