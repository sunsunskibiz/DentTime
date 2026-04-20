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
    from src.features.feature_transformer import build_treatment_encoding
    encoding = build_treatment_encoding(SAMPLE_TREATMENT_DICT)
    (tmp_path / "treatment_encoding.json").write_text(
        json.dumps(encoding), encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def transformer(artifact_dir):
    return FeatureTransformer(
        doctor_profile_path=str(artifact_dir / "doctor_profile.json"),
        clinic_profile_path=str(artifact_dir / "clinic_profile.json"),
        treatment_dict_path=str(artifact_dir / "treatment_dict.json"),
        treatment_encoding_path=str(artifact_dir / "treatment_encoding.json"),
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


def test_no_string_columns_in_output(transformer):
    df = _make_df([_base_row()])
    result = transformer.transform(df)
    string_cols = [c for c in result.columns if result[c].dtype == object]
    assert string_cols == [], f"String columns found: {string_cols}"


def test_treatment_class_dtype_is_int64(transformer):
    df = _make_df([_base_row()])
    result = transformer.transform(df)
    assert str(result["treatment_class"].dtype) == "int64"


def test_treatment_class_value_in_valid_range(transformer):
    # SAMPLE_TREATMENT_DICT has 4 classes → valid integers are 0, 1, 2, 3
    df = _make_df([_base_row(treatment="ขูดหินปูน")])  # SCALING → 2
    result = transformer.transform(df)
    assert result["treatment_class"].iloc[0] in {0, 1, 2, 3}


def test_treatment_class_encoding_is_stable(transformer):
    df = _make_df([_base_row(treatment="ขูดหินปูน")])
    result1 = transformer.transform(df.copy())
    result2 = transformer.transform(df.copy())
    assert result1["treatment_class"].iloc[0] == result2["treatment_class"].iloc[0]
