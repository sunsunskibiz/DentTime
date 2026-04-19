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
