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
