# DentTime Feature Engineering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stateless `FeatureTransformer` and CLI script that transforms the post-ADR-001 anonymized CSV (~255K rows) into a model-ready Parquet feature matrix with doctor/clinic profile lookups, treatment fuzzy matching, and ceiling-binned duration targets.

**Architecture:** Option B — stateless transformer loading versioned JSON lookup tables (doctor/clinic profiles, treatment dict). `build_profiles.py` runs once on train split to produce the JSONs; `FeatureTransformer` loads them at init and applies all transformations deterministically. Same class used by both `feature_engineering.py` CLI and FastAPI `/predict`.

**Tech Stack:** Python 3.10+, pandas, rapidfuzz, pyarrow (Parquet), pytest

**Spec:** `docs/superpowers/specs/2026-04-19-feature-engineering-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/__init__.py` | Create | Package marker |
| `src/features/__init__.py` | Create | Package marker |
| `src/features/tooth_parser.py` | Create | Parse `tooth_no` into 3 binary/integer features |
| `src/features/treatment_mapper.py` | Create | Regex prefix + exact + fuzzy match → `treatment_class` |
| `src/features/artifacts/treatment_dict.json` | Create | Canonical treatment category seed (15–20 categories) |
| `src/features/build_profiles.py` | Create | Compute doctor/clinic stats from train split → JSON |
| `src/features/feature_transformer.py` | Create | Stateless transformer: all 9 pipeline steps |
| `feature_engineering.py` | Create | CLI entrypoint: split → build profiles → transform → write Parquet |
| `tests/__init__.py` | Create | Package marker |
| `tests/test_tooth_parser.py` | Create | Unit tests: 7 cases covering all 4 formats |
| `tests/test_treatment_mapper.py` | Create | Unit tests: 5 cases (exact, prefix, fuzzy hit, fuzzy miss, null) |
| `tests/test_build_profiles.py` | Create | Unit tests: cold-start threshold, global fallback, has_dentist_id=0 |
| `tests/test_feature_transformer.py` | Create | Integration tests: leakage guard, sentinel remap, allowlist, binning, determinism |

---

## Task 1: Project Scaffold + Dependencies

**Files:**
- Create: `src/__init__.py`
- Create: `src/features/__init__.py`
- Create: `tests/__init__.py`
- Create: `requirements-fe.txt`

- [ ] **Step 1.1: Create package markers and requirements file**

```bash
mkdir -p "src/features/artifacts"
touch "src/__init__.py" "src/features/__init__.py" "tests/__init__.py"
```

Create `requirements-fe.txt`:
```
pandas>=2.0
rapidfuzz>=3.0
pyarrow>=14.0
pytest>=7.0
```

- [ ] **Step 1.2: Install dependencies**

```bash
pip install -r requirements-fe.txt
```

Expected output includes: `Successfully installed pandas-... rapidfuzz-... pyarrow-...`

- [ ] **Step 1.3: Verify imports work**

```bash
python -c "import pandas; import rapidfuzz; import pyarrow; print('OK')"
```

Expected: `OK`

- [ ] **Step 1.4: Commit scaffold**

```bash
git add src/ tests/ requirements-fe.txt
git commit -m "feat: scaffold src/features package and test directories"
```

---

## Task 2: `tooth_parser.py` (TDD)

**Files:**
- Create: `src/features/tooth_parser.py`
- Create: `tests/test_tooth_parser.py`

- [ ] **Step 2.1: Write all failing tests**

Create `tests/test_tooth_parser.py`:
```python
import math
import pytest
from src.features.tooth_parser import parse_tooth_no


def test_null_returns_zeros():
    assert parse_tooth_no(None) == {"has_tooth_no": 0, "tooth_count": 0, "is_area_treatment": 0}


def test_nan_returns_zeros():
    assert parse_tooth_no(float("nan")) == {"has_tooth_no": 0, "tooth_count": 0, "is_area_treatment": 0}


def test_single_fdi_integer():
    assert parse_tooth_no("46") == {"has_tooth_no": 1, "tooth_count": 1, "is_area_treatment": 0}


def test_comma_separated_list():
    assert parse_tooth_no("11,12,13") == {"has_tooth_no": 1, "tooth_count": 3, "is_area_treatment": 0}


def test_full_mouth():
    assert parse_tooth_no("Full mouth") == {"has_tooth_no": 1, "tooth_count": 0, "is_area_treatment": 1}


def test_upper():
    assert parse_tooth_no("Upper") == {"has_tooth_no": 1, "tooth_count": 0, "is_area_treatment": 1}


def test_lower():
    assert parse_tooth_no("Lower") == {"has_tooth_no": 1, "tooth_count": 0, "is_area_treatment": 1}
```

- [ ] **Step 2.2: Run tests to confirm they all fail**

```bash
cd "/Users/sunsun/Library/CloudStorage/GoogleDrive-chantapat.sun@gmail.com/My Drive/Study/Semester2/SE_for_ML/DentTime"
python -m pytest tests/test_tooth_parser.py -v
```

Expected: `7 failed` with `ModuleNotFoundError` or `ImportError`.

- [ ] **Step 2.3: Implement `tooth_parser.py`**

Create `src/features/tooth_parser.py`:
```python
import math
from typing import Optional

_AREA_LABELS = {"full mouth", "upper", "lower"}


def parse_tooth_no(tooth_no: Optional[str]) -> dict:
    if tooth_no is None:
        return {"has_tooth_no": 0, "tooth_count": 0, "is_area_treatment": 0}
    if isinstance(tooth_no, float) and math.isnan(tooth_no):
        return {"has_tooth_no": 0, "tooth_count": 0, "is_area_treatment": 0}

    s = str(tooth_no).strip()

    if s.lower() in _AREA_LABELS:
        return {"has_tooth_no": 1, "tooth_count": 0, "is_area_treatment": 1}

    parts = [p.strip() for p in s.split(",") if p.strip()]
    return {"has_tooth_no": 1, "tooth_count": len(parts), "is_area_treatment": 0}
```

- [ ] **Step 2.4: Run tests to confirm they all pass**

```bash
python -m pytest tests/test_tooth_parser.py -v
```

Expected: `7 passed`

- [ ] **Step 2.5: Commit**

```bash
git add src/features/tooth_parser.py tests/test_tooth_parser.py
git commit -m "feat: add tooth_no parser with 4-format support (TDD)"
```

---

## Task 3: `treatment_dict.json` Seed File

**Files:**
- Create: `src/features/artifacts/treatment_dict.json`

- [ ] **Step 3.1: Create the seed treatment dictionary**

Create `src/features/artifacts/treatment_dict.json`:
```json
{
  "ORTHO_ADJUST": [
    "ปรับเครื่องมือจัดฟัน",
    "ปรับเครื่องมือ",
    "เปลี่ยนยาง/ปรับเครื่องมือจัดฟัน",
    "At"
  ],
  "SCALING": [
    "ขูดหินปูน",
    "ขูดหินปูนทั้งปาก",
    "SC"
  ],
  "STERILIZATION_FEE": [
    "ค่าปลอดเชื้อ",
    "ค่าปลอดเชื้อครื่องมือ",
    "ค่าปลอดเชื้อเครื่องมือ"
  ],
  "EXTRACTION": [
    "ถอนฟันแท้",
    "ถอนฟัน",
    "ถอนฟันน้ำนม",
    "Ext"
  ],
  "COMPOSITE_FILL": [
    "อุดฟันคอมโพสิท",
    "อุดฟันคอมโพสิท 1 ด้าน",
    "อุดฟันคอมโพสิท 2 ด้าน",
    "อุดฟันคอมโพสิท 3 ด้าน"
  ],
  "CONSULTATION": [
    "ปรึกษา",
    "ตรวจและวางแผนการรักษา",
    "ตรวจฟัน"
  ],
  "RUBBER_CHANGE": [
    "เปลี่ยนยาง",
    "เปลี่ยนยางจัดฟัน",
    "Rebond"
  ],
  "MEDICAL_FEE": [
    "รวมค่าบริการทางการแพทย์",
    "* รวมค่าบริการทางการแพทย์",
    "ค่าบริการ"
  ],
  "XRAY": [
    "ถ่ายภาพรังสี",
    "X-ray",
    "Xray",
    "ถ่าย X-ray"
  ],
  "ROOT_CANAL": [
    "รักษารากฟัน",
    "RCT",
    "Root canal"
  ],
  "CROWN": [
    "ครอบฟัน",
    "ใส่ครอบฟัน",
    "Crown"
  ],
  "DENTURE": [
    "ฟันปลอม",
    "ใส่ฟันปลอม",
    "ฟันปลอมบางส่วน"
  ],
  "WHITENING": [
    "ฟอกสีฟัน",
    "Bleaching"
  ],
  "IMPLANT": [
    "รากฟันเทียม",
    "Implant"
  ],
  "UNKNOWN": []
}
```

- [ ] **Step 3.2: Commit**

```bash
git add src/features/artifacts/treatment_dict.json
git commit -m "feat: add treatment_dict.json seed with 14 canonical categories"
```

---

## Task 4: `treatment_mapper.py` (TDD)

**Files:**
- Create: `src/features/treatment_mapper.py`
- Create: `tests/test_treatment_mapper.py`

- [ ] **Step 4.1: Write all failing tests**

Create `tests/test_treatment_mapper.py`:
```python
import pytest
from src.features.treatment_mapper import build_reverse_map, map_treatment, FUZZY_MATCH_THRESHOLD

SAMPLE_DICT = {
    "ORTHO_ADJUST": ["ปรับเครื่องมือจัดฟัน", "ปรับเครื่องมือ", "At"],
    "SCALING": ["ขูดหินปูน", "SC"],
    "COMPOSITE_FILL": ["อุดฟันคอมโพสิท"],
    "UNKNOWN": [],
}


@pytest.fixture
def reverse_map():
    return build_reverse_map(SAMPLE_DICT)


def test_exact_match(reverse_map):
    assert map_treatment("ขูดหินปูน", SAMPLE_DICT, reverse_map) == "SCALING"


def test_structured_prefix_match(reverse_map):
    # "At — ปรับเครื่องมือ" has prefix "At" which maps to ORTHO_ADJUST
    assert map_treatment("At — ปรับเครื่องมือจัดฟัน", SAMPLE_DICT, reverse_map) == "ORTHO_ADJUST"


def test_fuzzy_match_hit(reverse_map):
    # "ขูดหินปูนทั้งปาก" is close enough to "ขูดหินปูน" (score >= 85)
    assert map_treatment("ขูดหินปูนทั้งปาก", SAMPLE_DICT, reverse_map) == "SCALING"


def test_fuzzy_match_miss_returns_unknown(reverse_map):
    assert map_treatment("XXXXXXXXXGARBAGE99999", SAMPLE_DICT, reverse_map) == "UNKNOWN"


def test_null_returns_unknown(reverse_map):
    assert map_treatment(None, SAMPLE_DICT, reverse_map) == "UNKNOWN"


def test_threshold_constant_is_85():
    assert FUZZY_MATCH_THRESHOLD == 85
```

- [ ] **Step 4.2: Run tests to confirm they all fail**

```bash
python -m pytest tests/test_treatment_mapper.py -v
```

Expected: `6 failed` with `ModuleNotFoundError`.

- [ ] **Step 4.3: Implement `treatment_mapper.py`**

Create `src/features/treatment_mapper.py`:
```python
import json
import re
import math
import logging
from typing import Optional
from rapidfuzz import process, fuzz

FUZZY_MATCH_THRESHOLD = 85

_PREFIX_RE = re.compile(r"^([A-Za-z]+)\s*—")


def load_treatment_dict(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_reverse_map(treatment_dict: dict) -> dict:
    """Maps each alias string (lowercased) to its canonical class name."""
    reverse = {}
    for class_name, aliases in treatment_dict.items():
        for alias in aliases:
            reverse[alias.lower()] = class_name
    return reverse


def map_treatment(raw: Optional[str], treatment_dict: dict, reverse_map: dict) -> str:
    if raw is None:
        return "UNKNOWN"
    if isinstance(raw, float) and math.isnan(raw):
        return "UNKNOWN"

    s = str(raw).strip()

    # Stage 1: structured prefix regex (e.g. "At — ปรับเครื่องมือ")
    m = _PREFIX_RE.match(s)
    if m:
        code = m.group(1).lower()
        if code in reverse_map:
            return reverse_map[code]

    # Stage 2: exact string match
    if s.lower() in reverse_map:
        return reverse_map[s.lower()]

    # Stage 3: fuzzy match
    candidates = list(reverse_map.keys())
    result = process.extractOne(s.lower(), candidates, scorer=fuzz.token_sort_ratio)
    if result and result[1] >= FUZZY_MATCH_THRESHOLD:
        return reverse_map[result[0]]

    return "UNKNOWN"
```

- [ ] **Step 4.4: Run tests to confirm they all pass**

```bash
python -m pytest tests/test_treatment_mapper.py -v
```

Expected: `6 passed`

- [ ] **Step 4.5: Commit**

```bash
git add src/features/treatment_mapper.py tests/test_treatment_mapper.py
git commit -m "feat: add treatment mapper with regex prefix + fuzzy match (TDD)"
```

---

## Task 5: `build_profiles.py` (TDD)

**Files:**
- Create: `src/features/build_profiles.py`
- Create: `tests/test_build_profiles.py`

- [ ] **Step 5.1: Write all failing tests**

Create `tests/test_build_profiles.py`:
```python
import pandas as pd
import pytest
from src.features.build_profiles import build_doctor_profile, build_clinic_profile, COLD_START_THRESHOLD


def _make_train_df(n_dentist_rows=35, n_no_dentist_rows=10):
    """Minimal train fixture: one dentist with enough cases, one clinic."""
    rows = []
    for i in range(n_dentist_rows):
        rows.append({
            "dentist_pseudo_id": "D_test01",
            "clinic_pseudo_id": "C_test01",
            "has_dentist_id": 1,
            "scheduled_duration_min": 30 if i < 30 else 90,
        })
    for i in range(n_no_dentist_rows):
        rows.append({
            "dentist_pseudo_id": None,
            "clinic_pseudo_id": "C_test01",
            "has_dentist_id": 0,
            "scheduled_duration_min": 45,
        })
    return pd.DataFrame(rows)


def test_doctor_profile_includes_global_key():
    df = _make_train_df()
    profile = build_doctor_profile(df)
    assert "__global__" in profile


def test_doctor_profile_cold_start_excluded_when_below_threshold():
    # Dentist with only 5 cases — below threshold of 30
    df = pd.DataFrame([
        {"dentist_pseudo_id": "D_small", "clinic_pseudo_id": "C_x",
         "has_dentist_id": 1, "scheduled_duration_min": 30}
        for _ in range(5)
    ])
    profile = build_doctor_profile(df)
    assert "D_small" not in profile
    assert "__global__" in profile


def test_doctor_profile_included_when_above_threshold():
    df = _make_train_df(n_dentist_rows=35)
    profile = build_doctor_profile(df)
    assert "D_test01" in profile
    assert profile["D_test01"]["case_count"] == 35


def test_doctor_profile_pct_long_correct():
    # 5 rows at 90 min (long), 30 rows at 30 min → pct_long = 5/35
    df = _make_train_df(n_dentist_rows=35)
    profile = build_doctor_profile(df)
    expected_pct = 5 / 35
    assert abs(profile["D_test01"]["doctor_pct_long"] - expected_pct) < 0.001


def test_clinic_profile_cold_start_threshold():
    assert COLD_START_THRESHOLD == 30


def test_clinic_profile_global_uses_all_rows():
    df = _make_train_df(n_dentist_rows=35, n_no_dentist_rows=10)
    profile = build_clinic_profile(df)
    assert profile["__global__"]["case_count"] == 45
```

- [ ] **Step 5.2: Run tests to confirm they all fail**

```bash
python -m pytest tests/test_build_profiles.py -v
```

Expected: `6 failed` with `ModuleNotFoundError`.

- [ ] **Step 5.3: Implement `build_profiles.py`**

Create `src/features/build_profiles.py`:
```python
import json
from pathlib import Path
import pandas as pd

COLD_START_THRESHOLD = 30


def build_doctor_profile(train_df: pd.DataFrame) -> dict:
    profile = {}

    dentist_rows = train_df[train_df["has_dentist_id"] == 1]
    for dentist_id, group in dentist_rows.groupby("dentist_pseudo_id")["scheduled_duration_min"]:
        if len(group) >= COLD_START_THRESHOLD:
            profile[dentist_id] = {
                "doctor_median_duration": float(group.median()),
                "doctor_pct_long": float((group >= 60).mean()),
                "case_count": int(len(group)),
            }

    all_dur = train_df["scheduled_duration_min"]
    profile["__global__"] = {
        "doctor_median_duration": float(all_dur.median()),
        "doctor_pct_long": float((all_dur >= 60).mean()),
        "case_count": int(len(all_dur)),
    }
    return profile


def build_clinic_profile(train_df: pd.DataFrame) -> dict:
    profile = {}

    for clinic_id, group in train_df.groupby("clinic_pseudo_id")["scheduled_duration_min"]:
        if len(group) >= COLD_START_THRESHOLD:
            profile[clinic_id] = {
                "clinic_median_duration": float(group.median()),
                "clinic_pct_long": float((group >= 60).mean()),
                "case_count": int(len(group)),
            }

    all_dur = train_df["scheduled_duration_min"]
    profile["__global__"] = {
        "clinic_median_duration": float(all_dur.median()),
        "clinic_pct_long": float((all_dur >= 60).mean()),
        "case_count": int(len(all_dur)),
    }
    return profile


def build_and_save(train_df: pd.DataFrame, output_dir: Path) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    doctor_profile = build_doctor_profile(train_df)
    clinic_profile = build_clinic_profile(train_df)

    with open(output_dir / "doctor_profile.json", "w", encoding="utf-8") as f:
        json.dump(doctor_profile, f, indent=2, ensure_ascii=False)

    with open(output_dir / "clinic_profile.json", "w", encoding="utf-8") as f:
        json.dump(clinic_profile, f, indent=2, ensure_ascii=False)
```

- [ ] **Step 5.4: Run tests to confirm they all pass**

```bash
python -m pytest tests/test_build_profiles.py -v
```

Expected: `6 passed`

- [ ] **Step 5.5: Commit**

```bash
git add src/features/build_profiles.py tests/test_build_profiles.py
git commit -m "feat: add build_profiles with cold-start threshold and global fallback (TDD)"
```

---

## Task 6: `feature_transformer.py` (TDD — Integration)

**Files:**
- Create: `src/features/feature_transformer.py`
- Create: `tests/test_feature_transformer.py`

- [ ] **Step 6.1: Write all failing integration tests**

Create `tests/test_feature_transformer.py`:
```python
import json
import math
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from src.features.feature_transformer import FeatureTransformer, FEATURE_COLUMNS

# --- Fixtures ---

SAMPLE_TREATMENT_DICT = {
    "SCALING": ["ขูดหินปูน", "SC"],
    "EXTRACTION": ["ถอนฟันแท้", "Ext"],
    "COMPOSITE_FILL": ["อุดฟันคอมโพสิท"],
    "UNKNOWN": [],
}

SAMPLE_DOCTOR_PROFILE = {
    "D_test01": {"doctor_median_duration": 30.0, "doctor_pct_long": 0.10, "case_count": 50},
    "__global__": {"doctor_median_duration": 28.0, "doctor_pct_long": 0.09, "case_count": 255000},
}

SAMPLE_CLINIC_PROFILE = {
    "C_test01": {"clinic_median_duration": 30.0, "clinic_pct_long": 0.12, "case_count": 200},
    "__global__": {"clinic_median_duration": 28.0, "clinic_pct_long": 0.09, "case_count": 255000},
}


@pytest.fixture
def artifact_dir(tmp_path):
    (tmp_path / "treatment_dict.json").write_text(
        json.dumps(SAMPLE_TREATMENT_DICT), encoding="utf-8"
    )
    (tmp_path / "doctor_profile.json").write_text(
        json.dumps(SAMPLE_DOCTOR_PROFILE), encoding="utf-8"
    )
    (tmp_path / "clinic_profile.json").write_text(
        json.dumps(SAMPLE_CLINIC_PROFILE), encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def transformer(artifact_dir):
    return FeatureTransformer(
        doctor_profile_path=str(artifact_dir / "doctor_profile.json"),
        clinic_profile_path=str(artifact_dir / "clinic_profile.json"),
        treatment_dict_path=str(artifact_dir / "treatment_dict.json"),
    )


def _base_row(**overrides):
    row = {
        "clinic_pseudo_id": "C_test01",
        "dentist_pseudo_id": "D_test01",
        "has_dentist_id": 1,
        "treatment": "ขูดหินปูน",
        "tooth_no": "46",
        "surfaces": None,
        "total_amount": 500.0,
        "has_notes": 0,
        "appt_day_of_week": 1,
        "appt_hour_bucket": 8,
        "scheduled_duration_min": 30,
        "appointment_rank_in_day": 1,
        "is_first_case": 1,
    }
    row.update(overrides)
    return row


def _make_df(rows):
    return pd.DataFrame(rows)


# --- Tests ---

def test_leakage_column_raises_value_error(transformer):
    df = _make_df([_base_row(checkin_delay_min=5)])
    with pytest.raises(ValueError, match="leakage"):
        transformer.transform(df)


def test_hour_bucket_zero_remapped_to_minus_one(transformer):
    df = _make_df([_base_row(appt_hour_bucket=0)])
    result = transformer.transform(df)
    assert result["appt_hour_bucket"].iloc[0] == -1


def test_hour_bucket_nonzero_unchanged(transformer):
    df = _make_df([_base_row(appt_hour_bucket=8)])
    result = transformer.transform(df)
    assert result["appt_hour_bucket"].iloc[0] == 8


def test_has_dentist_id_zero_uses_global_fallback(transformer):
    df = _make_df([_base_row(has_dentist_id=0, dentist_pseudo_id=None, appointment_rank_in_day=None)])
    result = transformer.transform(df)
    assert result["doctor_median_duration"].iloc[0] == SAMPLE_DOCTOR_PROFILE["__global__"]["doctor_median_duration"]
    assert result["appointment_rank_in_day"].iloc[0] == 0


def test_output_columns_match_exact_allowlist(transformer):
    df = _make_df([_base_row()])
    result = transformer.transform(df)
    expected = set(FEATURE_COLUMNS) | {"scheduled_duration_min", "duration_class"}
    assert set(result.columns) == expected


def test_duration_binning_10_to_15(transformer):
    df = _make_df([_base_row(scheduled_duration_min=10)])
    result = transformer.transform(df)
    assert result["duration_class"].iloc[0] == 15


def test_duration_binning_20_to_30(transformer):
    df = _make_df([_base_row(scheduled_duration_min=20)])
    result = transformer.transform(df)
    assert result["duration_class"].iloc[0] == 30


def test_duration_binning_40_to_45(transformer):
    df = _make_df([_base_row(scheduled_duration_min=40)])
    result = transformer.transform(df)
    assert result["duration_class"].iloc[0] == 45


def test_duration_binning_120_to_105(transformer):
    df = _make_df([_base_row(scheduled_duration_min=120)])
    result = transformer.transform(df)
    assert result["duration_class"].iloc[0] == 105


def test_scheduled_duration_min_preserved_in_output(transformer):
    df = _make_df([_base_row(scheduled_duration_min=20)])
    result = transformer.transform(df)
    assert result["scheduled_duration_min"].iloc[0] == 20


def test_determinism(transformer):
    df = _make_df([_base_row(), _base_row(appt_hour_bucket=0, has_dentist_id=0,
                                           dentist_pseudo_id=None, appointment_rank_in_day=None)])
    result1 = transformer.transform(df.copy())
    result2 = transformer.transform(df.copy())
    pd.testing.assert_frame_equal(result1.reset_index(drop=True), result2.reset_index(drop=True))


def test_composite_fill_flag_set(transformer):
    df = _make_df([_base_row(treatment="อุดฟันคอมโพสิท")])
    result = transformer.transform(df)
    assert result["composite_treatment_flag"].iloc[0] == 1


def test_composite_fill_flag_not_set_for_other_treatment(transformer):
    df = _make_df([_base_row(treatment="ขูดหินปูน")])
    result = transformer.transform(df)
    assert result["composite_treatment_flag"].iloc[0] == 0


def test_surface_count_null_is_zero(transformer):
    df = _make_df([_base_row(surfaces=None)])
    result = transformer.transform(df)
    assert result["surface_count"].iloc[0] == 0


def test_surface_count_comma_separated(transformer):
    df = _make_df([_base_row(surfaces="4,5")])
    result = transformer.transform(df)
    assert result["surface_count"].iloc[0] == 2
```

- [ ] **Step 6.2: Run tests to confirm they all fail**

```bash
python -m pytest tests/test_feature_transformer.py -v
```

Expected: `14 failed` with `ModuleNotFoundError`.

- [ ] **Step 6.3: Implement `feature_transformer.py`**

Create `src/features/feature_transformer.py`:
```python
import json
import logging
import math
from typing import Optional

import pandas as pd

from .treatment_mapper import build_reverse_map, load_treatment_dict, map_treatment
from .tooth_parser import parse_tooth_no

LEAKAGE_COLUMNS = {"checkin_delay_min", "tx_record_offset_min", "receipt_offset_min"}

FEATURE_COLUMNS = [
    "treatment_class",
    "composite_treatment_flag",
    "has_tooth_no",
    "tooth_count",
    "is_area_treatment",
    "surface_count",
    "total_amount",
    "has_notes",
    "appt_day_of_week",
    "appt_hour_bucket",
    "is_first_case",
    "has_dentist_id",
    "appointment_rank_in_day",
    "clinic_median_duration",
    "clinic_pct_long",
    "doctor_median_duration",
    "doctor_pct_long",
]

_DURATION_CLASSES = [15, 30, 45, 60, 90, 105]


def _bin_duration(minutes: int) -> int:
    for cls in _DURATION_CLASSES:
        if minutes <= cls:
            return cls
    return _DURATION_CLASSES[-1]


class FeatureTransformer:
    def __init__(
        self,
        doctor_profile_path: str,
        clinic_profile_path: str,
        treatment_dict_path: str,
    ):
        with open(doctor_profile_path, encoding="utf-8") as f:
            self._doctor_profile = json.load(f)
        with open(clinic_profile_path, encoding="utf-8") as f:
            self._clinic_profile = json.load(f)
        self._treatment_dict = load_treatment_dict(treatment_dict_path)
        self._reverse_map = build_reverse_map(self._treatment_dict)

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        # Step 1: Leakage guard
        present = LEAKAGE_COLUMNS & set(df.columns)
        if present:
            raise ValueError(f"Post-visit leakage columns present in input: {present}")

        out = pd.DataFrame(index=df.index)

        # Step 2: appt_hour_bucket sentinel remap
        out["appt_hour_bucket"] = df["appt_hour_bucket"].replace(0, -1)

        # Step 3: Treatment mapper
        out["treatment_class"] = df["treatment"].apply(
            lambda x: map_treatment(x, self._treatment_dict, self._reverse_map)
        )
        unknown_rate = (out["treatment_class"] == "UNKNOWN").mean()
        if unknown_rate > 0.10:
            logging.warning(
                f"UNKNOWN treatment_class rate is {unknown_rate:.1%} — "
                "consider expanding treatment_dict.json"
            )
        out["composite_treatment_flag"] = (out["treatment_class"] == "COMPOSITE_FILL").astype(int)

        # Step 4: Tooth parser
        tooth_parsed = df["tooth_no"].apply(parse_tooth_no).apply(pd.Series)
        out["has_tooth_no"] = tooth_parsed["has_tooth_no"]
        out["tooth_count"] = tooth_parsed["tooth_count"]
        out["is_area_treatment"] = tooth_parsed["is_area_treatment"]

        # Step 5: Surface count (null = non-restorative = 0, not missing)
        out["surface_count"] = df["surfaces"].apply(
            lambda x: len(str(x).split(",")) if pd.notna(x) else 0
        )

        # Step 6: Doctor lookup
        def _doctor_lookup(row):
            if row["has_dentist_id"] == 0:
                stats = self._doctor_profile["__global__"]
            else:
                entry = self._doctor_profile.get(row.get("dentist_pseudo_id"))
                if entry and entry["case_count"] >= 30:
                    stats = entry
                else:
                    stats = self._doctor_profile["__global__"]
            return stats["doctor_median_duration"], stats["doctor_pct_long"]

        doctor_stats = df.apply(_doctor_lookup, axis=1, result_type="expand")
        out["doctor_median_duration"] = doctor_stats[0]
        out["doctor_pct_long"] = doctor_stats[1]

        # appointment_rank_in_day: 0 for has_dentist_id=0 rows (avoids extra missingness)
        rank_col = df.get("appointment_rank_in_day", pd.Series(0, index=df.index))
        out["appointment_rank_in_day"] = (
            rank_col.where(df["has_dentist_id"] == 1, 0).fillna(0).astype(int)
        )

        # Step 7: Clinic lookup
        def _clinic_lookup(clinic_id):
            entry = self._clinic_profile.get(clinic_id)
            if entry and entry["case_count"] >= 30:
                stats = entry
            else:
                stats = self._clinic_profile["__global__"]
            return stats["clinic_median_duration"], stats["clinic_pct_long"]

        clinic_stats = df["clinic_pseudo_id"].apply(
            lambda x: pd.Series(_clinic_lookup(x))
        )
        out["clinic_median_duration"] = clinic_stats[0]
        out["clinic_pct_long"] = clinic_stats[1]

        # Pass-through columns
        for col in ["total_amount", "has_notes", "appt_day_of_week", "is_first_case", "has_dentist_id"]:
            out[col] = df[col].values

        # Step 8: Target binning (ceiling strategy — never under-estimate)
        out["duration_class"] = df["scheduled_duration_min"].apply(_bin_duration)
        out["scheduled_duration_min"] = df["scheduled_duration_min"].values

        # Step 9: Enforce exact column allowlist
        final_cols = FEATURE_COLUMNS + ["scheduled_duration_min", "duration_class"]
        missing = set(final_cols) - set(out.columns)
        if missing:
            raise ValueError(f"BUG: transformer failed to produce columns: {missing}")

        return out[final_cols]
```

- [ ] **Step 6.4: Run tests to confirm they all pass**

```bash
python -m pytest tests/test_feature_transformer.py -v
```

Expected: `14 passed`

- [ ] **Step 6.5: Run the full test suite**

```bash
python -m pytest tests/ -v
```

Expected: `27 passed` (7 + 6 + 6 + 14 — all tasks combined)

- [ ] **Step 6.6: Commit**

```bash
git add src/features/feature_transformer.py tests/test_feature_transformer.py
git commit -m "feat: add FeatureTransformer with full 9-step pipeline (TDD)"
```

---

## Task 7: `feature_engineering.py` CLI Entrypoint

**Files:**
- Create: `feature_engineering.py`

- [ ] **Step 7.1: Implement the CLI script**

Create `feature_engineering.py` at the project root:
```python
"""
Feature engineering pipeline for DentTime.

Usage:
    python feature_engineering.py --input "Data Collection/data.csv" --output features/
"""

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from src.features.build_profiles import build_and_save
from src.features.feature_transformer import FeatureTransformer

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

ARTIFACTS_DIR = Path("src/features/artifacts")


def main():
    parser = argparse.ArgumentParser(description="DentTime feature engineering pipeline")
    parser.add_argument("--input", required=True, help="Path to anonymized data.csv")
    parser.add_argument("--output", required=True, help="Output directory for Parquet files")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    logging.info(f"Loading {args.input}")
    df = pd.read_csv(args.input)
    logging.info(f"Loaded {len(df):,} rows, {len(df.columns)} columns")

    # Time-based split — never random (prevents leakage)
    train_df = df[df["appt_year_month"] <= "2025-02"].copy()
    test_df = df[df["appt_year_month"] == "2025-04"].copy()
    logging.info(f"Train: {len(train_df):,} rows (≤ 2025-02)")
    logging.info(f"Test:  {len(test_df):,} rows (2025-04)")
    logging.info("Note: 2025-03 is absent from source data — documented gap")

    if len(train_df) == 0:
        raise ValueError("Train split is empty — check appt_year_month column and date range")
    if len(test_df) == 0:
        raise ValueError("Test split is empty — check appt_year_month column and date range")

    # Build profiles from train split only (no test data leaks in)
    logging.info("Building doctor and clinic profiles from train split only...")
    build_and_save(train_df, ARTIFACTS_DIR)
    logging.info(f"Profiles written to {ARTIFACTS_DIR}/")

    transformer = FeatureTransformer(
        doctor_profile_path=str(ARTIFACTS_DIR / "doctor_profile.json"),
        clinic_profile_path=str(ARTIFACTS_DIR / "clinic_profile.json"),
        treatment_dict_path=str(ARTIFACTS_DIR / "treatment_dict.json"),
    )

    logging.info("Transforming train split...")
    train_features = transformer.transform(train_df)
    train_out = output_dir / "features_train.parquet"
    train_features.to_parquet(train_out, index=False)
    logging.info(f"Train features: {len(train_features):,} rows → {train_out}")

    logging.info("Transforming test split...")
    test_features = transformer.transform(test_df)
    test_out = output_dir / "features_test.parquet"
    test_features.to_parquet(test_out, index=False)
    logging.info(f"Test features:  {len(test_features):,} rows → {test_out}")

    # Feature stats sidecar (train only — becomes drift monitoring baseline)
    stats = {}
    for col in train_features.columns:
        col_stats: dict = {"null_rate": float(train_features[col].isna().mean())}
        if train_features[col].dtype == object:
            top5 = train_features[col].value_counts().head(5).to_dict()
            col_stats["top5"] = {str(k): int(v) for k, v in top5.items()}
            if col == "treatment_class":
                col_stats["unknown_rate"] = float(
                    (train_features[col] == "UNKNOWN").sum() / len(train_features)
                )
        else:
            col_stats["mean"] = float(train_features[col].mean())
            if col == "appt_hour_bucket":
                col_stats["pct_sentinel"] = float(
                    (train_features[col] == -1).sum() / len(train_features)
                )
        stats[col] = col_stats

    stats_path = output_dir / "feature_stats.json"
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
    logging.info(f"Feature stats written to {stats_path}")

    # Duration class distribution
    dist = train_features["duration_class"].value_counts().sort_index().to_dict()
    logging.info(f"Duration class distribution (train): {dist}")
    logging.info("Done.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7.2: Run a smoke test with the real data**

```bash
cd "/Users/sunsun/Library/CloudStorage/GoogleDrive-chantapat.sun@gmail.com/My Drive/Study/Semester2/SE_for_ML/DentTime"
python feature_engineering.py --input "Data Collection/data.csv" --output features/
```

Expected output (approximate):
```
INFO: Loaded 255,000 rows, 20 columns
INFO: Train: ~200,000 rows (≤ 2025-02)
INFO: Test:  ~55,000 rows (2025-04)
INFO: Building doctor and clinic profiles from train split only...
INFO: Profiles written to src/features/artifacts/
INFO: Train features: ~200,000 rows → features/features_train.parquet
INFO: Test features:  ~55,000 rows  → features/features_test.parquet
INFO: Feature stats written to features/feature_stats.json
INFO: Duration class distribution (train): {15: ..., 30: ..., 45: ..., 60: ..., 90: ..., 105: ...}
INFO: Done.
```

Check UNKNOWN rate in the log — if it warns `> 10%`, expand `treatment_dict.json` with more aliases and re-run.

- [ ] **Step 7.3: Verify Parquet output is readable and correct shape**

```bash
python -c "
import pandas as pd
train = pd.read_parquet('features/features_train.parquet')
test  = pd.read_parquet('features/features_test.parquet')
print('Train shape:', train.shape)
print('Test shape: ', test.shape)
print('Train columns:', list(train.columns))
print('Null counts (train):\n', train.isnull().sum())
print('Duration class dist:\n', train['duration_class'].value_counts().sort_index())
"
```

Expected: 19 columns (17 features + `scheduled_duration_min` + `duration_class`), zero unexpected nulls, all duration classes present.

- [ ] **Step 7.4: Commit**

```bash
git add feature_engineering.py src/features/artifacts/doctor_profile.json src/features/artifacts/clinic_profile.json
git commit -m "feat: add feature_engineering CLI entrypoint and run initial profiles"
```

---

## Task 8: Final Checks + DVC Metadata

**Files:**
- Create: `features/.dvcignore` (optional — if DVC is set up)

- [ ] **Step 8.1: Run the complete test suite one final time**

```bash
python -m pytest tests/ -v --tb=short
```

Expected: `27 passed`, `0 failed`. If any fail, fix before proceeding.

- [ ] **Step 8.2: Check feature_stats.json for anomalies**

```bash
python -c "
import json
with open('features/feature_stats.json') as f:
    stats = json.load(f)
for col, s in stats.items():
    if s.get('null_rate', 0) > 0.05:
        print(f'WARNING high null rate: {col} = {s[\"null_rate\"]:.1%}')
    if col == 'treatment_class' and s.get('unknown_rate', 0) > 0.10:
        print(f'WARNING UNKNOWN rate too high: {s[\"unknown_rate\"]:.1%} — expand treatment_dict.json')
print('Stats check complete')
"
```

- [ ] **Step 8.3: Document known gap in a metadata file**

Create `features/metadata.json`:
```json
{
  "date_range": ["2025-01", "2025-02", "2025-04"],
  "known_gap": "2025-03 absent from source data",
  "label_column": "duration_class",
  "label_classes": [15, 30, 45, 60, 90, 105],
  "binning_strategy": "ceiling — maps to nearest standard class >= actual duration",
  "feature_count": 17,
  "leakage_excluded": ["checkin_delay_min", "tx_record_offset_min", "receipt_offset_min"],
  "zero_variance_excluded": ["treatment_recorded"],
  "near_zero_variance_excluded": ["receipt_issued"],
  "pk_excluded": ["appointment_pseudo_id"]
}
```

- [ ] **Step 8.4: Final commit**

```bash
git add features/metadata.json tests/ src/
git commit -m "feat: complete feature engineering pipeline — profiles, transformer, CLI, tests"
```

---

## Self-Review Against Spec

| Spec Section | Task(s) | Status |
|---|---|---|
| Phase A: leakage guard + shared transformer | Task 6 (FeatureTransformer) | ✅ |
| Phase A: target binning (ceiling) | Task 6 (`_bin_duration`) | ✅ |
| Phase A: appt_hour_bucket=0 → -1 | Task 6 (Step 2 of pipeline) | ✅ |
| Phase B: treatment_dict.json (14 categories) | Task 3 | ✅ |
| Phase B: regex prefix + fuzzy match | Task 4 (treatment_mapper) | ✅ |
| Phase B: tooth_no parser (4 formats) | Task 2 (tooth_parser) | ✅ |
| Phase B: surface_count, composite_flag | Task 6 (Steps 4–5) | ✅ |
| Phase C: clinic profile, cold-start=30 | Task 5 (build_profiles) | ✅ |
| Phase D: doctor profile, has_dentist_id=0 first-class | Task 5 + Task 6 | ✅ |
| Phase D: appointment_rank_in_day 0-fill | Task 6 (Step 6) | ✅ |
| Phase E: 17-column allowlist enforced | Task 6 (Step 9) | ✅ |
| Phase E: feature_stats.json sidecar | Task 7 | ✅ |
| Phase E: CLI with --input/--output | Task 7 | ✅ |
| Phase E: DVC metadata (date range, gap, row count) | Task 8 | ✅ |
| Testing: unit tests tooth_parser (7 cases) | Task 2 | ✅ |
| Testing: unit tests treatment_mapper (6 cases) | Task 4 | ✅ |
| Testing: integration test transformer (14 cases) | Task 6 | ✅ |
| Key risk: training-serving skew | Shared FeatureTransformer class | ✅ |
| Key risk: leakage in profile stats | build_and_save receives train_df only | ✅ |
| Key risk: fuzzy threshold named constant | FUZZY_MATCH_THRESHOLD = 85 in treatment_mapper.py | ✅ |
