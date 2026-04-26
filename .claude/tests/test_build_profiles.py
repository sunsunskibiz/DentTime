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
